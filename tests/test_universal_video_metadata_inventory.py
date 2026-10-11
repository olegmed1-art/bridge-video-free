from __future__ import annotations

import copy
import importlib.util
import io
import json
import multiprocessing
import os
import stat
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from universal_video import metadata_inventory as inventory

FOLDER = "testFolder00000001"
BASE = "https://metadata.invalid/drive/v3"


def item(number=1, **changes):
    value = {"id": f"testFile{number:010d}", "name": f"Private lesson {number}.mp4",
             "parents": [FOLDER], "mimeType": "video/mp4", "trashed": False,
             "version": "17", "modifiedTime": "2026-10-10T12:30:00.123Z",
             "size": "2000000", "md5Checksum": "a" * 32}
    value.update(changes)
    return value


def folder(**changes):
    return item(id=FOLDER, mimeType=inventory.FOLDER_MIME, **changes)


def page(rows=None, **changes):
    return {"files": [item()] if rows is None else rows, "incompleteSearch": False, **changes}


class Response:
    def __init__(self, payload, *, status=200, chunk_size=65536, hook=None):
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.raw = self
        self.offset = 0
        self.status_code = status
        self.chunk_size = chunk_size
        self.hook = hook
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, amount, *, decode_content):
        assert 0 < amount <= 65536 and decode_content is True
        if self.hook:
            self.hook()
        chunk = self.body[self.offset:self.offset + min(amount, self.chunk_size)]
        self.offset += len(chunk)
        return chunk


class Session:
    def __init__(self, payloads):
        self.responses = [p if isinstance(p, Response) else Response(p) for p in payloads]
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses[len(self.calls) - 1]


def collect(pages=None, *, folder_row=None, token_provider=None, clock=None):
    session = Session([folder() if folder_row is None else folder_row,
                       *([page()] if pages is None else pages)])
    counts = dict.fromkeys(inventory.COUNT_KEYS, 0)
    options = {} if clock is None else {"clock": clock}
    raw, receipt = inventory._collect_metadata(
        FOLDER, token_provider=token_provider or (lambda: "synthetic-token"),
        session=session, base_url=BASE, counts=counts, **options)
    return raw, receipt, session


@pytest.fixture(autouse=True)
def no_live_network_or_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Test attempted a real network/credential action")
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("universal_video.drive_adapter.access_token", forbidden)


def test_single_folder_native_capture_and_only_bounded_metadata_gets():
    rows = [item(videoMediaMetadata={"durationMillis": None}, sha256Checksum=None),
            item(2, mimeType=inventory.FOLDER_MIME),
            item(3, mimeType="application/vnd.google-apps.shortcut"),
            item(4, trashed=True)]
    raw, result, session = collect([page(rows)])
    assert raw["pages"] == [page(rows)]
    assert raw["folder"] == folder()
    assert "estimated_cost_usd" not in raw["pages"][0]["files"][0]
    assert result["status"] == "COMPLETE"
    assert result["execution_allowed"] is False
    assert result["counts"] == {"get_requests": 2, "pages_received": 1,
                                "items_received": 4, "video_items": 2, "trashed_items": 1,
                                "response_bytes": sum(len(r.body) for r in session.responses)}
    assert raw["inventory_sha256"] == result["inventory_sha256"]
    assert len(session.calls) == 2
    for url, options in session.calls:
        assert url in (BASE + "/files/" + FOLDER, BASE + "/files")
        assert options["stream"] is True
        assert options["allow_redirects"] is False
        assert 0 < options["timeout"] <= 30
        assert options["headers"] == {"Authorization": "Bearer synthetic-token"}
        assert "alt" not in options["params"]
    params = session.calls[1][1]["params"]
    assert params["q"] == f"'{FOLDER}' in parents"
    assert params["pageSize"] == 100 and params["corpora"] == "user"
    assert "version,modifiedTime,trashed" in params["fields"]
    assert params["fields"].startswith("nextPageToken,incompleteSearch,")
    assert all(r.closed for r in session.responses)
    public = json.dumps(result)
    for private in (FOLDER, rows[0]["id"], rows[0]["name"], "synthetic-token"):
        assert private not in public


