from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from contextlib import ExitStack
from unittest.mock import Mock, patch

import pytest

from universal_video import batch_plan, capture_plan, metadata_inventory
from universal_video.capture_plan import CapturePlanError, build_capture_plan, main


SOURCE, OUTPUT, WORK = "sourceFolder000001", "outputFolder000001", "workFolder00000001"
OPTIONS = dict(source_folder_id=SOURCE, output_folder_id=OUTPUT, work_folder_id=WORK)


@pytest.fixture(autouse=True)
def no_live_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected live network or credential call")
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("universal_video.drive_adapter.access_token", forbidden)


def item(number=1, **changes):
    return {"id": f"privateVideo{number:08d}", "name": f"Private lesson {number}.mp4",
            "mimeType": "video/mp4", "parents": [SOURCE], "trashed": False,
            "size": "2000000", "version": "17", "modifiedTime": "2026-10-10T12:30:00Z",
            "md5Checksum": None, "videoMediaMetadata": {"durationMillis": None}, **changes}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def fixture(rows=None, *, page_size=100, selected=None, excluded=None):
    rows = [item()] if rows is None else rows
    folder = item(id=SOURCE, mimeType=metadata_inventory.FOLDER_MIME)
    pages = [{"files": rows[i:i + page_size], "incompleteSearch": False}
             for i in range(0, len(rows), page_size)] or [{"files": [], "incompleteSearch": False}]
    for i, page in enumerate(pages[:-1]):
        page["nextPageToken"] = f"private-page-token-{i}"
    digest = hashlib.sha256(encoded({"folder": folder, "files": sorted(rows, key=lambda r: r["id"])})).hexdigest()
    raw = {"schema": "universal-video-raw-metadata-v1", "folder": folder, "pages": pages,
           "inventory_sha256": digest, "execution_allowed": False}
    receipt = {"schema": "universal-video-metadata-receipt-v1", "status": "COMPLETE", "code": "OK",
               "inventory_sha256": digest, "execution_allowed": False,
               "counts": {"get_requests": 1 + len(pages), "pages_received": len(pages),
                   "items_received": len(rows), "video_items": sum(r["mimeType"].startswith("video/") for r in rows),
                   "trashed_items": sum(r["trashed"] for r in rows),
                   "response_bytes": len(encoded(folder)) + sum(len(encoded(p)) for p in pages)}}
    selection = {"schema": "universal-video-selection-v1", "inventory_sha256": digest,
                 "selected_file_ids": [r["id"] for r in rows] if selected is None else selected,
                 "excluded_files": [] if excluded is None else excluded}
    return raw, receipt, selection


def plan(data=None, **changes):
    return build_capture_plan(*(fixture() if data is None else data), **{**OPTIONS, **changes})


def excluded(file_id, reason):
    return {"file_id": file_id, "reason": reason}


def test_full_accounting_delegates_once_and_preserves_existing_fingerprints():
    rows = [item(), item(2, mimeType=metadata_inventory.FOLDER_MIME),
            item(3, mimeType="application/vnd.google-apps.shortcut"), item(4, trashed=True),
            item(5, name="AI_PART_5.mp4"), item(6)]
    decisions = [excluded(rows[i]["id"], reason) for i, reason in
                 [(1, "NON_VIDEO"), (2, "NON_VIDEO"), (3, "TRASHED"),
                  (4, "DERIVED_VIDEO"), (5, "DEFERRED")]]
    data = fixture(rows, selected=[rows[0]["id"]], excluded=decisions)
    before = copy.deepcopy(data)
    with patch.object(batch_plan, "build_batch_plan", wraps=batch_plan.build_batch_plan) as planner:
        result = plan(data)
        planner.assert_called_once_with([rows[0]], **OPTIONS, max_jobs_per_batch=1000)
    assert result["plan"] == batch_plan.build_batch_plan([rows[0]], **OPTIONS)
    assert data == before
    assert result["plan"]["files"][0]["duration_ms"] is None
    assert result["plan"]["files"][0]["estimated_cost_usd"] is None
    assert result["receipt"]["counts"] == {"captured_items": 6, "selected_items": 1,
        "excluded_items": 5, "batches": 1, "exclusions_by_reason":
        {"TRASHED": 1, "NON_VIDEO": 2, "DERIVED_VIDEO": 1, "DEFERRED": 1}}
    assert result["execution_allowed"] is result["receipt"]["execution_allowed"] is False


