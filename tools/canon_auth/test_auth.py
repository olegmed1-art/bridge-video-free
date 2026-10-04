import io
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from . import db_diagnostic as d, vercel_validator as v

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
SECRET = "synthetic-never-print-this-token"


def intent():
    return dict(CANON_VALIDATION_INTENT=v.INTENT, CANON_READY_SHA=v.READY_SHA,
                CANON_READY_DEPLOYMENT=v.DEPLOYMENT, CANON_READY_STATE="READY",
                CANON_READY_ORIGIN=v.ORIGIN, VERCEL_PROJECT_ID=v.PROJECT,
                VERCEL_ENV="production", CANON_READY_OBSERVED_AT=NOW.isoformat(), BRIDGE_API_TOKEN=SECRET)


class Response(io.BytesIO):
    def __init__(self, url, status, data):
        super().__init__(json.dumps(data).encode())
        self.status, self.url = status, url

    def geturl(self):
        return self.url


class Transport:
    def __init__(self, change=None):
        self.calls = []
        self.change = change

    def open(self, req, timeout):
        self.calls.append(req)
        assert timeout == 8
        n = len(self.calls)
        if self.change:
            changed = self.change(req, n)
            if changed:
                return changed
        return Response(req.full_url, 404 if n == 3 else 200,
                        ({"status": "ok"} if n == 1 else {"stable_name": "synthetic"} if n == 2
                         else {"detail": {"code": "POSITION_NOT_FOUND"}}))


def test_only_fixed_requests_and_redacted_result(tmp_path):
    tr = Transport()
    result = v.run(intent(), now=NOW, opener=tr, claim=tmp_path / "claim")
    assert result["status"] == "authenticated_refusal_pass"
    assert not result["baseline_abstain_proven"]
    assert len(tr.calls) == 3 and SECRET not in json.dumps(result)
    assert tr.calls[0].get_header("Authorization") is None
    assert all(req.full_url.startswith(v.ORIGIN + "/") for req in tr.calls)
    assert tr.calls[1].get_header("Authorization") == "Bearer " + SECRET
    body = json.loads(tr.calls[2].data)
    assert body["canon_request"]["proposed_call"] == "3H"
    assert "action" not in body


@pytest.mark.parametrize("field,value", [
    ("CANON_READY_SHA", "0" * 40), ("CANON_READY_STATE", "BUILDING"),
    ("CANON_READY_ORIGIN", "https://attacker.invalid"),
    ("CANON_READY_ORIGIN", "https://bridge-video-free.vercel.app"),
    ("CANON_READY_DEPLOYMENT", "other"), ("CANON_VALIDATION_INTENT", ""),
    ("VERCEL_PROJECT_ID", "other"), ("VERCEL_ENV", "preview"),
    ("CANON_READY_OBSERVED_AT", (NOW - timedelta(seconds=301)).isoformat()),
    ("CANON_READY_OBSERVED_AT", (NOW + timedelta(seconds=1)).isoformat()),
    ("BRIDGE_API_TOKEN", ""), ("BRIDGE_API_TOKEN", "x\r\ny")])
def test_bad_intent_never_networks(tmp_path, field, value):
    env = intent() | {field: value}
    tr = Transport()
    with pytest.raises(v.Rejected):
        v.run(env, now=NOW, opener=tr, claim=tmp_path / "claim")
    assert not tr.calls


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 500])
def test_protection_and_redirect_stop_before_token(tmp_path, status):
    def fail(req, n):
        raise HTTPError(req.full_url, status, SECRET, {"Location": "https://attacker.invalid"}, io.BytesIO(SECRET.encode()))
    tr = Transport(fail)
    with pytest.raises(v.Rejected, match="http_rejected") as exc:
        v.run(intent(), now=NOW, opener=tr, claim=tmp_path / "claim")
    assert SECRET not in str(exc.value)
    assert len(tr.calls) == 1 and tr.calls[0].get_header("Authorization") is None
    assert v.NoRedirect().redirect_request(None, None, None, None, None, None) is None