def test_repeat_order_and_pagination_do_not_change_inventory_digest():
    rows = [item(i) for i in range(8)]
    before = copy.deepcopy(rows)
    first = collect([page(rows)])[0:2]
    assert collect([page(rows)])[0:2] == first
    raw, result, session = collect([page(rows[4:][::-1], nextPageToken="private-token"),
                                    page(rows[:4][::-1])])
    assert result["inventory_sha256"] == first[1]["inventory_sha256"]
    assert raw["pages"][0]["nextPageToken"] == "private-token"
    assert "private-token" not in json.dumps(result)
    assert session.calls[2][1]["params"]["pageToken"] == "private-token"
    assert rows == before
    assert collect([page([item(0, version="18"), *rows[1:]])])[1]["inventory_sha256"] != result["inventory_sha256"]


def test_shared_drive_query_is_scoped_to_observed_drive_not_all_drives():
    _, _, session = collect(folder_row=folder(driveId="sharedDrive000001"))
    params = session.calls[1][1]["params"]
    assert params["corpora"] == "drive"
    assert params["driveId"] == "sharedDrive000001"
    assert params["includeItemsFromAllDrives"] == params["supportsAllDrives"] == "true"


def test_empty_complete_inventory_is_distinct_from_unknown_completeness():
    _, receipt, _ = collect([page([])])
    assert receipt["status"] == "COMPLETE" and receipt["counts"]["items_received"] == 0


@pytest.mark.parametrize("value", [None, True, 0, "false", []])
def test_incomplete_or_unknown_search_is_blocked(value):
    with pytest.raises(inventory.InventoryError) as caught:
        collect([page(incompleteSearch=value)])
    assert (caught.value.status, caught.value.code) == ("BLOCKED", "UNKNOWN_COMPLETENESS")


def test_missing_completeness_is_blocked_on_any_page():
    with pytest.raises(inventory.InventoryError, match="UNKNOWN_COMPLETENESS"):
        collect([page([], nextPageToken="next"), {"files": []}])


def test_exact_500_items_and_six_gets_complete():
    pages = [page([item(j) for j in range(i * 100, (i + 1) * 100)],
                  **({"nextPageToken": str(i)} if i < 4 else {})) for i in range(5)]
    _, receipt, session = collect(pages)
    assert receipt["status"] == "COMPLETE"
    assert receipt["counts"]["items_received"] == 500
    assert len(session.calls) == 6


@pytest.mark.parametrize("rows_per_page,expected", [(100, "ITEM_LIMIT"), (1, "GET_LIMIT"), (0, "GET_LIMIT")])
def test_more_pages_stop_without_seventh_get(rows_per_page, expected):
    pages = [page([item(i * 100 + j) for j in range(rows_per_page)], nextPageToken=str(i))
             for i in range(5)]
    session = Session([folder(), *pages])
    counts = dict.fromkeys(inventory.COUNT_KEYS, 0)
    with pytest.raises(inventory.InventoryError, match=expected) as caught:
        inventory._collect_metadata(FOLDER, token_provider=lambda: "fake", session=session,
                                    base_url=BASE, counts=counts)
    assert caught.value.status == "BLOCKED"
    assert len(session.calls) == 6 and counts["items_received"] <= 500


@pytest.mark.parametrize("count,expected", [(101, "INVALID_PAGE_SIZE"), (501, "ITEM_LIMIT")])
def test_server_cannot_override_row_bounds(count, expected):
    with pytest.raises(inventory.InventoryError, match=expected):
        collect([page([item(i) for i in range(count)])])


@pytest.mark.parametrize("rows", [[item(), item()], [item(), item(name="different revision", version="18")]])
def test_duplicate_ids_are_errors_without_deduplication(rows):
    with pytest.raises(inventory.InventoryError, match="DUPLICATE_ID"):
        collect([page([rows[0]], nextPageToken="next"), page([rows[1]])])


@pytest.mark.parametrize("token", [False, 0, 1, [], {}, "   "])
def test_invalid_pagination_token_is_an_error(token):
    with pytest.raises(inventory.InventoryError, match="INVALID_PAGE_TOKEN"):
        collect([page([], nextPageToken=token)])


