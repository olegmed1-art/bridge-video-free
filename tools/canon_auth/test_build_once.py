import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from . import build_once as b, vercel_validator as v
from .test_auth import Response
from tools.tournament_pilot.package import position, catalog
from bridge_school_api.tournament_teacher import evaluate

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
SECRET = "synthetic-build-token-do-not-print"


def environment():
    return {"VERCEL_GIT_COMMIT_MESSAGE": f"{b.MARKER}\n\nobserved_at=2026-10-04T12:00:00Z\nbase={v.READY_SHA}",
            "VERCEL_GIT_COMMIT_REF": "main", "VERCEL_ENV": "production", "VERCEL_PROJECT_ID": v.PROJECT,
            "BRIDGE_API_TOKEN": SECRET}


def data(call, active):
    result = evaluate(position(UUID(int=101)), call, [catalog(call)] if active else [])
    return dict(result, position_id=v.POSITION, teacher_key="school-tournament-shape")


class Flow:
    def __init__(self, states=None):
        self.n = 0
        self.states = iter(states or [None, False, False, True, True, True, False, False, True, True])

    def open(self, req, timeout):
        self.n += 1
        if self.n == 1:
            payload, code = {"status": "ok"}, 200
            assert req.get_header("Authorization") is None
        elif self.n == 2:
            payload, code = {"stable_name": "synthetic"}, 200
        else:
            state = None if self.n == 3 else next(self.states)
            call = json.loads(req.data)["canon_request"]["proposed_call"]
            payload, code = ({"detail": {"code": "POSITION_NOT_FOUND"}}, 404) if state is None else (data(call, state), 200)
        response = Response(req.full_url, code, payload)
        response.headers["x-vercel-id"] = f"fra1::fra1::test{self.n}-1791113746799-e295f3ece609"
        return response


def test_lifecycle_http_only_and_every_phase_pending(tmp_path, capsys):
    env = b.intent(environment()) | {"BRIDGE_API_TOKEN": SECRET}
    flow, sleeps = Flow(), []
    result = b.lifecycle(env, opener=flow, sleep=sleeps.append, now=NOW, claim=tmp_path / "claim")
    lines = [json.loads(s) for s in capsys.readouterr().out.splitlines()]
    assert [r.get("phase") for r in lines if r.get("phase") != "poll"] == [None, "baseline", "active", "revoked", "reactivated", "all_requests"]
    assert all(r["status"] == "pending_deployment_correlation" for r in lines)
    assert result == {"status": "pending_external_final_acceptance", "requests": 13}
    assert len(lines[-1]["receipts"]) == flow.n
    assert SECRET not in json.dumps(lines)
    assert sleeps == [16, 8, 8, 8]


@pytest.mark.parametrize("message", ["ordinary commit", "", "prefix " + b.MARKER])
def test_future_ordinary_builds_do_not_start(message):
    assert b.intent(environment() | {"VERCEL_GIT_COMMIT_MESSAGE": message}) is None


def test_nonmain_and_wrong_project_do_not_start():
    with pytest.raises(v.Rejected):
        b.intent(environment() | {"VERCEL_GIT_COMMIT_REF": "feature"})
    with pytest.raises(v.Rejected):
        b.intent(environment() | {"VERCEL_PROJECT_ID": "other"})


def test_phase_timeout_bounded_and_no_activation_claim(tmp_path, capsys):
    flow = Flow(states=[None] * 20)
    with pytest.raises(v.Rejected, match="phase_deadline_exceeded"):
        b.lifecycle(b.intent(environment()) | {"BRIDGE_API_TOKEN": SECRET}, opener=flow, sleep=lambda _: None, now=NOW, claim=tmp_path / "claim")
    assert flow.n == 13
    assert '"phase": "active"' not in capsys.readouterr().out


@pytest.mark.parametrize("key,value", [("action", "3H"), ("persisted", True),
    ("point_threshold", 10), ("status", "SUPPORTED"), ("teacher_system", "L1")])
def test_active_assessment_no_semantic_or_write_drift(key, value):
    answer = data("3S", True) | {key: value}
    with pytest.raises(v.Rejected):
        b.assessment(answer, "3S")


def test_missing_gate_and_wrong_source_hash_rejected():
    answer = data("3H", False) | {"reason": "PILOT_POSITION_ONLY"}
    with pytest.raises(v.Rejected):
        b.assessment(answer, "3H")
    answer = data("3H", True)
    answer["source_bindings"][0]["payload_sha256"] = "0" * 64
    with pytest.raises(v.Rejected):
        b.assessment(answer, "3H")


