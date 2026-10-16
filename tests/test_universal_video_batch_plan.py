from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import random
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from universal_video import batch_plan
from universal_video.batch_plan import BatchPlanError, build_batch_plan, main, read_snapshot

SOURCE = "sourceFolder000001"
OUTPUT = "outputFolder000001"
WORK = "workFolder00000001"
OPTIONS = {"source_folder_id": SOURCE, "output_folder_id": OUTPUT, "work_folder_id": WORK}


def item(number=1, **changes):
    raw = {
        "id": f"driveVideo{number:08d}", "name": f"Lesson {number}.mp4",
        "mimeType": "video/mp4", "parents": [SOURCE], "size": "2000000",
        "version": "17", "modifiedTime": "2026-10-10T12:30:00.123Z",
        "md5Checksum": f"{number:032x}", "trashed": False,
    }
    raw.update(changes)
    return raw


def plan(rows=None, **changes):
    return build_batch_plan([item()] if rows is None else rows, **{**OPTIONS, **changes})


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def cli_args(path):
    return [str(path), "--source-folder-id", SOURCE, "--output-folder-id", OUTPUT,
            "--work-folder-id", WORK]


def test_1001_sources_form_two_bounded_fixed_subsets_without_loss():
    result = plan([item(i) for i in range(1001)])
    batches = result["batches"]
    assert [b["expected_count"] for b in batches] == [1000, 1]
    assert [b["sequence"] for b in batches] == [1, 2]
    ids = [file_id for b in batches for file_id in b["file_ids"]]
    assert ids == sorted(item(i)["id"] for i in range(1001))
    assert len(set(ids)) == result["expected_count"] == 1001
    assert result["total_size_bytes"] == 1001 * 2_000_000
    assert result["execution_allowed"] is False
    assert result["database_persistence_allowed"] is False
    assert result["canonical_promotion_allowed"] is False
    assert all(b["execution_allowed"] is False for b in batches)


@pytest.mark.parametrize("bound", [1, 7, 1000])
def test_permutation_and_repeat_are_identical_and_do_not_mutate_snapshot(bound):
    rows = [item(i) for i in range(1001)]
    before = copy.deepcopy(rows)
    expected = plan(rows, max_jobs_per_batch=bound)
    assert plan(rows, max_jobs_per_batch=bound) == expected
    assert rows == before
    random.Random(20261011).shuffle(rows)
    assert plan(rows, max_jobs_per_batch=bound) == expected
    assert plan({"files": rows, "incompleteSearch": False}, max_jobs_per_batch=bound) == expected
    assert all(1 <= len(b["file_ids"]) <= bound for b in expected["batches"])


def test_digests_bind_inventory_destinations_bounds_and_exact_subsets():
    result = plan([item(1), item(2), item(3)], max_jobs_per_batch=2)
    assert result["inventory_sha256"] == digest({
        "schema": "universal-video-inventory-v1", "source_folder_id": SOURCE, "files": result["files"],
    })
    assert result["plan_sha256"] == digest({k: v for k, v in result.items() if k != "plan_sha256"})
    for batch in result["batches"]:
        subset = [f for f in result["files"] if f["file_id"] in batch["file_ids"]]
        assert batch["subset_sha256"] == digest(subset)
    for change in ({"max_jobs_per_batch": 1}, {"output_folder_id": "otherOutput000001"},
                   {"work_folder_id": "otherWork00000001"}):
        changed = plan([item(1), item(2), item(3)], **{"max_jobs_per_batch": 2, **change})
        assert changed["inventory_sha256"] == result["inventory_sha256"]
        assert changed["plan_sha256"] != result["plan_sha256"]


def test_rename_keeps_identity_fingerprint_and_subset_membership():
    original = plan([item(1), item(2)], max_jobs_per_batch=1)
    renamed = plan([item(1, name="ZZZ renamed.mp4"), item(2)], max_jobs_per_batch=1)
    for key in ("file_id", "source_identity", "source_fingerprint"):
        assert original["files"][0][key] == renamed["files"][0][key]
    assert [b["file_ids"] for b in original["batches"]] == [b["file_ids"] for b in renamed["batches"]]
    assert original["inventory_sha256"] != renamed["inventory_sha256"]
    assert original["plan_sha256"] != renamed["plan_sha256"]


