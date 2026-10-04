"""Anonymous loopback HTTP protocol qualification; never a live channel receipt."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha1, sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
import pytest
from .github_channels import (GitHubREST, OwnerObservation, CreateOnlyClaims, ClaimUncertain,
                              PREFIX, WORKFLOW, REPO, canonical)
from .resident_preflight import Refused
from .test_fixed_adapter import launch, C
from .candidate_readiness import cancellation, CUTOFF


class FixtureChannel:
    evidence_class = "synthetic_loopback_http"
    def __init__(self, server):
        self.origin = "http://127.0.0.1:" + str(server.server_port)
    def request(self, method, path, body=None):
        request = Request(self.origin + path, method=method,
            data=canonical(body) if body is not None else None)
        try:
            with urlopen(request, timeout=3) as result:
                raw = result.read(4_000_001)
                status = result.status
        except HTTPError as error:
            raw, status = b"", error.code
        return {"status": status, "data": json.loads(raw) if raw else None,
                "original_record_sha256": sha256(raw).hexdigest(), "request_id": "fixture-http"}
    def get(self, path):
        return self.request("GET", path)
    def _create(self, path, body):
        return self.request("POST", path, body)


@pytest.fixture
def channel():
    state = {"objects": {}, "refs": {}, "lock": threading.Lock(), "fault": None, "main": C,
        "posts": 0, "run": {"id": 456, "head_sha": C, "head_branch": "main",
            "event": "workflow_dispatch", "run_attempt": 1, "status": "completed",
            "conclusion": "success", "path": WORKFLOW, "actor": {"login": "olegmed1-art"},
            "triggering_actor": {"login": "olegmed1-art"}, "repository": {"full_name": REPO}},
        "job": {"name": "canon-owner-probe", "run_id": 456, "run_attempt": 1,
            "status": "completed", "conclusion": "success", "steps": [{
                "name": "Inventory existing owner connection without writes", "conclusion": "success"}]}}
    def reference(ref, sha):
        return {"ref": ref, "object": {"type": "commit", "sha": sha,
            "url": "https://api.github.com" + PREFIX + "/git/commits/" + sha}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, status, value=None):
            raw = canonical(value) if value is not None else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            path = self.path
            if path == PREFIX + "/branches/main":
                return self.reply(200, {"commit": {"sha": state["main"]}})
            if path == PREFIX + "/actions/runs/456":
                return self.reply(200, state["run"])
            if path == PREFIX + "/actions/runs/456/jobs?per_page=100":
                return self.reply(200, {"total_count": 1, "jobs": [state["job"]]})
            if "/git/matching-refs/" in path:
                ref = "refs/" + path.split("/git/matching-refs/")[1]
                values = [reference(k, v) for k, v in state["refs"].items() if k.startswith(ref)]
                if state["fault"] == "readback":
                    values = []
                return self.reply(200, values)
            if "/git/commits/" in path:
                sha = path.rsplit("/", 1)[-1]
                return self.reply(200, state["objects"][sha])
            self.reply(404)
        def do_POST(self):
            state["posts"] += 1
            if state["fault"] == "permission":
                return self.reply(403)
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            part = self.path.rsplit("/", 1)[-1]
            if part == "blobs":
                raw = base64.b64decode(data["content"])
                sha = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                return self.reply(201, {"sha": sha})
            if part == "trees":
                assert len(data["tree"]) == 1
                entry = data["tree"][0]
                assert (entry["path"], entry["mode"], entry["type"]) == ("claim.json", "100644", "blob")
                raw = b"100644 claim.json\0" + bytes.fromhex(entry["sha"])
                sha = sha1(b"tree " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                return self.reply(201, {"sha": sha})
            if part == "commits":
                assert data["parents"] == []
                sha = sha1(canonical(data)).hexdigest()
                state["objects"][sha] = {"sha": sha, "message": data["message"],
                                         "parents": [], "tree": {"sha": data["tree"]}}
                return self.reply(201, {"sha": sha})
            if part == "refs":
                assert data["ref"].startswith("refs/canon-claims/")
                with state["lock"]:
                    if data["ref"] in state["refs"]:
                        return self.reply(422)
                    state["refs"][data["ref"]] = data["sha"]
                if state["fault"] == "lost_after_commit":
                    self.connection.shutdown(2)
                    self.connection.close()
                    return
                return self.reply(201, reference(data["ref"], data["sha"]))
            self.reply(404)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield FixtureChannel(server), state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_authenticated_inventory_interface_does_not_admit_recovery(channel):
    ch, _ = channel
    proof = OwnerObservation(ch).inventory(C, 456)
    assert proof["source_transport"] == "synthetic_loopback_http"
    assert proof["status"] == "OWNER_INVENTORY_PROVENANCE"
    assert proof["write_admission"] is proof["recovery_ready"] is proof["watcher_24h_installed"] is False
    assert len(proof["originals"]) == 4


@pytest.mark.parametrize("field,value", [
    ("event", "push"), ("run_attempt", 2), ("head_sha", "0"*40),
    ("head_branch", "review/test"), ("conclusion", "failure"),
    ("path", ".github/workflows/unreviewed.yml"), ("actor", {"login": "other"}),
    ("triggering_actor", {"login": "other"}), ("repository", {"full_name": "other/repo"})])
def test_actual_http_observation_rejects_wrong_provenance(channel, field, value):
    ch, state = channel
    state["run"][field] = value
    with pytest.raises(Refused):
        OwnerObservation(ch).inventory(C, 456)


def test_changed_main_refuses(channel):
    ch, state = channel
    state["main"] = "0"*40
    with pytest.raises(Refused, match="main_changed"):
        OwnerObservation(ch).inventory(C, 456)


def test_create_only_reservation_and_stage_readbacks(channel):
    ch, state = channel
    plan = launch(uuid4())
    claims = CreateOnlyClaims(ch)
    assert claims.reserve(plan)["status"] == "DURABLE_CLAIM"
    assert claims.inspect_reservation(plan)["execution_admission"] is False
    assert claims.stage(plan, "baseline")["status"] == "STAGE_CLAIM"
    assert len(state["refs"]) == 3
    with pytest.raises(ClaimUncertain):
        claims.stage(plan, "baseline")
    with pytest.raises(ClaimUncertain):
        claims.reserve(plan)
    assert len(state["refs"]) == 3


def test_stage_requires_complete_exact_reservation_before_any_post(channel):
    ch, state = channel
    with pytest.raises(ClaimUncertain):
        CreateOnlyClaims(ch).stage(launch(uuid4()), "baseline")
    assert state["posts"] == 0


@pytest.mark.parametrize("field,value", [
    ("runtime_sha", "0"*40), ("controller_run_id", 789),
    ("validation_build_id", "dpl_AnotherBuild123"), ("plan_hash", "0"*64)])
def test_stage_rejects_other_tuple(channel, field, value):
    ch, state = channel
    plan = launch(uuid4())
    claims = CreateOnlyClaims(ch)
    claims.reserve(plan)
    before = state["posts"]
    with pytest.raises(ClaimUncertain):
        claims.stage(replace(plan, **{field: value}), "baseline")
    assert state["posts"] == before


def test_partial_global_claim_never_admits(channel):
    ch, state = channel
    original = launch(uuid4())
    claims = CreateOnlyClaims(ch)
    claims.reserve(original)
    other = replace(original, intent=str(uuid4()), controller_run_id=789)
    with pytest.raises(ClaimUncertain):
        claims.reserve(other)
    assert len(state["refs"]) == 3  # Permanently consumes intent, conflicting build stays original.
    with pytest.raises(Refused):
        claims.inspect_reservation(other)


def test_atomic_parallel_one_intent(channel):
    ch, state = channel
    plan = launch(uuid4())
    def reserve():
        try:
            return CreateOnlyClaims(ch).reserve(plan)["status"]
        except ClaimUncertain:
            return "REFUSED"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(results) == ["DURABLE_CLAIM", "REFUSED"]
    assert len(state["refs"]) == 2


@pytest.mark.parametrize("fault", ["permission", "readback", "lost_after_commit"])
def test_http_failure_or_uncertain_claim_no_replay(channel, fault):
    ch, state = channel
    state["fault"] = fault
    with pytest.raises(ClaimUncertain, match="no_replay"):
        CreateOnlyClaims(ch).reserve(launch(uuid4()))


@pytest.mark.parametrize("path", [
    "https://other.example/", PREFIX + "/contents/private.env",
    PREFIX + "/git/ref/heads/main", PREFIX + "/git/refs/heads/main",
    PREFIX + "/actions/runs/456/jobs?per_page=100&page=2", PREFIX + "/branches/main?redirect=1",
    "/repos/other/repo/branches/main"])
def test_native_fixed_transport_rejects_path_before_network(path):
    transport = GitHubREST("anonymous-fixture-capability")
    with pytest.raises(Refused, match="path_or_method"):
        transport.get(path)
    assert transport.calls == 0
    assert "anonymous-fixture-capability" not in repr(transport)


@pytest.mark.parametrize("method", ["PATCH", "DELETE", "PUT"])
def test_native_fixed_transport_has_no_ref_update_or_delete(method):
    transport = GitHubREST("anonymous-fixture-capability")
    with pytest.raises(Refused):
        transport._send(method, PREFIX + "/git/refs", {})
    assert transport.calls == 0


@pytest.mark.parametrize("now", [CUTOFF-timedelta(minutes=20), CUTOFF,
                                CUTOFF+timedelta(minutes=1), CUTOFF+timedelta(days=1)])
def test_candidate_cancellation_no_extension_or_rollover(now):
    result = cancellation(now)
    assert result["status"] == "CANDIDATE_CANCELLED"
    assert result["admission"] is result["automatic_reschedule"] is False
    assert result["candidate_window"] == ["2026-10-05T00:00:00Z", "2026-10-05T00:05:00Z"]
    assert len(result["missing"]) == 6


def test_candidate_rejects_naive_or_non_utc_time():
    with pytest.raises(ValueError):
        cancellation(datetime(2026, 10, 4, 23, 55))
    with pytest.raises(ValueError):
        cancellation(CUTOFF.astimezone(timezone(timedelta(hours=1))))


@pytest.mark.parametrize("path,body", [
    (PREFIX+"/git/refs", {"ref":"refs/heads/main","sha":"a"*40}),
    (PREFIX+"/git/refs", {"ref":"refs/tags/release","sha":"a"*40}),
    (PREFIX+"/git/trees", {"tree":[{"path":".github/workflows/live.yml","mode":"100644","type":"blob","sha":"a"*40}]}),
    (PREFIX+"/git/commits", {"message":"canon-claim "+"a"*64,"tree":"a"*40,"parents":["b"*40]}),
    (PREFIX+"/git/blobs", {"content":base64.b64encode(b'{"private":"value"}').decode(),"encoding":"base64"}),
])
def test_native_post_surface_rejects_main_workflows_or_arbitrary_blob(path, body):
    transport=GitHubREST("anonymous-fixture-capability")
    with pytest.raises(Refused):
        transport._create(path,body)
    assert transport.calls==0
