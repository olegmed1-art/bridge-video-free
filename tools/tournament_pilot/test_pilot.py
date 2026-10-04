"""New formal evaluator and existing-route boundary; SQL gates run in CI rehearsal."""
from copy import deepcopy
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from bridge_school_api import tournament_teacher as teacher, ai_teacher
from tools.tournament_pilot.package import formal_package
from tools.tournament_pilot.package import position,envelope

SCHOOL=UUID("00000000-0000-4000-8000-000000000101")


def catalog(call):
    entry=next(r for r in formal_package()["rules"] if r["payload"]["source_rule"]["call"]==call)
    return dict(school_id=SCHOOL,scope_key=teacher.SCOPE,rule_key=entry["rule_key"],
        rule_id=UUID(int=201),knowledge_version_id=UUID(int=202),runtime_activation_id=UUID(int=203),
        compiled_payload=entry["payload"],**entry["payload"]["catalog"])


def test_compiler_matches_reviewed_runtime_pin():
    pkg=formal_package()
    assert pkg["create_activations"] is False and pkg["school_id"] is None
    for entry in pkg["rules"]:
        assert entry["payload_sha256"]==teacher.PAYLOAD_HASHES[entry["payload"]["source_rule"]["call"]]


@pytest.mark.parametrize("call",["3H","3S"])
def test_all_560_distributions_against_explicit_four_allowed_shapes(call):
    row=catalog(call)
    allowed=({(3,1,4,5),(3,1,5,4)} if call=="3H" else {(1,3,4,5),(1,3,5,4)})
    total=0
    for s in range(14):
        for h in range(14-s):
            for d in range(14-s-h):
                shape=(s,h,d,13-s-h-d)
                answer=teacher.evaluate(position(SCHOOL,shape),call,[row])
                assert answer["status"]==("SUPPORTED" if shape in allowed else "CONTRADICTED")
                assert answer["action"] is None and answer["point_threshold"] is None
                assert answer["assessment_scope"]=="shape_meaning_only"
                total+=1
    assert total==560


@pytest.mark.parametrize("field,value",[("seat","E"),("dealer",None),("hand_pbn","AAAA.2.234.23456"),
    ("hand_pbn",None),("auction_json",["1NT","X"]),("cards_played_json",["SA"]),
    ("dummy_pbn","234.2.2345.23456"),("input_status","INCOMPLETE"),
    ("system_us","BEN_DEFAULT"),("decision_type","PLAY"),("school_status","inactive")])
def test_context_gaps_abstain(field,value):
    pos=position(SCHOOL,(3,1,4,5),{field:value})
    assert teacher.evaluate(pos,"3H",[catalog("3H")])["status"]=="ABSTAIN"


@pytest.mark.parametrize("mutation",["none","duplicate","version","school","scope","payload","columns"])
def test_missing_or_mismatched_binding_cannot_activate(mutation):
    row=catalog("3H")
    rows=[row]
    if mutation=="none": rows=[]
    if mutation=="duplicate": rows=[row,deepcopy(row)]
    if mutation=="version": row["method_version"]="other"
    if mutation=="school": row["school_id"]=UUID(int=9)
    if mutation=="scope": row["scope_key"]="default"
    if mutation=="payload": row["compiled_payload"]["recommend_supported"]=True
    if mutation=="columns": row["hand_constraints"]={"singleton":"S"}
    result=teacher.evaluate(position(SCHOOL,(3,1,4,5)),"3H",rows)
    assert result["status"]=="ABSTAIN" and result["source_bindings"]==[]


@pytest.mark.parametrize("field,value",[("hand_constraints",{"shape_sorted":[True,3,4,5],"singleton":"H","minor_lengths":[4,5]}),
    ("action",{"call":"3H","assessment_only":1})])
def test_catalog_json_types_are_bound_exactly(field,value):
    row=catalog("3H")
    row[field]=value
    result=teacher.evaluate(position(SCHOOL,(3,1,4,5)),"3H",[row])
    assert result["status"]=="ABSTAIN" and result["reason"]=="CANON_COLUMN_BINDING_MISMATCH"


