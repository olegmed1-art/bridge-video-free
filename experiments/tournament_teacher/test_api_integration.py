"""HTTP tests against the existing app:app route, not a substitute endpoint."""
from contextlib import contextmanager
from copy import deepcopy
from uuid import UUID
import socket

import pytest
from fastapi.testclient import TestClient
import psycopg

import app as entrypoint
from bridge_school_api import ai_teacher, ai_decision, ai, db, knowledge
from bridge_school_api.main import require_api_token
from bridge_school_api.tournament_teacher_test_adapter import (
    OfflineTournamentTeacherTarget, PROFILE, SOURCE_VERSION, TEACHER_KEY,
    TEACHER_VERSION, position_uuid,
)
from .consumer import MODE
from .scenarios import CASES, make_request, scenarios


def body(context):
    return dict(teacher_key=TEACHER_KEY,teacher_version=TEACHER_VERSION,
                teacher_system=PROFILE,test_source_version=SOURCE_VERSION,test_request=context)


def url(context):
    return f"/v1/ai/positions/{position_uuid(context)}/teacher-evidence"


def forbidden(*args,**kwargs):
    raise AssertionError("Test attempted a database connection or network socket")


@pytest.fixture
def client(monkeypatch):
    # Real entrypoint, router, validation, auth dependency, middleware, adapter.
    # Only auth success is stubbed; no real credential is read or transmitted.
    monkeypatch.setattr(entrypoint.app.state,"tournament_teacher_test_target",
                        OfflineTournamentTeacherTarget(),raising=False)
    monkeypatch.setitem(entrypoint.app.dependency_overrides,require_api_token,lambda:None)
    monkeypatch.setenv("VERCEL_OIDC_TOKEN","")
    monkeypatch.setenv("BRIDGE_API_TOKEN","")
    for module in (db,ai_teacher,ai_decision,ai,knowledge):
        monkeypatch.setattr(module,"connect",forbidden)
    monkeypatch.setattr(psycopg,"connect",forbidden)
    with TestClient(entrypoint.app) as http:
        # Windows creates a loopback socket pair while constructing its loop.
        # Ban new connections only after the ASGI test portal is initialized.
        monkeypatch.setattr(socket.socket,"connect",forbidden)
        yield http


def test_actual_entrypoint_mounts_original_function_once():
    # FastAPI 0.141 retains included routers rather than flattening app.routes.
    included = [r for r in entrypoint.app.routes if getattr(r,"original_router",None) is ai_teacher.router]
    assert len(included) == 1
    routes = [r for r in ai_teacher.router.routes if getattr(r,"path",None) ==
              "/v1/ai/positions/{position_id}/teacher-evidence"]
    assert len(routes) == 1
    assert routes[0].endpoint is ai_teacher.record_teacher_evidence


@pytest.mark.parametrize("case",CASES,ids=lambda c:c[0])
def test_all_rules_through_real_http_request_and_response(client,case):
    for category, context, expected in scenarios(case):
        response = client.post(url(context),json=body(context))
        assert response.status_code == 200,(category,response.text)
        result = response.json()
        assert result["status"] == expected,(category,result)
        assert result["teacher_version"] == TEACHER_VERSION
        assert result["source_version"] == SOURCE_VERSION
        assert result["teacher_system"] == PROFILE
        assert result["action"] is None  # these requests assess a proposed call
        assert result["explanation"]
        assert result["persisted"] is result["queued"] is result["finalized"] is False
        assert result["formal_db_activation_asserted"] is False
        assert response.headers["cache-control"] == "private, no-store, max-age=0"
        for source in result["source_bindings"]:
            assert source["source_id"] == "SRC-0096"
            assert source["database_rule_uuid"] is None
            assert UUID(source["binding_uuid"]).version == 5
            assert source["decision_ids"]


def test_recommend_and_abstain_end_to_end(client):
    context = make_request(next(c for c in CASES if c[0]=="1H-1NT-REBID-2NT"))
    context.pop("proposed_call")
    context["task"] = "recommend"
    result = client.post(url(context),json=body(context)).json()
    assert result["action"] == "2NT" and result["status"] == "RECOMMEND"
    assert any("TDEC-20261002-008" in s["decision_ids"] for s in result["source_bindings"])
    del context["school_points"]
    result = client.post(url(context),json=body(context)).json()
    assert result["action"] is None and result["status"] == "ABSTAIN"
    assert "SYNTHETIC_SCHOOL_POINTS_REQUIRED" in result["raw_output_json"]["abstain_reasons"]


@pytest.mark.parametrize("target",[None,True,{},"enabled"])
def test_default_and_invalid_opt_in_cannot_reach_db(client,monkeypatch,target):
    monkeypatch.setattr(entrypoint.app.state,"tournament_teacher_test_target",target)
    context = make_request(CASES[0])
    response = client.post(url(context),json=body(context))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TOURNAMENT_TEACHER_TEST_DISABLED"


def test_normal_app_has_no_opt_in_in_fresh_interpreter():
    import subprocess,sys
    code = "import app; assert not hasattr(app.app.state,'tournament_teacher_test_target')"
    subprocess.run([sys.executable,"-c",code],check=True,capture_output=True)


@pytest.mark.parametrize("enabled",[False,True])
@pytest.mark.parametrize("hint",[
    {"teacher_version":TEACHER_VERSION},
    {"teacher_version":"tournament-teacher-test-v1"},
    {"testRequest":{},"testSourceVersion":SOURCE_VERSION},
    {"test_request":None},{"teacher_key":"tournament-canon-test-old"},
])
def test_ambiguous_test_envelopes_never_fall_into_legacy_sql(client,monkeypatch,enabled,hint):
    monkeypatch.setattr(entrypoint.app.state,"tournament_teacher_test_target",
                        OfflineTournamentTeacherTarget() if enabled else None)
    payload = dict(teacher_key="other",teacher_system=PROFILE,action="PASS",**{})
    payload.update(hint)
    response = client.post(url(make_request(CASES[0])),json=payload)
    assert response.status_code in (404,409,422)
    if not enabled:
        assert response.json()["detail"]["code"] == "TOURNAMENT_TEACHER_TEST_DISABLED"