def test_repeats_permutation_and_repagination_have_identical_review_and_plan():
    rows = [item(i) for i in range(19)]
    args = dict(selected=[r["id"] for r in rows[:16]],
                excluded=[excluded(r["id"], "DEFERRED") for r in rows[16:]])
    expected = plan(fixture(rows, **args), max_jobs_per_batch=7)
    assert [b["expected_count"] for b in expected["plan"]["batches"]] == [7, 7, 2]
    other = fixture(rows[::-1], page_size=5, selected=args["selected"][::-1], excluded=args["excluded"][::-1])
    assert plan(other, max_jobs_per_batch=7) == expected
    assert plan(other, max_jobs_per_batch=7) == expected


def test_exact_500_capture_items_split_without_raising_existing_limits():
    result = plan(fixture([item(i) for i in range(500)]), max_jobs_per_batch=200)
    assert [b["expected_count"] for b in result["plan"]["batches"]] == [200, 200, 100]
    assert metadata_inventory.MAX_ITEMS == 500 and batch_plan.MAX_JOBS_PER_BATCH == 1000


def test_rename_and_revision_semantics_are_the_existing_planners():
    original = plan()["plan"]["files"][0]
    rename = plan(fixture([item(name="Private renamed.mp4")]))["plan"]["files"][0]
    revision = plan(fixture([item(version="18")]))["plan"]["files"][0]
    assert original["source_identity"] == rename["source_identity"] == revision["source_identity"]
    assert original["source_fingerprint"] == rename["source_fingerprint"]
    assert original["source_fingerprint"] != revision["source_fingerprint"]


def test_capture_tamper_and_recomputed_embedded_digest_cannot_replace_receipt():
    raw, receipt, selection = fixture()
    raw["pages"][0]["files"][0]["name"] = "private altered.mp4"
    with pytest.raises(CapturePlanError, match="CAPTURE_DIGEST_MISMATCH"):
        plan((raw, receipt, selection))
    raw["inventory_sha256"] = hashlib.sha256(encoded({"folder": raw["folder"],
        "files": raw["pages"][0]["files"]})).hexdigest()
    selection["inventory_sha256"] = raw["inventory_sha256"]
    with pytest.raises(CapturePlanError, match="CAPTURE_DIGEST_MISMATCH"):
        plan((raw, receipt, selection))


@pytest.mark.parametrize("target", ["snapshot", "receipt", "selection"])
def test_each_digest_must_match(target):
    data = fixture()
    data[{"snapshot": 0, "receipt": 1, "selection": 2}[target]]["inventory_sha256"] = "0" * 64
    with pytest.raises(CapturePlanError, match="DIGEST_MISMATCH"):
        plan(data)


@pytest.mark.parametrize("changes", [dict(parents=[OUTPUT]), dict(version=None),
    dict(modifiedTime="invalid"), dict(md5Checksum="bad"), dict(size="NaN"),
    dict(videoMediaMetadata={"durationMillis": -1})])
def test_bad_metadata_cannot_be_hidden_in_exclusions(changes):
    rows = [item(), item(2, **changes)]
    with pytest.raises(CapturePlanError):
        plan(fixture(rows, selected=[rows[0]["id"]], excluded=[excluded(rows[1]["id"], "DEFERRED")]))


def test_duplicate_ids_across_pages_fail_even_if_excluded():
    data = fixture([item(), item(2), item(2, version="18")], page_size=1,
                   selected=[item()["id"]], excluded=[excluded(item(2)["id"], "DEFERRED")])
    with pytest.raises(CapturePlanError, match="DUPLICATE_ID"):
        plan(data)


@pytest.mark.parametrize("mode", ["missing", "true", "null", "wrong_pages", "no_pages",
    "missing_token", "repeated_token", "unresolved_token", "oversize_page", "six_pages"])