def test_repeated_pagination_token_stops():
    with pytest.raises(inventory.InventoryError, match="REPEATED_PAGE_TOKEN"):
        collect([page([], nextPageToken="same"), page([], nextPageToken="same")])


@pytest.mark.parametrize("change,expected", [
    ({"parents": []}, "WRONG_PARENT"), ({"parents": ["otherFolder000001"]}, "WRONG_PARENT"),
    ({"parents": [FOLDER, FOLDER]}, "WRONG_PARENT"), ({"id": FOLDER}, "WRONG_PARENT"),
    ({"id": "bad'query"}, "INVALID_ID"), ({"name": " "}, "INVALID_METADATA"),
    ({"mimeType": None}, "INVALID_METADATA"), ({"trashed": None}, "INVALID_METADATA"),
    ({"trashed": 0}, "INVALID_METADATA"), ({"version": None}, "UNRESOLVED_SOURCE_REVISION"),
    ({"version": 17}, "UNRESOLVED_SOURCE_REVISION"), ({"version": "0"}, "UNRESOLVED_SOURCE_REVISION"),
    ({"version": "017"}, "UNRESOLVED_SOURCE_REVISION"),
    ({"version": str(2**64)}, "UNRESOLVED_SOURCE_REVISION"),
    ({"modifiedTime": None}, "UNRESOLVED_SOURCE_REVISION"),
    ({"modifiedTime": "2026-10-10T12:30:00"}, "UNRESOLVED_SOURCE_REVISION"),
    ({"modifiedTime": "2026-02-30T12:30:00Z"}, "INVALID_SOURCE_REVISION"),
    ({"md5Checksum": "bad"}, "INVALID_CHECKSUM"), ({"sha1Checksum": 1}, "INVALID_CHECKSUM"),
    ({"sha256Checksum": "a" * 63}, "INVALID_CHECKSUM"), ({"size": -1}, "INVALID_SIZE"),
    ({"size": "-1"}, "INVALID_SIZE"), ({"size": True}, "INVALID_SIZE"),
    ({"videoMediaMetadata": []}, "INVALID_DURATION"),
    ({"videoMediaMetadata": {"durationMillis": "NaN"}}, "INVALID_DURATION"),
    ({"driveId": ""}, "INVALID_ID"),
])
def test_invalid_native_metadata_is_not_silently_skipped(change, expected):
    with pytest.raises(inventory.InventoryError, match=expected):
        collect([page([item(**change)])])


@pytest.mark.parametrize("rows", [None, {}, "", [None], [1], ["bad"]])
def test_missing_or_non_object_rows_are_explicit_errors(rows):
    with pytest.raises(inventory.InventoryError, match="INVALID_METADATA"):
        collect([{"files": rows, "incompleteSearch": False}])


@pytest.mark.parametrize("changes,expected", [
    ({"id": "otherFolder000001"}, "INVALID_FOLDER"),
    ({"mimeType": "video/mp4"}, "INVALID_FOLDER"),
    ({"trashed": True}, "FOLDER_TRASH_STATE"),
    ({"version": None}, "UNRESOLVED_SOURCE_REVISION"),
])
def test_folder_is_validated_before_listing(changes, expected):
    initial = folder()
    initial.update(changes)
    with pytest.raises(inventory.InventoryError, match=expected):
        collect([], folder_row=initial)


@pytest.mark.parametrize("payload", [b'{"files": [], "files": []}', b'{"unused":NaN}',
    b'{"unused":Infinity}', b'{"unused":1e1000}', b'{"unused":"\\ud800"}', b'not json',
    b'\xff', pytest.param(b'{"unused":' + b'9' * 5000 + b'}', id="huge-integer")])
def test_invalid_json_fails_closed_even_in_unused_metadata(payload):
    with pytest.raises(inventory.InventoryError, match="INVALID_JSON"):
        collect([payload])


def test_deep_json_is_rejected_by_decoder_or_metadata_validation():
    with pytest.raises(inventory.InventoryError, match="INVALID_JSON|INVALID_METADATA"):
        collect([b'[' * 1100 + b'0' + b']' * 1100])


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500, 503])
def test_http_failures_do_not_retry_or_follow_redirects(status):
    response = Response(b"private error body", status=status)
    with pytest.raises(inventory.InventoryError, match="HTTP_ERROR"):
        collect([response])
    assert response.closed