@pytest.mark.parametrize("change,expected",[
    ({"action":"3H"},422),({"canon_request":None},422),({"canon_request":{}},422),
    ({"teacher_version":"stale"},409),({"teacher_system":"BEN_DEFAULT"},409),
    ({"test_request":{}},422),({"canonRequest":{}},422),
])
def test_invalid_request_never_falls_into_legacy_writer(monkeypatch,change,expected):
    import app as entrypoint
    from bridge_school_api.main import require_api_token
    def forbidden(): raise AssertionError("Invalid request reached SQL")
    monkeypatch.setattr(ai_teacher,"connect",forbidden)
    monkeypatch.setattr(teacher,"connect",forbidden)
    monkeypatch.setenv("VERCEL_OIDC_TOKEN","")
    monkeypatch.setitem(entrypoint.app.dependency_overrides,require_api_token,lambda:None)
    with TestClient(entrypoint.app) as client:
        response=client.post('/v1/ai/positions/00000000-0000-4000-8000-000000000001/teacher-evidence',json=envelope("3H")|change)
        assert response.status_code==expected,response.text


@pytest.fixture
def screen():
    from threading import Thread
    from .screen import create_server
    with create_server(port=0) as server:
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_port}"
        server.shutdown();thread.join(timeout=5)


def test_screen_access_and_provenance(screen):
    import httpx,re
    page=httpx.get(screen)
    assert page.status_code==200 and "__NONCE__" not in page.text
    assert page.headers["x-frame-options"]=="DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    nonce=re.search(r'nonce="([^"]+)"',page.text).group(1)
    headers={"Origin":screen,"X-Pilot-Nonce":nonce}
    for call,status in [("3H","SUPPORTED"),("3S","CONTRADICTED")]:
        response=httpx.post(screen+"/assess",json={"call":call},headers=headers)
        assert response.status_code==200 and response.json()["status"]==status
        assert response.json()["sources"][0]["decision_ids"]==["TDEC-20261003-002"]
        assert "database_source_id" not in response.text
    assert httpx.post(screen+"/assess",json={"call":"3NT"},headers=headers).status_code==422
    assert httpx.post(screen+"/assess",json={"call":"3H","school":"forged"},headers=headers).status_code==422


def test_cross_origin_and_dns_rebinding_refused(screen):
    import httpx,re
    page=httpx.get(screen)
    nonce=re.search(r'nonce="([^"]+)"',page.text).group(1)
    assert httpx.get(screen,headers={"Host":"attacker.invalid"}).status_code==403
    for headers in ({},{"Origin":"https://attacker.invalid","X-Pilot-Nonce":nonce},
                    {"Origin":screen,"X-Pilot-Nonce":"wrong"},{"Origin":"null","X-Pilot-Nonce":nonce}):
        result=httpx.post(screen+"/assess",json={"call":"3H"},headers=headers)
        assert result.status_code==403 and "access-control-allow-origin" not in result.headers


def test_auth_is_required_before_any_database_access(monkeypatch):
    import app as entrypoint
    from bridge_school_api.main import require_api_token
    entrypoint.app.dependency_overrides.pop(require_api_token,None)
    monkeypatch.setenv("BRIDGE_API_TOKEN","synthetic-test-token")
    def forbidden(): raise AssertionError("Unauthenticated SQL")
    monkeypatch.setattr(teacher,"connect",forbidden)
    with TestClient(entrypoint.app) as client:
        path='/v1/ai/positions/00000000-0000-4000-8000-000000000001/teacher-evidence'
        assert client.post(path,json=envelope("3H")).status_code==401
        assert client.post(path,json=envelope("3H"),headers={"Authorization":"Bearer wrong"}).status_code==403


def test_entrypoint_wraps_once_and_preserves_legacy_writer(monkeypatch):
    import app as entrypoint
    path="/v1/ai/positions/{position_id}/teacher-evidence"
    def paths(router):
        for route in router.routes:
            if hasattr(route,"original_router"):
                yield from paths(route.original_router)
            else:
                yield getattr(route,"path",None)
    assert list(paths(entrypoint.app)).count(path)==1
    calls=[]
    def legacy(pid,evidence):
        calls.append((pid,evidence.teacher_key,evidence.action))
        return {"legacy":True}
    monkeypatch.setattr(teacher,"record_teacher_evidence",legacy)
    monkeypatch.setenv("BRIDGE_API_TOKEN","synthetic-test-token")
    with TestClient(entrypoint.app) as client:
        response=client.post(path.replace("{position_id}",str(UUID(int=1))),
            headers={"Authorization":"Bearer synthetic-test-token"},json={"teacher_key":"BEN_DEFAULT","action":"PASS"})
    assert response.json()=={"legacy":True} and calls==[(UUID(int=1),"BEN_DEFAULT","PASS")]


@pytest.mark.parametrize("change",[{"stable_key":"other"},{"hand_pbn":"2.234.2345.23456"},
    {"dealer":"E","seat":"W"},{"source_id":None}])
def test_stored_canary_guard_precedes_catalog(monkeypatch,change):
    from contextlib import contextmanager
    pos=position(SCHOOL,changes=change)
    class Connection:
        def execute(self,*args): return self
        def fetchone(self): return pos
    @contextmanager
    def connection(): yield Connection()
    monkeypatch.setattr(teacher,"connect",connection)
    def forbidden(*args): raise AssertionError("Other position read catalog")
    monkeypatch.setattr(teacher,"read_school_catalog",forbidden)
    answer=teacher.answer(UUID(int=1),teacher.TeacherEvidence(**envelope("3H")))
    assert answer["status"]=="ABSTAIN" and answer["reason"]=="PILOT_POSITION_ONLY"


def test_redirect_cannot_forward_live_credential():
    from .screen import NoRedirect,live_sender
    assert NoRedirect().redirect_request(None,None,302,"",{},"https://attacker.invalid") is None
    with pytest.raises(ValueError): live_sender(str(UUID(int=1)),"")


@pytest.mark.parametrize("change",[{"source_bindings":[]},{"proposed_call":"3S"},
    {"teacher_version":"stale"},{"scope_key":"other"},{"queued":True},{"persisted":0},
    {"observed_shape":{"S":1,"H":3,"D":4,"C":5}}])
def test_screen_rejects_unbound_positive_answer(change):
    from .screen import public_answer
    from .package import catalog as fixture_catalog
    result=teacher.evaluate(position(UUID(int=101)),"3H",[fixture_catalog("3H")])
    assert public_answer(result,"3H")["status"]=="SUPPORTED"
    with pytest.raises(ValueError): public_answer(result|change,"3H")
    with pytest.raises(ValueError): public_answer({"status":"SUPPORTED","persisted":False},"3H")


def test_actual_redirect_never_reaches_destination(monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    from urllib.error import HTTPError
    from . import screen as module
    reached=[]
    class Target(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            reached.append(self.headers.get("Authorization"))
            self.send_response(200);self.end_headers()
        do_POST=do_GET
    with ThreadingHTTPServer(("127.0.0.1",0),Target) as target:
        class Redirect(Target):
            def do_POST(self):
                assert self.headers.get("Authorization")=="Bearer synthetic-token"
                self.send_response(302)
                self.send_header("Location",f"http://127.0.0.1:{target.server_port}/capture")
                self.end_headers()
        with ThreadingHTTPServer(("127.0.0.1",0),Redirect) as redirect:
            workers=[Thread(target=s.serve_forever,daemon=True) for s in (target,redirect)]
            for worker in workers: worker.start()
            try:
                monkeypatch.setattr(module,"UPSTREAM",f"http://127.0.0.1:{redirect.server_port}")
                with pytest.raises(HTTPError) as error:
                    module.live_sender(str(UUID(int=1)),"synthetic-token")("3H")
                assert error.value.code==302 and reached==[]
            finally:
                target.shutdown();redirect.shutdown()
                for worker in workers: worker.join(timeout=5)