def test_response_origin_cannot_change(tmp_path):
    tr = Transport(lambda req, n: Response("https://attacker.invalid", 200, {"status": "ok"}))
    with pytest.raises(v.Rejected, match="response_origin_rejected"):
        v.run(intent(), now=NOW, opener=tr, claim=tmp_path / "claim")
    assert len(tr.calls) == 1


def test_second_invocation_even_after_failure_cannot_request(tmp_path):
    tr = Transport(lambda req, n: Response(req.full_url, 503, {"token": SECRET}))
    for code in ("health_rejected", "already_attempted"):
        with pytest.raises(v.Rejected, match=code):
            v.run(intent(), now=NOW, opener=tr, claim=tmp_path / "claim")
    assert len(tr.calls) == 1


def test_expired_and_arbitrary_path_rejected():
    with pytest.raises(v.Rejected):
        v.validate_intent(intent(), v.DEADLINE)
    with pytest.raises(v.Rejected):
        v.request(Transport(), "https://attacker.invalid", token=SECRET)


def test_main_does_not_print_exception_or_response(monkeypatch, capsys):
    def fail(*a, **k):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(v, "run", fail)
    assert v.main() == 1
    assert SECRET not in capsys.readouterr().out


def db_env():
    return dict(CANON_DIAGNOSTIC_INTENT=d.INTENT, GITHUB_REPOSITORY="olegmed1-art/bridge-video-free",
                GITHUB_REF=d.BRANCH, GITHUB_EVENT_NAME="push", GITHUB_RUN_NUMBER="1", GITHUB_RUN_ATTEMPT="1")


@pytest.mark.parametrize("field", list(db_env()))
def test_db_intent_and_repeat_guard(field):
    assert d.authorized(db_env(), NOW)
    assert not d.authorized(db_env() | {field: "other"}, NOW)
    assert not d.authorized(db_env(), d.DEADLINE)


def setup_dsn(monkeypatch, query="sslmode=require&channel_binding=require", host=None):
    for key in list(d.os.environ):
        if key.startswith("PG") or key == "VERCEL_ENV":
            monkeypatch.delenv(key)
    host = host or d.db.PRODUCTION_HOST
    monkeypatch.setenv("BRIDGE_APP_DATABASE_URL", f"postgresql://{d.db.EXPECTED_PRINCIPAL}:{SECRET}@{host}/neondb?{query}")


def test_db_auth_failure_only_safe_metadata(monkeypatch):
    setup_dsn(monkeypatch)
    def fail(*args, **kwargs):
        assert kwargs["options"] == "-c default_transaction_read_only=on -c statement_timeout=5000"
        assert kwargs["connect_timeout"] == 8
        raise RuntimeError("authentication failed " + SECRET)
    monkeypatch.setattr(d.psycopg, "connect", fail)
    result = d.probe()
    assert result["status"] == "authentication_failed"
    assert result["effective_target"] == "production_pooler"
    assert all(result["checks"].values())
    assert SECRET not in json.dumps(result)
    assert "VERCEL_ENV" not in d.os.environ


@pytest.mark.parametrize("query,host", [
    ("sslmode=require&channel_binding=require&host=attacker.invalid", None),
    ("sslmode=require&channel_binding=require&user=other", None),
    ("sslmode=require&channel_binding=require&options=evil", None),
    ("sslmode=disable&channel_binding=require", None),
    ("sslmode=require&channel_binding=require", d.db.PREVIEW_HOST)])
def test_db_wrong_target_or_override_never_connects(monkeypatch, query, host):
    setup_dsn(monkeypatch, query, host)
    def forbidden(*a, **k):
        pytest.fail("Unexpected database connection")
    monkeypatch.setattr(d.psycopg, "connect", forbidden)
    assert d.probe()["status"] in ("configuration_rejected", "connection_or_configuration_failed")


def test_db_libpq_environment_blocked(monkeypatch):
    setup_dsn(monkeypatch)
    monkeypatch.setenv("PGPASSWORD", SECRET)
    assert d.probe()["status"] == "libpq_environment_rejected"