@pytest.mark.parametrize("changes", [
    {"version": "18"}, {"modifiedTime": "2026-10-11T12:30:00Z"},
    {"md5Checksum": "f" * 32}, {"sha256Checksum": "a" * 64}, {"size": "3000000"},
])
def test_source_revision_changes_fingerprint_but_not_identity(changes):
    first = plan()["files"][0]
    changed = plan([item(**changes)])["files"][0]
    assert first["source_identity"] == changed["source_identity"]
    assert first["source_fingerprint"] != changed["source_fingerprint"]


def test_metadata_and_all_checksums_are_retained():
    raw = item(sha256Checksum="A" * 64, sha1Checksum="B" * 40)
    result = plan([raw])["files"][0]
    assert result["file_id"] == raw["id"]
    assert result["parent"] == raw["parents"][0]
    assert result["version"] == raw["version"]
    assert result["modifiedTime"] == raw["modifiedTime"]
    assert result["checksum"] == "sha256:" + "a" * 64
    assert result["checksums"] == {"sha256Checksum": "a" * 64, "sha1Checksum": "b" * 40,
                                   "md5Checksum": raw["md5Checksum"]}


@pytest.mark.parametrize("explicit_nulls", [False, True])
def test_unknown_duration_cost_and_checksum_remain_null(explicit_nulls):
    raw = item()
    raw.pop("md5Checksum")
    if explicit_nulls:
        raw.update(md5Checksum=None, videoMediaMetadata={"durationMillis": None}, estimated_cost_usd=None)
    result = plan([raw])["files"][0]
    assert result["duration_ms"] is result["estimated_cost_usd"] is result["checksum"] is None
    assert result["checksums"] == {}
    assert result["source_fingerprint"]


def test_supplied_duration_and_cost_are_preserved_without_derivation():
    result = plan([item(videoMediaMetadata={"durationMillis": "123456"}, estimated_cost_usd=0.12)])
    assert result["files"][0]["duration_ms"] == 123456
    assert result["files"][0]["estimated_cost_usd"] == 0.12
    assert plan([item(estimated_cost_usd=0)])["files"][0]["estimated_cost_usd"] == 0


@pytest.mark.parametrize("snapshot", [[], {}, {"files": []}, None, "", [None], [1], {"files": {}},
    {"files": [item()], "nextPageToken": "more"}, {"files": [item()], "incompleteSearch": True},
    {"files": [item()], "incompleteSearch": None}])
def test_empty_invalid_and_incomplete_snapshots_are_errors(snapshot):
    with pytest.raises(BatchPlanError):
        plan(snapshot) if snapshot is not None else build_batch_plan(snapshot, **OPTIONS)


def test_duplicate_ids_are_errors_even_with_different_name_or_revision():
    for duplicate in (item(), item(name="Other.mp4"), item(version="18")):
        with pytest.raises(BatchPlanError, match="duplicate Drive file id"):
            plan([item(), duplicate])


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", "bad/id"), ("name", ""), ("name", None),
    ("parents", []), ("parents", [OUTPUT]), ("parents", [SOURCE, OUTPUT]), ("parents", SOURCE),
    ("trashed", True), ("trashed", None), ("trashed", 0),
    ("mimeType", "application/pdf"), ("name", "AI_PART_1.mp4"),
    ("version", None), ("version", ""), ("version", "latest"), ("version", 17),
    ("version", "0"), ("version", "18446744073709551616"),
    ("modifiedTime", None), ("modifiedTime", ""), ("modifiedTime", "unknown"),
    ("modifiedTime", "2026-02-30T10:00:00Z"), ("modifiedTime", "2026-10-10T10:00:00"),
    ("modifiedTime", "2026-10-10T10:00:00+01:99"),
    ("size", True), ("size", 2_000_000.5), ("size", "NaN"), ("size", 0),
    ("size", 64 * 1024**3 + 1), ("size", -2_000_000),
    ("md5Checksum", ""), ("md5Checksum", "g" * 32), ("md5Checksum", "a" * 31),
    ("sha1Checksum", False), ("sha256Checksum", "a" * 63),
    ("videoMediaMetadata", []), ("videoMediaMetadata", {"durationMillis": "NaN"}),
    ("videoMediaMetadata", {"durationMillis": False}), ("videoMediaMetadata", {"durationMillis": -1}),
    ("estimated_cost_usd", -1), ("estimated_cost_usd", True), ("estimated_cost_usd", "0.1"),
])
def test_invalid_rows_fail_explicitly_instead_of_being_dropped(field, value):
    with pytest.raises(BatchPlanError):
        plan([item(1), item(2, **{field: value})])