def test_incomplete_or_invalid_page_envelopes_fail(mode):
    raw, receipt, selection = fixture([item(1), item(2), item(3)], page_size=1)
    if mode == "missing": del raw["pages"][0]["incompleteSearch"]
    elif mode in ("true", "null"): raw["pages"][0]["incompleteSearch"] = True if mode == "true" else None
    elif mode == "wrong_pages": raw["pages"] = {}
    elif mode == "no_pages": raw["pages"] = []
    elif mode == "missing_token": del raw["pages"][0]["nextPageToken"]
    elif mode == "repeated_token": raw["pages"][1]["nextPageToken"] = raw["pages"][0]["nextPageToken"]
    elif mode == "unresolved_token": raw["pages"][-1]["nextPageToken"] = "private-token"
    elif mode == "oversize_page": raw["pages"][0]["files"] *= 101
    else: raw["pages"] *= 2
    with pytest.raises(CapturePlanError):
        plan((raw, receipt, selection))


@pytest.mark.parametrize("field", list(metadata_inventory.COUNT_KEYS))
def test_receipt_counts_mismatch_fails(field):
    data = fixture()
    data[1]["counts"][field] = 0 if field == "response_bytes" else 9
    with pytest.raises(CapturePlanError, match="RECEIPT_COUNTS_MISMATCH"):
        plan(data)


@pytest.mark.parametrize("status", ["BLOCKED", "ERROR", "unknown", None, [], {}])
def test_noncomplete_and_malformed_receipts_fail_without_dependency_tracebacks(status):
    data = fixture()
    data[1]["status"] = status
    if status in ("BLOCKED", "ERROR"):
        data[1].update(code="TIME_LIMIT", inventory_sha256=None)
    with pytest.raises(CapturePlanError):
        plan(data)


@pytest.mark.parametrize("mode", ["missing", "empty", "duplicate_selected", "duplicate_excluded",
    "overlap", "omitted", "unknown", "missing_reason", "wrong_reason", "extra_field"])
def test_explicit_exhaustive_disjoint_selection_required(mode):
    raw, receipt, selection = fixture([item(), item(2)], selected=[item()["id"]],
                                      excluded=[excluded(item(2)["id"], "DEFERRED")])
    if mode == "missing": selection = None
    elif mode == "empty": selection["selected_file_ids"] = []
    elif mode == "duplicate_selected": selection["selected_file_ids"] *= 2
    elif mode == "duplicate_excluded": selection["excluded_files"] *= 2
    elif mode == "overlap": selection["excluded_files"][0]["file_id"] = item()["id"]
    elif mode == "omitted": selection["excluded_files"] = []
    elif mode == "unknown": selection["selected_file_ids"] = ["unknownVideo000001"]
    elif mode == "missing_reason": del selection["excluded_files"][0]["reason"]
    elif mode == "wrong_reason": selection["excluded_files"][0]["reason"] = "NON_VIDEO"
    else: selection["automatic_selection"] = True
    with pytest.raises(CapturePlanError):
        plan((raw, receipt, selection))


@pytest.mark.parametrize("changes", [dict(trashed=True), dict(mimeType="application/pdf"),
                                    dict(name="AI_PART_1.mp4"), dict(size="0")])
def test_selected_invalid_originals_are_rejected_by_existing_planner(changes):
    with pytest.raises(CapturePlanError, match="INVALID_PLAN_INPUT"):
        plan(fixture([item(**changes)]))


@pytest.mark.parametrize("bound", [0, 1001, True, "1", None])
def test_invalid_batch_bounds_fail(bound):
    with pytest.raises(CapturePlanError, match="INVALID_PLAN_INPUT"):
        plan(max_jobs_per_batch=bound)


def test_folder_or_excluded_item_destination_collision_fails():
    with pytest.raises(CapturePlanError, match="SOURCE_OUTPUT_COLLISION"):
        plan(output_folder_id=SOURCE)
    data = fixture([item(), item(2, id=OUTPUT)], selected=[item()["id"]],
                   excluded=[excluded(OUTPUT, "DEFERRED")])
    with pytest.raises(CapturePlanError, match="SOURCE_OUTPUT_COLLISION"):
        plan(data)


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), "\ud800"])
def test_nonfinite_and_invalid_unicode_even_in_unused_fields_fail(index, value):
    data = fixture()
    data[index]["unused"] = value
    with pytest.raises(CapturePlanError, match="INVALID_PLAN_INPUT"):
        plan(data)


def write_inputs(tmp_path, data):
    paths = [tmp_path / f"{name}.json" for name in ("capture", "receipt", "selection")]
    for path, value in zip(paths, data):
        path.write_bytes(encoded(value))
    return [str(path) for path in paths] + ["--source-folder-id", SOURCE,
        "--output-folder-id", OUTPUT, "--work-folder-id", WORK]


