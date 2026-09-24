from __future__ import annotations

import copy
import io
import json

import pytest

from oracle_autopilot import codex_terminal_readback as readback
from oracle_autopilot.codex_terminal_readback import read_original_terminal
from oracle_autopilot.github_codex_callback import CallbackContractError
from tests.test_oracle_autopilot_github_codex_callback import _terminal_event


class Response:
    status = 200

    def __init__(self, url, payload):
        self.url = url
        self.body = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def geturl(self):
        return self.url

    def read(self, size):
        return self.body.read(size)


def opener_for(event, *, changed_second=None):
    base = "https://api.github.com/repos/olegmed1-art/bridge-video-free"
    seen = []

    def opener(request, timeout):
        url = request.full_url
        assert timeout == 10
        seen.append(url)
        if url == f"{base}/issues/1150":
            return Response(url, event["issue"])
        if url == f"{base}/issues/comments/5669745748":
            comment = changed_second if len(seen) == 3 and changed_second is not None else event["comment"]
            return Response(url, comment)
        raise AssertionError(url)

    return opener, seen


def test_exact_original_comment_replays_through_existing_parser():
    event = _terminal_event()
    event["comment"]["updated_at"] = event["comment"]["created_at"]
    opener, seen = opener_for(event)
    terminal = read_original_terminal(1150, 5669745748, "test-token", opener=opener)
    assert terminal.delivery_id == "github-codex-result:5669745748"
    assert terminal.dispatch_id == "6275443a-5868-4c1f-9406-c0d72b8068bd"
    assert len(seen) == 3


@pytest.mark.parametrize("change", [
    lambda c: c.update(body="changed"),
    lambda c: c.update(issue_url="https://api.github.com/repos/olegmed1-art/bridge-video-free/issues/797"),
    lambda c: c["user"].update(id=1),
    lambda c: c["performed_via_github_app"].update(id=1),
    lambda c: c.update(updated_at="2026-09-14T20:37:32Z"),
])
def test_changed_wrong_pr_wrong_actor_app_or_edited_comment_fails_closed(change):
    event = _terminal_event()
    event["comment"]["updated_at"] = event["comment"]["created_at"]
    corrupted = copy.deepcopy(event["comment"])
    change(corrupted)
    opener, _ = opener_for(event, changed_second=corrupted)
    with pytest.raises(CallbackContractError):
        read_original_terminal(1150, 5669745748, "test-token", opener=opener)


def test_input_does_not_create_dispatch_or_extend_deadline():
    source = open("oracle_autopilot/codex_terminal_readback.py", encoding="utf-8").read()
    assert "ingest_rest_readback(" in source
    assert "accept_role_dispatch_codex_rest_readback(" in source
    assert "accept_role_dispatch_codex_terminal(" not in source
    assert "UPDATE autopilot." not in source
    workflow = open(".github/workflows/autopilot-codex-terminal-readback.yml", encoding="utf-8").read()
    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "github.actor == 'olegmed1-art'" in workflow
    assert "contents: write" not in workflow
    assert "issues: write" not in workflow


def test_rest_readback_uses_distinct_rpc_and_retains_original_delivery_id(monkeypatch):
    terminal = readback.parse_terminal_event(_terminal_event())
    seen = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def execute(self, sql, params): seen.append((sql, params))
        def fetchone(self): return (True, "DONE")
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def cursor(self): return Cursor()
        def commit(self): pass
    monkeypatch.setattr(readback, "validate_callback_dsn", lambda dsn: dsn)
    monkeypatch.setattr(readback.psycopg, "connect", lambda *args, **kwargs: Connection())
    assert readback.ingest_rest_readback("dsn", terminal) == (True, "DONE")
    sql, params = seen[0]
    assert "accept_role_dispatch_codex_rest_readback" in sql
    assert "accept_role_dispatch_codex_terminal(" not in sql
    assert params[0] == terminal.delivery_id
    assert params[2] is True  # API verification, not event signature