@pytest.mark.parametrize("field", ["version", "modifiedTime", "trashed", "parents", "id"])
def test_missing_required_source_evidence_fails_closed(field):
    raw = item()
    raw.pop(field)
    with pytest.raises(BatchPlanError):
        plan([raw])


def test_invalid_secondary_checksum_is_not_hidden_by_valid_stronger_checksum():
    with pytest.raises(BatchPlanError, match="md5Checksum"):
        plan([item(sha256Checksum="a" * 64, md5Checksum="bad")])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("field", ["size", "estimated_cost_usd", "unusedMetadata"])
def test_nonfinite_metadata_is_rejected_even_in_unused_fields(field, value):
    with pytest.raises(BatchPlanError, match="finite JSON"):
        plan([item(**{field: value})])


@pytest.mark.parametrize("value", [0, -1, 1001, True, False, 1.5, "1000", None, float("nan")])
def test_invalid_batch_bounds_fail(value):
    with pytest.raises(BatchPlanError, match="max_jobs_per_batch"):
        plan(max_jobs_per_batch=value)


@pytest.mark.parametrize("field", ["output_folder_id", "work_folder_id"])
def test_source_destination_folder_collisions_fail(field):
    with pytest.raises(BatchPlanError, match="source/output collision"):
        plan(**{field: SOURCE})


@pytest.mark.parametrize("file_id", [SOURCE, OUTPUT, WORK])
def test_source_file_cannot_be_a_source_or_destination_folder(file_id):
    with pytest.raises(BatchPlanError, match="collision"):
        plan([item(id=file_id)])


@pytest.mark.parametrize("field", list(OPTIONS))
def test_invalid_folder_ids_fail(field):
    with pytest.raises(BatchPlanError, match="invalid"):
        plan(**{field: " "})


@pytest.mark.parametrize("raw", [b"", b"{", b"\xff", b'{"files":[],"files":[]}',
    b'{"x": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}', b'{"files":[{"id":1,"id":2}]}'])
def test_local_loader_rejects_invalid_json_and_duplicate_keys(tmp_path, raw):
    path = tmp_path / "snapshot.json"
    path.write_bytes(raw)
    with pytest.raises(BatchPlanError):
        read_snapshot(path)


def test_local_loader_rejects_missing_directory_and_oversize(tmp_path, monkeypatch):
    with pytest.raises(BatchPlanError):
        read_snapshot(tmp_path / "missing.json")
    with pytest.raises(BatchPlanError, match="regular file"):
        read_snapshot(tmp_path)
    path = tmp_path / "snapshot.json"
    path.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(batch_plan, "MAX_SNAPSHOT_BYTES", 4)
    with pytest.raises(BatchPlanError, match="bounded size"):
        read_snapshot(path)


def test_cli_repeat_is_byte_identical_and_leaves_input_unchanged(tmp_path, capsys):
    path = tmp_path / "snapshot.json"
    raw = json.dumps({"files": [item(2), item(1)]}).encode()
    path.write_bytes(raw)
    assert main(cli_args(path)) == 0
    first = capsys.readouterr().out
    assert main(cli_args(path)) == 0
    assert capsys.readouterr().out == first
    assert json.loads(first) == plan([item(1), item(2)])
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