def test_byte_budget_is_cumulative_and_exact_boundary_blocks_when_eof_unproven(monkeypatch):
    payload = page([])
    size = len(Response(folder()).body) + len(Response(payload).body)
    monkeypatch.setattr(inventory, "MAX_BYTES", size + 1)
    assert collect([payload])[1]["status"] == "COMPLETE"
    monkeypatch.setattr(inventory, "MAX_BYTES", size)
    response = Response(payload, chunk_size=3)
    with pytest.raises(inventory.InventoryError, match="BYTE_LIMIT") as caught:
        collect([response])
    assert caught.value.status == "BLOCKED" and response.closed


def test_stream_is_stopped_before_reading_unbounded_body(monkeypatch):
    monkeypatch.setattr(inventory, "MAX_BYTES", len(Response(folder()).body) + 2)
    chunks = Mock()
    response = Response(b" " * 100000, hook=chunks)
    with pytest.raises(inventory.InventoryError, match="BYTE_LIMIT"):
        collect([response])
    assert chunks.call_count == 1 and response.offset == 2


def test_exhausted_folder_body_does_not_issue_a_listing_get(monkeypatch):
    session = Session([folder()])
    monkeypatch.setattr(inventory, "MAX_BYTES", len(session.responses[0].body))
    counts = dict.fromkeys(inventory.COUNT_KEYS, 0)
    with pytest.raises(inventory.InventoryError, match="BYTE_LIMIT"):
        inventory._collect_metadata(FOLDER, token_provider=lambda: "fake", session=session,
                                    base_url=BASE, counts=counts)
    assert len(session.calls) == counts["get_requests"] == 1
    assert counts["response_bytes"] == inventory.MAX_BYTES


def test_actual_http_response_reader_never_returns_bytes_beyond_budget(monkeypatch):
    from urllib3.response import HTTPResponse

    response = Response(b"unused")
    body = io.BytesIO(b" " * 100000)
    response.raw = HTTPResponse(body=body, preload_content=False)
    monkeypatch.setattr(inventory, "MAX_BYTES", len(Response(folder()).body) + 2)
    with pytest.raises(inventory.InventoryError, match="BYTE_LIMIT"):
        collect([response])
    assert body.tell() == 2


def test_authentication_and_streaming_are_in_elapsed_budget():
    now = [0]

    def auth():
        now[0] = 120
        return "fake"

    with pytest.raises(inventory.InventoryError, match="TIME_LIMIT"):
        collect(token_provider=auth, clock=lambda: now[0])
    now[0] = 0
    response = Response(page(), hook=lambda: now.__setitem__(0, 120))
    with pytest.raises(inventory.InventoryError, match="TIME_LIMIT"):
        collect([response], clock=lambda: now[0])
    assert response.closed


def _sleep_worker(folder_id, staging, sender):
    # Spawned by the deadline test; deliberately no adapter, OAuth or network.
    Path(staging).write_bytes(b"synthetic private partial")
    time.sleep(60)


def test_supervisor_kills_stalled_worker_and_removes_private_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "_private_directory", lambda path: tmp_path)
    monkeypatch.setattr(inventory, "_worker", _sleep_worker)
    monkeypatch.setattr(inventory, "MAX_SECONDS", 1)
    before = {p.pid for p in multiprocessing.active_children()}
    started = time.monotonic()
    result = inventory.collect_inventory(FOLDER, tmp_path)
    assert time.monotonic() - started < 8
    assert result["status"] == "BLOCKED" and result["code"] == "TIME_LIMIT"
    assert result["counts"] is None and result["inventory_sha256"] is None
    assert {p.pid for p in multiprocessing.active_children()} == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("error", [RuntimeError("private internal detail"), KeyboardInterrupt()])