def test_existing_auth_dependency_is_not_bypassed(client,monkeypatch):
    monkeypatch.delitem(entrypoint.app.dependency_overrides,require_api_token)
    monkeypatch.delenv("BRIDGE_API_TOKEN",raising=False)
    context = make_request(CASES[0])
    assert client.post(url(context),json=body(context)).status_code == 503


@pytest.mark.parametrize("key,value,status",[
    ("test_source_version","stale",409),("teacher_system","SCHOOL_L1_DB_V1",409),
    ("teacher_version","old",409),("teacher_key","other",409),
    ("test_request",None,422),("test_request",[],422),
    ("action","PASS",422),("confidence",1,422),
    ("raw_output",{"action":"PASS"},422),("candidate_scores",{"PASS":1},422),
    ("explanation","caller fabricated",422),
])
def test_bad_bindings_and_injected_outputs_refuse(client,key,value,status):
    context = make_request(CASES[0])
    payload = body(context)
    payload[key] = value
    assert client.post(url(context),json=payload).status_code == status


def test_position_and_snapshot_bindings_refuse_stale_input(client,monkeypatch):
    context = make_request(CASES[0])
    payload = body(deepcopy(context))
    payload["test_request"]["school_points"]["value"] += 1
    assert client.post(url(context),json=payload).json()["detail"]["code"] == "SYNTHETIC_POSITION_BINDING_MISMATCH"
    import bridge_school_api.tournament_teacher_test_adapter as adapter
    monkeypatch.setattr(adapter,"RULES_SHA256","stale")
    assert client.post(url(context),json=body(context)).json()["detail"]["code"] == "TEST_RULE_SNAPSHOT_MISMATCH"


def test_production_profile_gate_stays_closed_over_http(client):
    response = client.get("/v1/knowledge/runtime/l1",params={"system_profile":PROFILE})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "RUNTIME_PROFILE_GAP"


def test_legacy_teacher_write_contract_and_finalizer_are_unchanged(client,monkeypatch):
    """A transaction double exercises the existing SQL path; no DB is contacted."""
    position = UUID("00000000-0000-4000-8000-000000000001")
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,params): self.sql,self.params=sql,params
        def fetchall(self): return []
        def fetchone(self):
            if "INSERT INTO ai.teacher_output" in self.sql:
                assert self.params[1:5] == ("legacy-teacher","legacy-v1","SCHOOL_L1_DB_V1","1S")
                return {"position_id":position,"teacher_key":"legacy-teacher","action":"1S"}
            if "count(*)" in self.sql: return {"n":1}
            if "SELECT * FROM ai.decision_position" in self.sql:
                return dict(position_id=position,input_status="COMPLETE",system_us=PROFILE)
            if "SELECT 1 FROM ai.decision_position" in self.sql: return {"exists":1}
            return None
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def cursor(self): return Cursor()
        def commit(self): pass
    monkeypatch.setattr(ai_teacher,"connect",Connection)
    monkeypatch.setattr(ai_decision,"connect",Connection)
    legacy = client.post(f"/v1/ai/positions/{position}/teacher-evidence",json=dict(
        teacher_key="legacy-teacher",teacher_version="legacy-v1",teacher_system="SCHOOL_L1_DB_V1",action="1S"))
    assert legacy.status_code == 200 and legacy.json()["action"] == "1S"
    final = client.post(f"/v1/ai/positions/{position}/finalize")
    assert final.status_code == 200
    assert final.json()["status"] == "INSUFFICIENT_EVIDENCE"
    assert final.json()["finalized"] is False


def test_source_target_manifest_matches_executable_bindings():
    import json
    from pathlib import Path
    from .api_manifest import manifest
    saved = json.loads(Path(__file__).with_name("api_manifest.json").read_text())
    assert saved == manifest()
    assert saved["deployed_target"] is None
    assert len(saved["rule_bindings"]) == 26
    assert all(r["database_rule_uuid"] is None for r in saved["rule_bindings"])


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("modified", [False, True])
def test_snapshot_newlines_and_content_integrity_over_http(client, monkeypatch, tmp_path, newline, modified):
    import experiments.tournament_teacher.consumer as consumer
    snapshot = consumer.RULES_PATH.read_text(encoding="utf-8")
    if modified:
        snapshot += " "  # Even valid JSON whitespace changes the pinned snapshot.
    path = tmp_path / "rules.json"
    path.write_bytes(snapshot.encode("utf-8").replace(b"\n", newline))
    monkeypatch.setattr(consumer, "RULES_PATH", path)
    context = make_request(CASES[0])
    response = client.post(url(context), json=body(context))
    assert response.status_code == (409 if modified else 200)
    if modified:
        assert response.json()["detail"]["code"] == "TEST_RULE_SNAPSHOT_MISMATCH"


def test_non_utf8_snapshot_refuses_over_http(client, monkeypatch, tmp_path):
    import experiments.tournament_teacher.consumer as consumer
    path = tmp_path / "invalid.json"
    path.write_bytes(b"\xff")
    monkeypatch.setattr(consumer, "RULES_PATH", path)
    context = make_request(CASES[0])
    response = client.post(url(context), json=body(context))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "TEST_RULE_SNAPSHOT_UNAVAILABLE"