def test_cli_only_sanitized_receipts_is_repeatable_and_never_writes(tmp_path, capsys):
    data = fixture([item(1), item(2)], page_size=1)
    args = write_inputs(tmp_path, data)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert main(args) == 0
    first = capsys.readouterr().out
    assert json.loads(first) == plan(data)["receipt"]
    assert main(args) == 0 and capsys.readouterr().out == first
    for private in [SOURCE, OUTPUT, WORK, "privateVideo", "Private lesson", "private-page-token"]:
        assert private not in first
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    (tmp_path / "capture.json").write_bytes(b'{"files": [], "files": []}')
    assert main(args) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["code"] == "INVALID_LOCAL_JSON" and error["execution_allowed"] is False
    assert error["counts"] is error["digests"] is None


@pytest.mark.parametrize("extra", [["--max-jobs-per-batch", "private-input-marker"],
                                    ["--private-input-marker"], []])
def test_cli_argument_errors_are_fixed_receipts_without_raw_stderr(tmp_path, capsys, extra):
    args = write_inputs(tmp_path, fixture()) + extra if extra else []
    assert main(args) == 1
    output = capsys.readouterr()
    assert output.err == "" and "private-input-marker" not in output.out
    assert json.loads(output.out)["code"] == "INVALID_ARGUMENTS"


def test_empty_complete_capture_cannot_silently_create_a_plan():
    with pytest.raises(CapturePlanError, match="INVALID_SELECTION"):
        plan(fixture([]))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_schema_or_execution_permission_tampering_fails(index):
    data = fixture()
    data[index]["schema"] = "unrecognized"
    with pytest.raises(CapturePlanError):
        plan(data)
    if index != 2:
        data = fixture()
        data[index]["execution_allowed"] = True
        with pytest.raises(CapturePlanError):
            plan(data)


def test_real_collector_output_with_synthetic_transport_is_accepted():
    data = fixture([item(2), item(1)], page_size=1)
    replies = []
    for value in [data[0]["folder"], *data[0]["pages"]]:
        response = Mock(status_code=200)
        stream = io.BytesIO(encoded(value))
        response.raw.read.side_effect = lambda n, decode_content, s=stream: s.read(n)
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        replies.append(context)
    session = Mock()
    session.get.side_effect = replies
    raw, receipt = metadata_inventory._collect_metadata(SOURCE, token_provider=lambda: "synthetic",
        session=session, base_url="https://metadata.invalid", counts=dict.fromkeys(metadata_inventory.COUNT_KEYS, 0))
    assert plan((raw, receipt, data[2])) == plan(data)


def test_import_library_and_cli_never_call_network_credentials_db_queue_or_runtime(tmp_path, capsys):
    data = fixture()
    args = write_inputs(tmp_path, data)
    targets = ["universal_video.drive_adapter.access_token", "universal_video.drive_adapter.download_file",
        "universal_video.drive_adapter.file_metadata", "universal_video.drive_adapter.list_folder_files",
        "universal_video.metadata_inventory.collect_inventory", "universal_video.metadata_inventory._worker",
        "universal_video.metadata_inventory._collect_metadata", "universal_video.video_queue.enqueue_manifest",
        "universal_video.video_queue.enqueue_drive_request", "universal_video.video_queue.claim_job",
        "universal_video.video_queue.database_url_from_env", "ops.ibm_vpc_power.create_action",
        "universal_video.drive_results.publish_result", "universal_video.drive_results._upload_or_verify_file",
        "psycopg.connect", "requests.sessions.Session.request", "urllib.request.urlopen",
        "socket.create_connection", "subprocess.Popen", "multiprocessing.process.BaseProcess.start"]
    with ExitStack() as stack:
        spies = [stack.enter_context(patch(target, side_effect=AssertionError(target))) for target in targets]
        spec = importlib.util.spec_from_file_location("universal_video.offline_capture_probe", capture_plan.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.build_capture_plan(*data, **OPTIONS)["execution_allowed"] is False
        assert module.main(args) == 0
        (tmp_path / "selection.json").write_text("null", encoding="utf-8")
        assert module.main(args) == 1
        for spy in spies:
            spy.assert_not_called()
    assert len(capsys.readouterr().out.splitlines()) == 2