def test_parent_exception_still_kills_worker_and_sanitizes_error(tmp_path, monkeypatch, error):
    monkeypatch.setattr(inventory, "_private_directory", lambda path: tmp_path)
    context = multiprocessing.get_context("spawn")
    real_process = context.Process
    started = []

    def process_factory(**kwargs):
        process = real_process(target=_sleep_worker, args=kwargs["args"])
        real_join = process.join
        joins = [0]

        def join(timeout=None):
            joins[0] += 1
            if joins[0] == 1:
                raise error
            return real_join(timeout)

        process.join = join
        started.append(process)
        return process

    monkeypatch.setattr(context, "Process", process_factory)
    monkeypatch.setattr(inventory.multiprocessing, "get_context", lambda mode: context)
    before = {p.pid for p in multiprocessing.active_children()}
    result = inventory.collect_inventory(FOLDER, tmp_path)
    assert result["status"] == "ERROR"
    assert result["code"] in {"CAPTURE_FAILED", "INTERRUPTED"}
    assert "private" not in json.dumps(result)
    assert started and {p.pid for p in multiprocessing.active_children()} == before
    assert list(tmp_path.iterdir()) == []


def _failing_worker(folder_id, staging, sender):
    from universal_video import drive_adapter

    def fail_auth():
        print("SYNTHETIC_PRIVATE_AUTH_DETAIL", flush=True)
        raise RuntimeError("SYNTHETIC_PRIVATE_AUTH_DETAIL")

    drive_adapter.access_token = fail_auth
    drive_adapter.requests.Session = lambda: _ContextSession([])
    inventory._worker(folder_id, staging, sender)


def test_real_worker_auth_error_does_not_expose_logs_or_exception(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(inventory, "_private_directory", lambda path: tmp_path)
    monkeypatch.setattr(inventory, "_worker", _failing_worker)
    result = inventory.collect_inventory(FOLDER, tmp_path)
    assert result["status"] == "ERROR" and result["code"] == "CAPTURE_FAILED"
    assert result["counts"]["get_requests"] == 0
    output = capfd.readouterr()
    assert "SYNTHETIC_PRIVATE_AUTH_DETAIL" not in output.out + output.err + json.dumps(result)
    assert list(tmp_path.iterdir()) == []


def _blocked_worker(folder_id, staging, sender):
    from universal_video import drive_adapter
    drive_adapter.access_token = lambda: "fake"
    drive_adapter.requests.Session = lambda: _ContextSession([folder(), page(incompleteSearch=True)])
    inventory._worker(folder_id, staging, sender)


def test_real_worker_unknown_completeness_cannot_publish_partial_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "_private_directory", lambda path: tmp_path)
    monkeypatch.setattr(inventory, "_worker", _blocked_worker)
    result = inventory.collect_inventory(FOLDER, tmp_path)
    assert result["status"] == "BLOCKED" and result["code"] == "UNKNOWN_COMPLETENESS"
    assert result["counts"]["get_requests"] == 2
    assert result["inventory_sha256"] is None and list(tmp_path.iterdir()) == []


def test_unsupported_runtime_stops_before_spawn_or_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory.os, "name", "nt")
    spawn = Mock(side_effect=AssertionError("spawn"))
    monkeypatch.setattr(inventory.multiprocessing, "get_context", spawn)
    result = inventory.collect_inventory(FOLDER, tmp_path)
    assert result["status"] == "BLOCKED"
    assert result["code"] == "UNSUPPORTED_PRIVATE_RUNTIME"
    spawn.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission enforcement requires a POSIX runtime")
def test_private_directory_rejects_permissions_and_symlinks(tmp_path):
    tmp_path.chmod(0o700)
    assert inventory._private_directory(tmp_path) == tmp_path
    tmp_path.chmod(0o750)
    with pytest.raises(inventory.InventoryError, match="UNSAFE_PRIVATE_DIRECTORY"):
        inventory._private_directory(tmp_path)
    tmp_path.chmod(0o700)
    link = tmp_path / "alias"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(inventory.InventoryError, match="UNSAFE_PRIVATE_DIRECTORY"):
        inventory._private_directory(link)
    parent = tmp_path / "writable"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    child = parent / "private"
    child.mkdir(mode=0o700)
    with pytest.raises(inventory.InventoryError, match="UNSAFE_PRIVATE_DIRECTORY"):
        inventory._private_directory(child)


