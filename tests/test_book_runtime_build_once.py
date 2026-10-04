"""Synthetic-only review checks. Network blocked; no actual build execution."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import io
import json
from types import SimpleNamespace
import socket
import subprocess
import pytest
from tools.book_runtime_observation import build_once as hook
from tools.book_runtime_observation import build_entry

NOW = datetime(2026, 10, 4, 16, tzinfo=timezone.utc)
SECRET = "synthetic-resident-book-token"


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("real network forbidden"))


def env():
    return {"VERCEL_GIT_COMMIT_MESSAGE": hook.MARKER + "\nobserved_at=2026-10-04T16:00:00Z\nbase="
            + hook.READY_SHA + "\ndeployment=" + hook.READY_DEPLOYMENT + "\nnonce=" + "a" * 32,
            "VERCEL_GIT_COMMIT_SHA": "1" * 40, "VERCEL_ENV": "production", "VERCEL_GIT_COMMIT_REF": "main",
            "VERCEL_PROJECT_ID": hook.PROJECT, "BRIDGE_API_TOKEN": SECRET}


def data():
    return {"contract_version": "book-runtime-identity-v1", "status": "OBSERVED_NOT_ADMITTED",
            "observed_at": NOW.isoformat(), "deployment_revision": hook.READY_SHA,
            "connection": deepcopy(hook.CONNECTION), "neon": {
                "project_id": "misty-poetry-18012774", "endpoint_id": "ep-noisy-pine-b1pe30sf",
                "branch_id": "br-independent-observed", "source": "configuration file",
                "reset_matches": True, "pending_restart": False}}


class Response(io.BytesIO):
    def __init__(self, payload=None):
        super().__init__(json.dumps(payload or data()).encode())
        self.status = 200
        self.headers = {"Cache-Control": "private, no-store, max-age=0", "x-vercel-id": "fra1::synthetic-123"}
        self.url = hook.ORIGIN + hook.PATH

    def geturl(self):
        return self.url


class Client:
    def __init__(self, response):
        self.response, self.calls = response, []

    def open(self, request, **kwargs):
        self.calls.append(request)
        assert kwargs == {"timeout": 10}
        return self.response


@pytest.mark.parametrize("message_environment", [{}, {"VERCEL_GIT_COMMIT_MESSAGE": ""},
    {"VERCEL_GIT_COMMIT_MESSAGE": "Ordinary independent change"},
    {"VERCEL_GIT_COMMIT_MESSAGE": "TRUNCATED_" + hook.MARKER}])
def test_missing_or_changed_intent_stops_before_other_environment(monkeypatch, capsys, message_environment):
    class Guard(dict):
        def get(self, key, default=None):
            assert key == "VERCEL_GIT_COMMIT_MESSAGE"
            return super().get(key, default)
    monkeypatch.setattr(hook, "os", SimpleNamespace(environ=Guard(message_environment)))
    assert hook.main() == 1
    assert "INTENT_REQUIRED_VALIDATION_BUILD_ONLY" in capsys.readouterr().out


@pytest.mark.parametrize("key,value", [("VERCEL_ENV", "preview"), ("VERCEL_GIT_COMMIT_REF", "feature"),
    ("VERCEL_PROJECT_ID", "wrong"), ("VERCEL_GIT_COMMIT_SHA", ""),
    ("VERCEL_GIT_COMMIT_SHA", hook.READY_SHA)])
def test_wrong_context_precedes_credentials(key, value):
    values = env()
    values[key] = value
    with pytest.raises(hook.Rejected):
        hook.intent(values, NOW)


@pytest.mark.parametrize("seconds", [-1, 301, 7200])
def test_intent_time_window(seconds):
    with pytest.raises(hook.Rejected):
        hook.intent(env(), NOW + timedelta(seconds=seconds))


@pytest.mark.parametrize("edit", ["old_base", "deployment", "extra_line", "bad_nonce"])
def test_no_stale_or_expanded_intent(edit):
    values = env()
    msg = values["VERCEL_GIT_COMMIT_MESSAGE"]
    if edit == "old_base": msg = msg.replace(hook.READY_SHA, "66ece90986569299a927e30fa5db3bd94e82e787")
    if edit == "deployment": msg = msg.replace(hook.READY_DEPLOYMENT, "dpl_other")
    if edit == "extra_line": msg += "\nurl=https://other.invalid"
    if edit == "bad_nonce": msg = msg.replace("a" * 32, "short")
    values["VERCEL_GIT_COMMIT_MESSAGE"] = msg
    with pytest.raises(hook.Rejected):
        hook.intent(values, NOW)


def test_one_fixed_get_and_unadmitted_receipt():
    values = env()
    client = Client(Response())
    receipt = hook.observe(values, hook.intent(values, NOW), opener=client, now=NOW)
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request.full_url == "https://bridge-video-free.vercel.app/v1/knowledge/validation/runtime-identity"
    assert request.method == "GET" and request.data is None
    assert request.get_header("Authorization") == "Bearer " + SECRET
    assert receipt["status"] == "PENDING_DEPLOYMENT_CORRELATION"
    assert receipt["neon"]["branch_id"] == "br-independent-observed"
    assert receipt["expected_ready_sha"] == hook.READY_SHA
    assert SECRET not in json.dumps(receipt)
    assert hook.NoRedirect().redirect_request(None, None, None, None, None, "https://other.invalid") is None


@pytest.mark.parametrize("change", ["sha", "branch", "source", "restart", "writable", "stale", "extra", "secret"])
def test_reject_unbound_or_leaking_response(change):
    payload = data()
    if change == "sha": payload["deployment_revision"] = "66ece90986569299a927e30fa5db3bd94e82e787"
    if change == "branch": payload["neon"]["branch_id"] = "unknown"
    if change == "source": payload["neon"]["source"] = "session"
    if change == "restart": payload["neon"]["pending_restart"] = True
    if change == "writable": payload["connection"]["read_only"] = False
    if change == "stale": payload["observed_at"] = (NOW - timedelta(seconds=61)).isoformat()
    if change == "extra": payload["extra"] = "unexpected"
    if change == "secret": payload["neon"]["branch_id"] = "br-" + SECRET
    client = Client(Response(payload))
    with pytest.raises(hook.Rejected):
        hook.observe(env(), hook.intent(env(), NOW), opener=client, now=NOW)
    assert len(client.calls) == 1


@pytest.mark.parametrize("change", ["status", "redirect", "cache", "request_id"])
def test_http_binding_fails_without_retry(change):
    response = Response()
    if change == "status": response.status = 503
    if change == "redirect": response.url = "https://other.invalid"
    if change == "cache": response.headers["Cache-Control"] = "public"
    if change == "request_id": response.headers.pop("x-vercel-id")
    client = Client(response)
    with pytest.raises(hook.Rejected):
        hook.observe(env(), hook.intent(env(), NOW), opener=client, now=NOW)
    assert len(client.calls) == 1


def test_missing_resident_token_makes_no_request():
    values = env()
    values.pop("BRIDGE_API_TOKEN")
    client = Client(Response())
    with pytest.raises(hook.Rejected, match="RESIDENT_TOKEN_UNAVAILABLE"):
        hook.observe(values, hook.intent(values, NOW), opener=client, now=NOW)
    assert not client.calls


@pytest.mark.parametrize("key,value", [("tls_in_use", 1), ("read_only", 1), ("port", 5432.0)])
def test_connection_scalar_types_are_exact(key, value):
    payload = data()
    payload["connection"][key] = value
    with pytest.raises(hook.Rejected):
        hook.observe(env(), hook.intent(env(), NOW), opener=Client(Response(payload)), now=NOW)


def test_local_claim_refuses_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "gettempdir", lambda: str(tmp_path))
    binding = hook.intent(env(), NOW)
    hook.claim_once(binding)
    with pytest.raises(hook.Rejected, match="BUILD_ALREADY_CLAIMED"):
        hook.claim_once(binding)


def test_successful_observation_still_exits_failure_and_disarms(monkeypatch, capsys):
    alarms = []
    monkeypatch.setattr(hook, "intent", lambda *_: {"build_sha": "1" * 40, "nonce": "a" * 32})
    monkeypatch.setattr(hook, "claim_once", lambda *_: None)
    monkeypatch.setattr(hook, "observe", lambda *_: {"status": "PENDING_DEPLOYMENT_CORRELATION"})
    monkeypatch.setattr(hook.signal, "SIGALRM", 14, raising=False)
    monkeypatch.setattr(hook.signal, "signal", lambda *_: None)
    monkeypatch.setattr(hook.signal, "alarm", alarms.append, raising=False)
    assert hook.main() == 1
    assert alarms == [30, 0]
    assert "observation_complete_no_promotion" in capsys.readouterr().out


def test_entry_never_calls_canon_after_marked_book_hook(monkeypatch):
    monkeypatch.setattr(build_entry, "observe_book", lambda: 1)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("canon must not run"))
    assert build_entry.main() == 1


def test_entry_refuses_success_even_when_observation_returns_zero(monkeypatch):
    calls = []
    def observe():
        calls.append("book")
        return 0
    monkeypatch.setattr(build_entry, "observe_book", observe)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("canon must never run"))
    assert build_entry.main() == 1
    assert calls == ["book"]
