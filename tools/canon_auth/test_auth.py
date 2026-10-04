import io
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from . import db_diagnostic as d, vercel_validator as v

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
SECRET = "synthetic-never-print-this-token"


@pytest.fixture(autouse=True)
def synthetic_ready_binding(monkeypatch):
    monkeypatch.setattr(v, "READY_SHA", "a" * 40)
    monkeypatch.setattr(v, "DEPLOYMENT", "dpl_synthetic")


def intent():
    return dict(CANON_VALIDATION_INTENT=v.INTENT, CANON_READY_SHA=v.READY_SHA,
                CANON_READY_DEPLOYMENT=v.DEPLOYMENT, CANON_READY_STATE="READY",
                CANON_READY_ORIGIN=v.ORIGIN, VERCEL_PROJECT_ID=v.PROJECT,
                VERCEL_ENV="production", CANON_READY_OBSERVED_AT=NOW.isoformat(), BRIDGE_API_TOKEN=SECRET)


class Response(io.BytesIO):
    def __init__(self, url, status, data):
        super().__init__(json.dumps(data).encode())
        self.status, self.url = status, url
        number = 1 if url.endswith("healthz") else 2 if url.endswith("overview") else 3
        self.headers = {"x-vercel-id": f"fra1::fra1::test{number}-1791113746799-e295f3ece609"}

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
    assert result["status"] == "pending_deployment_correlation"
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
    ("CANON_READY_ORIGIN", "https://bridge-video-free-other.vercel.app"),
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


def test_unbound_retirement_target_never_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "READY_SHA", "")
    monkeypatch.setattr(v, "DEPLOYMENT", "")
    tr = Transport()
    with pytest.raises(v.Rejected, match="intent_rejected"):
        v.run(intent(), now=NOW, opener=tr, claim=tmp_path / "claim")
    assert not tr.calls


@pytest.mark.parametrize("header", ["", SECRET, "fra1::other", "fra1::fra1::" + SECRET,
                                    "fra1::fra1::test-1791113746799-e295f3ece609\n"])
def test_untrusted_request_id_not_emitted(tmp_path, header):
    def change(req, n):
        r = Response(req.full_url, 200, {"status": "ok"})
        r.headers["x-vercel-id"] = header
        return r
    tr = Transport(change)
    with pytest.raises(v.Rejected, match="request_id_rejected") as exc:
        v.run(intent(), now=NOW, opener=tr, claim=tmp_path / "claim")
    assert SECRET not in str(exc.value)
    assert len(tr.calls) == 1


def binding_fixture(tmp_path):
    result = v.run(intent(), now=NOW, opener=Transport(), claim=tmp_path / "claim")
    before = dict(project_id=v.PROJECT, deployment_id=v.DEPLOYMENT, sha=v.READY_SHA,
                  origin=v.ORIGIN, state="READY", observed_at=NOW.isoformat())
    after = before | {"observed_at": (NOW + timedelta(seconds=30)).isoformat()}
    logs = [dict(query_request_id=r["request_id"], query_project_id=v.PROJECT,
                 query_deployment_id=v.DEPLOYMENT, deployment_id=v.DEPLOYMENT,
                 matched_count=1, path=r["path"], status_code=r["status_code"],
                 request_at=(NOW + timedelta(seconds=10)).isoformat()) for r in result["receipts"]]
    return result, before, after, logs


def test_verified_refusal_requires_every_log_binding(tmp_path):
    from .verify_binding import verify
    result = verify(*binding_fixture(tmp_path))
    assert result["status"] == "authenticated_refusal_verified"
    assert result["requests_correlated"] == 3 and not result["baseline_abstain_proven"]


@pytest.mark.parametrize("mutation", ["alias_sha", "alias_deployment", "missing_log", "duplicate_log",
    "wrong_request", "wrong_log_deployment", "no_match", "wrong_path", "wrong_status", "wrong_time", "stale_window"])
def test_no_acceptance_from_alias_only_or_wrong_logs(tmp_path, mutation):
    from .verify_binding import verify
    result, before, after, logs = binding_fixture(tmp_path)
    if mutation == "alias_sha": after["sha"] = "b" * 40
    if mutation == "alias_deployment": after["deployment_id"] = "dpl_other"
    if mutation == "missing_log": logs.pop()
    if mutation == "duplicate_log": logs[2] = logs[0].copy()
    if mutation == "wrong_request": logs[0]["query_request_id"] = "different"
    if mutation == "wrong_log_deployment": logs[0]["deployment_id"] = "dpl_other"
    if mutation == "no_match": logs[0]["matched_count"] = 0
    if mutation == "wrong_path": logs[0]["path"] = "/other"
    if mutation == "wrong_status": logs[0]["status_code"] = 500
    if mutation == "wrong_time": logs[0]["request_at"] = (NOW - timedelta(seconds=1)).isoformat()
    if mutation == "stale_window": after["observed_at"] = (NOW + timedelta(seconds=301)).isoformat()
    with pytest.raises(v.Rejected):
        verify(result, before, after, logs)


@pytest.mark.parametrize("field", ["schema", "requests", "persisted", "queued", "finalized",
                                    "pilot_activated", "baseline_abstain_proven"])
def test_incomplete_result_never_verified(tmp_path, field):
    from .verify_binding import verify
    result, before, after, logs = binding_fixture(tmp_path)
    del result[field]
    with pytest.raises(v.Rejected, match="result_binding_rejected"):
        verify(result, before, after, logs)


def test_malformed_receipt_and_boolean_count_rejected(tmp_path):
    from .verify_binding import verify
    result, before, after, logs = binding_fixture(tmp_path)
    logs[0]["matched_count"] = True
    with pytest.raises(v.Rejected):
        verify(result, before, after, logs)
    logs[0]["matched_count"] = 1
    result["receipts"][0]["request_id"] = "bad"
    with pytest.raises(v.Rejected):
        verify(result, before, after, logs)


def test_health_id_cannot_echo_resident_token_even_without_auth_header(tmp_path):
    env = intent() | {"BRIDGE_API_TOKEN": "test1-1791113746799-e295f3ece609"}
    tr = Transport()
    with pytest.raises(v.Rejected, match="request_id_rejected"):
        v.run(env, now=NOW, opener=tr, claim=tmp_path / "claim")
    assert tr.calls[0].get_header("Authorization") is None