def _synthetic_worker(folder_id, staging, sender):
    """Exercise the real worker in a child with a fully synthetic adapter."""
    from universal_video import drive_adapter
    drive_adapter.access_token = lambda: "synthetic-token"
    drive_adapter.requests.Session = lambda: _ContextSession([folder(), page()])
    inventory._worker(folder_id, staging, sender)


class _ContextSession(Session):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.mark.skipif(os.name != "posix", reason="Atomic private-file publication requires POSIX")
def test_successful_supervised_capture_is_private_and_not_overwritten(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    # Spawn imports a fresh module; the wrapper calls the original worker there.
    monkeypatch.setattr(inventory, "_worker", _synthetic_worker)
    result = inventory.collect_inventory(FOLDER, tmp_path)
    assert result["status"] == "COMPLETE"
    paths = list(tmp_path.glob("capture-*/snapshot.json"))
    assert len(paths) == 1
    original = paths[0].read_bytes()
    raw = json.loads(original)
    assert raw["pages"] == [page()]
    assert raw["inventory_sha256"] == result["inventory_sha256"]
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths[0].parent.stat().st_mode) == 0o700
    assert not list(tmp_path.glob("capture-*/*.partial"))
    assert inventory.collect_inventory(FOLDER, tmp_path) == result
    assert len(list(tmp_path.glob("capture-*/snapshot.json"))) == 2
    assert paths[0].read_bytes() == original


@pytest.mark.parametrize("changes", [{"status": "UNKNOWN"}, {"execution_allowed": True},
    {"inventory_sha256": "private data"}, {"counts": {"name": "private"}},
    {"code": "token=private"}, {"unrequested": "private"}])
def test_parent_rejects_unexpected_worker_receipts(changes):
    receipt = inventory._receipt("COMPLETE", "OK", dict.fromkeys(inventory.COUNT_KEYS, 0), "a" * 64)
    receipt.update(changes)
    with pytest.raises(inventory.InventoryError, match="WORKER_FAILED"):
        inventory._validate_receipt(receipt)


@pytest.mark.parametrize("status,code,exit_code", [("COMPLETE", "OK", 0),
    ("BLOCKED", "UNKNOWN_COMPLETENESS", 2), ("ERROR", "INVALID_METADATA", 1)])
def test_cli_only_emits_sanitized_receipt(tmp_path, monkeypatch, capsys, status, code, exit_code):
    receipt = inventory._receipt(status, code)
    monkeypatch.setattr(inventory, "collect_inventory", lambda *args: receipt)
    assert inventory.main(["--folder-id", FOLDER, "--private-dir", str(tmp_path)]) == exit_code
    captured = capsys.readouterr()
    assert json.loads(captured.out) == receipt and captured.err == ""
    assert FOLDER not in captured.out and str(tmp_path) not in captured.out


def test_import_and_mock_capture_cannot_download_enqueue_claim_start_upload_or_write_db():
    targets = ["universal_video.drive_adapter.access_token", "universal_video.drive_adapter.file_metadata",
        "universal_video.drive_adapter.list_folder_files", "universal_video.drive_adapter.download_file",
        "universal_video.video_queue.enqueue_drive_request", "universal_video.video_queue.enqueue_manifest",
        "universal_video.video_queue.claim_job", "universal_video.video_queue.database_url_from_env",
        "ops.ibm_vpc_power.create_action", "universal_video.drive_results.publish_result",
        "universal_video.drive_results._upload_or_verify_file", "psycopg.connect",
        "requests.sessions.Session.request", "urllib.request.urlopen", "socket.create_connection"]
    with ExitStack() as stack:
        spies = [stack.enter_context(patch(target, side_effect=AssertionError(target))) for target in targets]
        spec = importlib.util.spec_from_file_location("offline_inventory_probe", inventory.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        session = Session([folder(), page()])
        _, result = module._collect_metadata(FOLDER, token_provider=lambda: "fake", session=session,
                                            base_url=BASE, counts=dict.fromkeys(inventory.COUNT_KEYS, 0))
        assert result["status"] == "COMPLETE" and len(session.calls) == 2
        for spy in spies:
            spy.assert_not_called()
