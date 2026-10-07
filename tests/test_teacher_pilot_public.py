"""Public synthetic storage/gate contracts only; no school canon or engine evidence."""
from copy import deepcopy
import unittest

from fastapi import HTTPException
from starlette.requests import Request

from bridge_school_api.teacher_pilot_http import PreviewGate, parse_body
from bridge_school_api.teacher_pilot_sessions import MemoryStore, PilotError, SessionService


class PublicRuntime:
    def __init__(self, turn=0):
        self.turn = turn

    def view(self):
        return {"version": "public-contract-fixture", "content_sha256": "a" * 64,
                "control_suite_version": "public-contract-fixture",
                "control_suite_sha256": "b" * 64, "turn": self.turn}

    def handle(self, request):
        self.turn += 1
        return self.view()

    def checkpoint(self):
        return {"turn": self.turn}


class PublicFactory:
    def create(self):
        return PublicRuntime()

    def restore(self, checkpoint):
        return PublicRuntime(checkpoint["turn"])


class PublicContractTests(unittest.TestCase):
    def test_synthetic_session_survives_service_instance_change(self):
        store = MemoryStore()
        service = SessionService(PublicFactory(), store)
        started = service.start()
        result = service.event(started["session_id"], {"turn": 0})
        self.assertEqual(result["session"]["turn"], 1)
        self.assertEqual(SessionService(PublicFactory(), store).read(started["session_id"]), result)

    def test_old_turn_never_calls_runtime_handle(self):
        store = MemoryStore()
        service = SessionService(PublicFactory(), store)
        sid = service.start()["session_id"]
        service.event(sid, {"turn": 0})
        with self.assertRaisesRegex(PilotError, "STALE_TURN"):
            service.event(sid, {"turn": 0})
        self.assertEqual(len(store.read(sid)["events"]), 1)

    def test_mutation_failure_does_not_write_memory_test_store(self):
        store = MemoryStore()
        service = SessionService(PublicFactory(), store)
        sid = service.start()["session_id"]
        original = store.read(sid)
        def fail(row):
            row["turn"] = 99
            raise PilotError("TEST_ONLY_FAILURE")
        with self.assertRaises(PilotError):
            store.mutate(sid, fail)
        self.assertEqual(store.read(sid), original)

    def test_checkpoint_session_binding_is_checked(self):
        store = MemoryStore()
        service = SessionService(PublicFactory(), store)
        first = service.start()["session_id"]
        second = service.start()["session_id"]
        store.rows[second]["checkpoint"] = deepcopy(store.rows[first]["checkpoint"])
        with self.assertRaisesRegex(PilotError, "CHECKPOINT_SESSION"):
            service.read(second)

    def test_production_gate_and_unapproved_branch_fail_closed(self):
        host = "public-contract-fixture.vercel.app"
        env = {"BRIDGE_TEACHER_PILOT_ENABLED": "1", "VERCEL_ENV": "production",
               "VERCEL_GIT_COMMIT_REF": "test/teacher-fixture", "VERCEL_GIT_COMMIT_SHA": "a" * 40,
               "VERCEL_URL": host}
        gate = PreviewGate("test/teacher-fixture", "a" * 40, host, env)
        request = Request({"type": "http", "method": "POST", "path": "/",
                           "headers": [(b"host", host.encode()),
                                       (b"origin", ("https://" + host).encode())]})
        for environment, branch in [("production", "test/teacher-fixture"), ("preview", "main")]:
            env["VERCEL_ENV"], env["VERCEL_GIT_COMMIT_REF"] = environment, branch
            with self.assertRaises(HTTPException) as caught:
                gate.check(request)
            self.assertEqual(caught.exception.status_code, 404)

    def test_json_parser_rejects_duplicates_nonfinite_and_excess_bytes(self):
        for value, status in [(b'{"a":1,"a":2}', 422), (b'{"a":Infinity}', 422),
                              (b" " * 1025, 413)]:
            with self.assertRaises(HTTPException) as caught:
                parse_body(value)
            self.assertEqual(caught.exception.status_code, status)


if __name__ == "__main__":
    unittest.main()