def test_network_request_budget():
    class Counter:
        def __init__(self): self.n = 0
        def open(self): self.n += 1
    transport = Counter()
    budget = b.Budget(transport)
    for _ in range(40): budget.open()
    with pytest.raises(v.Rejected, match="request_budget_exceeded"): budget.open()
    assert transport.n == 40


@pytest.mark.parametrize("message", ["CANON_ACCEPTANCE_20261004_ONCE", b.MARKER, b.MARKER + "\ntruncated",
    b.MARKER + "\nobserved_at=2026-10-04T12:00:00Z\nbase=" + "0" * 40])
def test_malformed_marked_build_fails_closed(message):
    with pytest.raises(v.Rejected, match="build_intent_malformed"):
        b.intent(environment() | {"VERCEL_GIT_COMMIT_MESSAGE": message})


def test_successful_marked_build_never_promotes(monkeypatch, capsys):
    monkeypatch.setattr(b, "intent", lambda _: {})
    monkeypatch.setattr(b, "lifecycle", lambda _: {"status": "pending_external_final_acceptance"})
    monkeypatch.setattr(v, "validate_intent", lambda *_: None)
    monkeypatch.setattr(b.os, "environ", {"BRIDGE_API_TOKEN": SECRET})
    monkeypatch.setattr(b.signal, "SIGALRM", 14, raising=False)
    monkeypatch.setattr(b.signal, "signal", lambda *_: None)
    monkeypatch.setattr(b.signal, "alarm", lambda *_: None, raising=False)
    assert b.main() == 1
    assert "validation_complete_no_promotion" in capsys.readouterr().out


def test_duplicate_receipt_stops_before_next_phase(tmp_path, capsys):
    class Reused(Flow):
        def open(self, *args, **kwargs):
            response = super().open(*args, **kwargs)
            if self.n >= 5:
                response.headers["x-vercel-id"] = "fra1::test4-1791113746799-e295f3ece609"
            return response
    with pytest.raises(v.Rejected, match="duplicate_request_id"):
        b.lifecycle(b.intent(environment()) | {"BRIDGE_API_TOKEN": SECRET}, opener=Reused(), sleep=lambda _: None, now=NOW, claim=tmp_path / "claim")
    assert '"phase": "baseline"' not in capsys.readouterr().out


def test_actual_expired_entrypoint_never_copies_environment_or_reads_credentials(monkeypatch, capsys):
    class GuardedEnvironment(dict):
        def __iter__(self): pytest.fail("Never copy the whole environment")
        def keys(self): pytest.fail("Never copy the whole environment")
        def get(self, key, default=None):
            if key in ("BRIDGE_API_TOKEN", "BRIDGE_APP_DATABASE_URL"):
                pytest.fail("Expired entrypoint must not read credentials")
            return super().get(key, default)
    class Clock:
        @staticmethod
        def now(tz): return v.DEADLINE
    env = GuardedEnvironment(environment())
    monkeypatch.setattr(b.os, "environ", env)
    monkeypatch.setattr(b, "datetime", Clock)
    monkeypatch.setattr(b.signal, "alarm", lambda *_: None, raising=False)
    def forbidden(*args, **kwargs): pytest.fail("Expired entrypoint must not start lifecycle/HTTP/claim")
    monkeypatch.setattr(b, "lifecycle", forbidden)
    assert b.main() == 1
    assert '"code": "intent_rejected"' in capsys.readouterr().out


def test_intent_returns_only_public_context_without_iteration_or_credential_lookup():
    class GuardedEnvironment(dict):
        def __iter__(self): pytest.fail("Never copy the whole environment")
        def keys(self): pytest.fail("Never copy the whole environment")
        def get(self, key, default=None):
            if key == "BRIDGE_API_TOKEN": pytest.fail("Intent must not read token")
            return super().get(key, default)
    result = b.intent(GuardedEnvironment(environment()))
    assert set(result) == {"VERCEL_ENV", "VERCEL_PROJECT_ID", "CANON_VALIDATION_INTENT",
        "CANON_READY_SHA", "CANON_READY_DEPLOYMENT", "CANON_READY_STATE",
        "CANON_READY_ORIGIN", "CANON_READY_OBSERVED_AT"}
    assert SECRET not in json.dumps(result)
