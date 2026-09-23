from __future__ import annotations

import copy
import io
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from oracle_autopilot.github_codex_callback import CallbackContractError
from oracle_autopilot import codex_terminal_sweep as module
from tests.test_oracle_autopilot_github_codex_callback import _terminal_event


class Response:
    status = 200

    def __init__(self, url, data):
        self.url = url
        self.body = io.BytesIO(json.dumps(data).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def geturl(self):
        return self.url

    def read(self, size):
        return self.body.read(size)


def fixture():
    event = _terminal_event()
    event["comment"]["updated_at"] = event["comment"]["created_at"]
    candidate = (
        UUID("6275443a-5868-4c1f-9406-c0d72b8068bd"), 1150, 55, "AUTOPILOT",
        "197a0c833e928a66d8e38b0c82f43e64bcc31c78ec8f8bc83602a82e1d35474e", "READ_ONLY",
        datetime(2026, 9, 14, 19, 35, tzinfo=timezone.utc),
        datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    return event, candidate


def opener_for(event, rows=None):
    base = "https://api.github.com/repos/olegmed1-art/bridge-video-free"
    urls = []

    def opener(request, timeout):
        url = request.full_url
        assert timeout == 10
        urls.append(url)
        if "/comments?" in url:
            return Response(url, rows if rows is not None else [event["comment"]])
        if url == f"{base}/issues/1150":
            return Response(url, event["issue"])
        if url == f"{base}/issues/comments/5669745748":
            return Response(url, event["comment"])
        if url == f"{base}/pulls/1150":
            return Response(url, {"number": 1150, "state": "open", "head": {
                "sha": "2ceb48716988ec9cbd01be438a0ebf8b46836667"}})
        raise AssertionError(url)

    return opener, urls


def test_single_terminal_passes_original_event_parser_and_existing_rpc(monkeypatch):
    event, candidate = fixture()
    opener, urls = opener_for(event)
    monkeypatch.setattr(module, "load_candidates", lambda dsn: [candidate])
    receipts = []
    monkeypatch.setattr(module, "ingest_rest_readback", lambda dsn, t: (receipts.append(t.delivery_id) or True, "DONE"))
    result = module.sweep("dsn", "token", opener=opener)
    assert result == [{"dispatch_id": str(candidate[0]), "result": "ACCEPTED", "state": "DONE"}]
    assert receipts == ["github-codex-result:5669745748"]
    assert len(urls) == 5  # one bounded page, issue, two exact comment reads, target head


def test_github_second_precision_does_not_reject_same_second_publication():
    event, candidate = fixture()
    candidate = (*candidate[:6], datetime(2026, 9, 14, 19, 37, 32, 500000, tzinfo=timezone.utc), candidate[7])
    opener, _ = opener_for(event)
    terminal = module.find_original_comment(candidate, "token", opener=opener)
    assert terminal is not None and terminal.comment_id == 5669745748


def test_duplicate_original_terminal_fails_before_rpc(monkeypatch):
    event, candidate = fixture()
    clone = copy.deepcopy(event["comment"])
    clone["id"] = 5669745749
    clone["body"] = event["comment"]["body"]
    rows = [event["comment"], clone]
    opener, _ = opener_for(event, rows)
    # The duplicate ID is re-read via the same mock and must be supplied.
    base_opener = opener
    def with_clone(request, timeout):
        if request.full_url.endswith("/issues/comments/5669745749"):
            return Response(request.full_url, clone)
        return base_opener(request, timeout)
    monkeypatch.setattr(module, "ingest_rest_readback", lambda *_: pytest.fail("duplicate must not ingest"))
    with pytest.raises(CallbackContractError, match="DUPLICATE_TERMINAL"):
        module.find_original_comment(candidate, "token", opener=with_clone)


def test_repeated_sweep_keeps_original_delivery_id_and_rpc_deduplication(monkeypatch):
    event, candidate = fixture()
    opener, _ = opener_for(event)
    monkeypatch.setattr(module, "load_candidates", lambda dsn: [candidate])
    calls = []
    def existing_rpc(dsn, terminal):
        calls.append(terminal.delivery_id)
        return len(calls) == 1, "DONE"
    monkeypatch.setattr(module, "ingest_rest_readback", existing_rpc)
    assert module.sweep("dsn", "token", opener=opener)[0]["result"] == "ACCEPTED"
    assert module.sweep("dsn", "token", opener=opener)[0]["result"] == "DUPLICATE"
    assert calls == ["github-codex-result:5669745748"] * 2


def test_unrelated_or_no_terminal_does_not_write(monkeypatch):
    event, candidate = fixture()
    actor = copy.deepcopy(event["comment"])
    actor["user"]["id"] = 1
    opener, _ = opener_for(event, [actor])
    monkeypatch.setattr(module, "load_candidates", lambda dsn: [candidate])
    monkeypatch.setattr(module, "ingest_rest_readback", lambda *_: pytest.fail("no terminal must not ingest"))
    assert module.sweep("dsn", "token", opener=opener)[0]["result"] == "NO_TERMINAL"


def test_head_mismatch_retains_distinct_diagnostic_without_acceptance(monkeypatch):
    event, candidate = fixture()
    event["comment"]["body"] = event["comment"]["body"].replace(
        "target_head_sha=2ceb48716988ec9cbd01be438a0ebf8b46836667",
        "target_head_sha=" + "f" * 40,
    )
    opener, _ = opener_for(event)
    monkeypatch.setattr(module, "load_candidates", lambda dsn: [candidate])
    monkeypatch.setattr(module, "ingest_rest_readback", lambda *_: pytest.fail("head mismatch"))
    diagnostics = []
    monkeypatch.setattr(module, "record_failure", lambda *args: diagnostics.append(args))
    assert module.sweep("dsn", "token", opener=opener)[0]["result"] == "CODEX_PR_HEAD_MISMATCH"
    assert diagnostics == [("dsn", candidate[0], "CODEX_PR_HEAD_MISMATCH", 5669745748)]


def test_repair_head_mismatch_has_separate_reason(monkeypatch):
    event, candidate = fixture()
    candidate = (*candidate[:5], "REPAIR", *candidate[6:])
    event["comment"]["body"] = event["comment"]["body"].replace(
        "target_head_sha=2ceb48716988ec9cbd01be438a0ebf8b46836667",
        "target_head_sha=" + "f" * 40,
    )
    opener, _ = opener_for(event)
    monkeypatch.setattr(module, "load_candidates", lambda dsn: [candidate])
    monkeypatch.setattr(module, "ingest_rest_readback", lambda *_: pytest.fail("unapplied repair"))
    diagnostics = []
    monkeypatch.setattr(module, "record_failure", lambda *args: diagnostics.append(args))
    assert module.sweep("dsn", "token", opener=opener)[0]["result"] == "CODEX_REPAIR_HEAD_NOT_APPLIED"
    assert diagnostics == [("dsn", candidate[0], "CODEX_REPAIR_HEAD_NOT_APPLIED", 5669745748)]


def test_malformed_matching_bot_result_retains_comment_evidence(monkeypatch):
    event, candidate = fixture()
    event["comment"]["body"] = event["comment"]["body"].replace(
        "status=SUCCEEDED", "status=UNSUPPORTED",
    )
    opener, _ = opener_for(event)
    monkeypatch.setattr(module, "load_candidates", lambda dsn: [candidate])
    monkeypatch.setattr(module, "ingest_rest_readback", lambda *_: pytest.fail("invalid"))
    diagnostics = []
    monkeypatch.setattr(module, "record_failure", lambda *args: diagnostics.append(args))
    assert module.sweep("dsn", "token", opener=opener)[0]["result"] == "CODEX_READBACK_INVALID_RESULT"
    assert diagnostics == [("dsn", candidate[0], "CODEX_READBACK_INVALID_RESULT", 5669745748)]


def test_expired_candidate_stops_before_github_or_db(monkeypatch):
    _, candidate = fixture()
    candidate = (*candidate[:-1], datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(CallbackContractError, match="DEADLINE_EXPIRED"):
        module.find_original_comment(candidate, "token", opener=lambda *_: pytest.fail("expired"))


def test_more_than_two_full_pages_fails_closed():
    event, candidate = fixture()
    rows = [dict(event["comment"], user={"id": 1}) for _ in range(100)]
    opener, urls = opener_for(event, rows)
    with pytest.raises(CallbackContractError, match="PAGE_LIMIT"):
        module.find_original_comment(candidate, "token", opener=opener)
    assert len(urls) == 2


def test_workflow_is_auto_and_candidate_rpc_least_privilege():
    workflow = open(".github/workflows/autopilot-codex-terminal-sweep.yml", encoding="utf-8").read()
    migration = open("database/migrations/0372_autopilot_codex_terminal_readback_candidates.sql", encoding="utf-8").read()
    assert "cron: '*/5 * * * *'" in workflow
    assert "vars.AUTOPILOT_CODEX_READBACK_ENABLED == 'true'" in workflow
    assert "ops.github_autopilot_db_route codex-terminal-sweep" in workflow
    assert "contents: write" not in workflow
    assert "LIMIT 7" in migration and "status IN ('SENT','PUBLISHED')" in migration
    assert "GRANT EXECUTE ON FUNCTION autopilot.codex_terminal_readback_candidates()" in migration
    assert "TO autopilot_callback" in migration
    candidate_definition = migration.split("CREATE FUNCTION autopilot.codex_terminal_readback_candidates()", 1)[1].split("REVOKE ALL ON FUNCTION", 1)[0]
    assert "UPDATE autopilot." not in candidate_definition
    assert "ingress_provenance='GITHUB_REST_DOUBLE_READ'" in migration