def test_cli_invalid_snapshot_returns_only_execution_disabled_error(tmp_path, capsys):
    path = tmp_path / "snapshot.json"
    path.write_text("[]", encoding="utf-8")
    assert main(cli_args(path)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["execution_allowed"] is False
    assert "non-empty list" in result["error"]
    assert "batches" not in result


def test_cli_unicode_names_survive_legacy_stdout_without_changing_digests(tmp_path):
    rows = [item(name="\u0423\u0440\u043e\u043a \U0001f3ac.mp4")]
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    buffer = io.BytesIO()
    with io.TextIOWrapper(buffer, encoding="cp1251", write_through=True) as stream:
        with patch("sys.stdout", stream):
            assert main(cli_args(path)) == 0
        emitted = json.loads(buffer.getvalue().decode("ascii"))
    assert emitted == plan(rows)
    assert emitted["files"][0]["name"] == rows[0]["name"]
    assert emitted["plan_sha256"] == digest({k: v for k, v in emitted.items() if k != "plan_sha256"})


@pytest.mark.parametrize("raw", [
    pytest.param('{"size":' + "9" * 5000 + "}", id="oversized-integer"),
    pytest.param("[" * 1100 + "0" + "]" * 1100, id="excessive-nesting"),
])
def test_cli_extreme_json_inputs_return_explicit_error_json(tmp_path, capsys, raw):
    path = tmp_path / "snapshot.json"
    path.write_text(raw, encoding="utf-8")
    assert main(cli_args(path)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["execution_allowed"] is False
    # Decoder depth limits vary by Python build. Both decoder rejection and
    # subsequent metadata validation must produce a structured, closed result.
    assert result["schema"] == "universal-video-batch-plan-error-v1"
    assert result["error"]
    assert "batches" not in result


def test_existing_queue_limit_still_rejects_1001_sources():
    from universal_video import video_queue

    assert video_queue.MAX_FILES == batch_plan.MAX_JOBS_PER_BATCH == 1000
    with pytest.raises(video_queue.VideoQueueError, match="video count outside bounded range"):
        video_queue.normalize_drive_inventory(SOURCE, [item(i) for i in range(1001)])


def test_import_library_and_cli_never_download_enqueue_claim_start_upload_or_write_db(tmp_path, capsys):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps([item()]), encoding="utf-8")
    targets = [
        "universal_video.drive_adapter.access_token", "universal_video.drive_adapter.file_metadata",
        "universal_video.drive_adapter.list_folder_files", "universal_video.drive_adapter.download_file",
        "universal_video.video_queue.enqueue_drive_request", "universal_video.video_queue.enqueue_manifest",
        "universal_video.video_queue.claim_job", "universal_video.video_queue.database_url_from_env",
        "ops.ibm_vpc_power.create_action", "universal_video.drive_results.publish_result",
        "universal_video.drive_results._upload_or_verify_file", "psycopg.connect",
        "requests.sessions.Session.request", "urllib.request.urlopen", "socket.create_connection",
        "subprocess.Popen",
    ]
    with ExitStack() as stack:
        spies = [stack.enter_context(patch(target, side_effect=AssertionError(target))) for target in targets]
        # Targets are patched before re-import, covering import-time service calls.
        spec = importlib.util.spec_from_file_location("offline_batch_plan_probe", batch_plan.__file__)
        fresh_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh_module)
        assert fresh_module.build_batch_plan([item()], **OPTIONS)["execution_allowed"] is False
        assert fresh_module.main(cli_args(path)) == 0
        path.write_text("[]", encoding="utf-8")
        assert fresh_module.main(cli_args(path)) == 1
        for spy in spies:
            spy.assert_not_called()
    assert len(capsys.readouterr().out.splitlines()) == 2
