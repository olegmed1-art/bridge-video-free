"""Synthetic only: fake client/process/GitHub responses; never import DB runtime."""
import ast
import copy
import hashlib
import io
import json
import logging
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch
import zipfile

import bootstrap
import ci
import observer
import probe
import protocol

SHA = "a" * 40
CANARY = "SYNTHETIC_SECRET_MUST_NOT_ESCAPE"
HERE = Path(__file__).resolve().parent
VERSIONS = {"psycopg": "3.3.4", "binary": "3.3.4", "typing_extensions": "4.15.0",
            "implementation": "binary", "python": [3, 12, 3], "libpq": 180001, "build_libpq": 180001}


def offline_evidence():
    return {"schema": "incident-1911-offline-v2", "head_sha": SHA, "status": "pass", "tests": 59,
            "failures": 0, "errors": 0, "network_namespace_isolated": True, "nonroot": True,
            "secret_present": False, "socket_attempts": 0, "native_connect_calls": 0, "runtime": dict(VERSIONS)}


def env(stage):
    branch, number = (probe.PREFLIGHT_BRANCH, "1") if stage == "preflight" else (probe.LIVE_BRANCH, "2")
    return {"GITHUB_REPOSITORY": probe.REPO, "GITHUB_EVENT_NAME": "create", "GITHUB_SHA": SHA,
            "GITHUB_REF_NAME": branch, "GITHUB_REF": "refs/heads/" + branch,
            "GITHUB_RUN_NUMBER": number, "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW_SHA": SHA,
            "GITHUB_EVENT_PATH": "/synthetic/event.json"}


def evidence():
    run = {"id": 1234, "repository": {"full_name": probe.REPO}, "head_sha": SHA,
           "head_branch": probe.PREFLIGHT_BRANCH, "event": "create", "run_number": 1,
           "run_attempt": 1, "status": "completed", "conclusion": "success", "name": ci.WORKFLOW_NAME,
           "path": ".github/workflows/" + probe.WORKFLOW}
    job = {"name": "Offline preflight", "head_sha": SHA, "run_attempt": 1, "status": "completed",
           "conclusion": "success", "steps": [{"name": name, "conclusion": "success"} for name in
              ("Bootstrap pinned public artifacts", "Offline compatibility without network")]}
    return {"total_count": 1, "workflow_runs": [run]}, {"total_count": 1, "jobs": [job]}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.observer = observer.AttemptObserver()
        self.state = probe.base_result(self.observer)
        self.publish = Mock()

    def test_single_success_closes_without_sql(self):
        conn = Mock(spec=["close"])
        client = Mock(spec=["connect"])
        client.connect.return_value = conn
        probe.connect_once(client, CANARY, self.state, self.publish)
        client.connect.assert_called_once_with(CANARY, **probe.CONNECT_KWARGS)
        conn.close.assert_called_once_with()
        self.assertTrue(self.state["public_call_returned"])
        self.assertTrue(self.state["close_succeeded"])
        self.assertNotIn(CANARY, json.dumps(self.state))

    def test_native_parameters_unchanged(self):
        self.assertEqual(probe.CONNECT_KWARGS, {"connect_timeout": 10,
            "application_name": "incident-1911-readonly-probe",
            "options": "-c default_transaction_read_only=on -c statement_timeout=5000"})

    def test_failure_never_retries(self):
        client = Mock(spec=["connect"])
        client.connect.side_effect = RuntimeError("Connection refused " + CANARY)
        probe.connect_once(client, CANARY, self.state, self.publish)
        self.assertEqual(client.connect.call_count, 1)
        self.assertFalse(self.state["public_call_returned"])
        self.assertEqual(self.state["failure_hints"], ["tcp_refused"])
        self.assertNotIn(CANARY, json.dumps(self.state))

    def test_cleanup_failure_does_not_retry_connect(self):
        conn = Mock(spec=["close"])
        conn.close.side_effect = RuntimeError(CANARY)
        client = Mock(spec=["connect"])
        client.connect.return_value = conn
        probe.connect_once(client, CANARY, self.state, self.publish)
        self.assertEqual(client.connect.call_count, 1)
        self.assertEqual(conn.close.call_count, 1)
        self.assertEqual(self.state["status"], "cleanup_failed")
        self.assertTrue(self.state["public_call_returned"])
        self.assertFalse(self.state["close_succeeded"])

    def test_post_success_log_failure_is_not_public_return(self):
        client = Mock(spec=["connect"])
        def broken(*args, **kwargs):
            for msg in (observer.START, observer.SUCCEEDED):
                self.observer.handle(logging.LogRecord("psycopg", 10, "synthetic", observer.EVENTS[msg][0],
                    msg, ("host: 'synthetic.invalid', port: '5432', hostaddr: '203.0.113.7'",), None, "connect"))
            raise RuntimeError("synthetic postprocessing")
        client.connect.side_effect = broken
        probe.connect_once(client, CANARY, self.state, self.publish)
        self.assertFalse(self.state["public_call_returned"])
        self.assertEqual(self.observer.snapshot()["attempts"][0]["outcome"], "attempt_succeeded")

    def test_no_arbitrary_exception_string_escape(self):
        class Broken(Exception):
            def __str__(self):
                raise ValueError(CANARY)
        self.assertEqual(probe.message_hints(Broken()), ["unknown"])

    def test_atomic_roundtrip(self):
        path = HERE / ".test-safe-result.json"
        self.addCleanup(path.unlink, missing_ok=True)
        self.addCleanup(path.with_suffix(".tmp").unlink, missing_ok=True)
        protocol.atomic_result(path, self.state)
        self.assertEqual(protocol.read_result(path), self.state)

    def test_atomic_replace_failure_leaves_previous_snapshot(self):
        path = HERE / ".test-safe-interruption.json"
        self.addCleanup(path.unlink, missing_ok=True)
        self.addCleanup(path.with_suffix(".tmp").unlink, missing_ok=True)
        protocol.atomic_result(path, self.state)
        newer = copy.deepcopy(self.state)
        newer["status"] = "connecting"
        newer["connect_calls"] = 1
        with patch.object(Path, "replace", side_effect=OSError(CANARY)):
            with self.assertRaises(OSError):
                protocol.atomic_result(path, newer)
        self.assertEqual(protocol.read_result(path), self.state)

    def test_unknown_output_keys_are_rejected(self):
        self.state["raw_exception"] = CANARY
        self.assertFalse(protocol.valid_result(self.state))

    def test_bad_output_values_are_rejected_without_throwing(self):
        for bad in (CANARY, {}, [], True, None, 42):
            data = copy.deepcopy(self.state)
            data["status"] = bad
            self.assertFalse(protocol.valid_result(data))

    def test_booleans_not_accepted_as_counters(self):
        self.state["connect_calls"] = True
        self.assertFalse(protocol.valid_result(self.state))

    def test_runtime_versions_are_fixed(self):
        self.assertTrue(protocol.valid_runtime(VERSIONS))
        for key in ("psycopg", "binary", "typing_extensions", "implementation", "libpq"):
            data = dict(VERSIONS)
            data[key] = CANARY
            self.assertFalse(protocol.valid_runtime(data))

    def test_raw_family_and_hint_rejected(self):
        row = {"index": 1, "family": CANARY, "outcome": "failed", "duration_ms": 1, "hints": ["unknown"]}
        self.state["observations"]["attempts"] = [row]
        self.state["observations"]["observed_starts"] = 1
        self.assertFalse(protocol.valid_result(self.state))

    def test_output_read_size_is_bounded(self):
        with patch.object(Path, "open") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b"x" * (protocol.MAX_BYTES + 1)
            self.assertIsNone(protocol.read_result("synthetic"))
            opened.return_value.__enter__.return_value.read.assert_called_once_with(protocol.MAX_BYTES + 1)

    def test_missing_output_is_unknown(self):
        with patch.object(Path, "open", side_effect=FileNotFoundError):
            self.assertIsNone(protocol.read_result("synthetic"))

    def test_supervisor_normal_exit_one_process(self):
        process = Mock()
        factory = Mock(return_value=process)
        with patch.object(probe, "read_result", return_value=None):
            result = probe.supervise(["synthetic"], "unused", {}, factory)
        factory.assert_called_once_with(["synthetic"], env={}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.wait.assert_called_once_with(timeout=20)
        process.terminate.assert_not_called()
        self.assertFalse(result["diagnostic_complete"])

    def test_supervisor_term_at_20s_preserves_snapshot(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 20), 0]
        with patch.object(probe, "read_result", return_value=self.state):
            result = probe.supervise([], "unused", {}, Mock(return_value=process))
        self.assertEqual(result["process_outcome"], "terminated_at_20s")
        self.assertFalse(result["diagnostic_complete"])
        self.assertIs(result["snapshot"], self.state)
        process.terminate.assert_called_once()
        process.kill.assert_not_called()
        self.assertEqual([x.kwargs for x in process.wait.call_args_list], [{"timeout": 20}, {"timeout": 5}])

    def test_supervisor_kill_after_grace_no_restart(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 20), subprocess.TimeoutExpired("synthetic", 5), 0]
        factory = Mock(return_value=process)
        with patch.object(probe, "read_result", return_value=self.state):
            result = probe.supervise([], "unused", {}, factory)
        process.kill.assert_called_once()
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(result["process_outcome"], "killed_after_grace")

    def test_incomplete_observer_never_complete_report(self):
        self.state["status"] = "connect_failed"
        self.state["observations"]["contract_errors"] = 1
        with patch.object(probe, "read_result", return_value=self.state):
            result = probe.supervise([], "unused", {}, Mock(return_value=Mock()))
        self.assertFalse(result["diagnostic_complete"])

    def test_complete_return_requires_successful_close_and_exit(self):
        self.state.update(status="call_returned", connect_calls=1, public_call_returned=True,
                          close_attempted=True, close_succeeded=True)
        for code, closed, expected in ((0, True, True), (1, True, False), (-9, True, False), (0, False, False)):
            self.state["close_succeeded"] = closed
            process = Mock()
            process.wait.return_value = code
            with patch.object(probe, "read_result", return_value=self.state):
                result = probe.supervise([], "unused", {}, Mock(return_value=process))
            self.assertEqual(result["diagnostic_complete"], expected)

    def test_completed_failed_connection_is_diagnostic_not_health_success(self):
        self.state.update(status="connect_failed", connect_calls=1, failure_hints=["unknown"])
        process = Mock()
        process.wait.return_value = 1
        with patch.object(probe, "read_result", return_value=self.state):
            result = probe.supervise([], "unused", {}, Mock(return_value=process))
        self.assertTrue(result["diagnostic_complete"])
        self.assertFalse(result["snapshot"]["public_call_returned"])

    def test_both_exact_stage_contexts(self):
        for stage in ("preflight", "live"):
            self.assertTrue(probe.context_allowed(env(stage), stage))

    def test_context_drift_rerun_wrong_ref_or_sha_rejected(self):
        for key, value in (("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_NUMBER", "3"), ("GITHUB_WORKFLOW_SHA", "b"*40),
                           ("GITHUB_REF", "refs/heads/main"), ("GITHUB_EVENT_NAME", "push"), ("GITHUB_REPOSITORY", "other/repo")):
            data = env("live")
            data[key] = value
            self.assertFalse(probe.context_allowed(data, "live"))

    def test_bad_live_context_never_spawns(self):
        with patch.dict(probe.os.environ, {}, clear=True), patch.object(probe, "supervise") as launch:
            result, code = probe.live()
        launch.assert_not_called()
        self.assertEqual(code, 1)

    def test_missing_gate_prevents_child(self):
        with patch.dict(probe.os.environ, env("live"), clear=True), \
             patch.object(Path, "read_text", side_effect=FileNotFoundError), patch.object(probe, "supervise") as launch:
            result, code = probe.live()
        launch.assert_not_called()
        self.assertEqual(code, 1)

    def test_second_claim_prevents_child(self):
        receipt = {"schema": "incident-1911-gate-v2", "head_sha": SHA, "preflight_passed": True}
        with patch.dict(probe.os.environ, env("live"), clear=True), \
             patch.object(Path, "read_text", side_effect=[json.dumps(receipt), json.dumps(offline_evidence())]), \
             patch.object(Path, "open", side_effect=FileExistsError) as claim, patch.object(probe, "supervise") as launch:
            result, code = probe.live()
        claim.assert_called_once_with("x")
        launch.assert_not_called()
        self.assertEqual(code, 1)

    def test_valid_live_gate_passes_secret_in_environment_only(self):
        receipt = {"schema": "incident-1911-gate-v2", "head_sha": SHA, "preflight_passed": True}
        data = env("live")
        data.update(BRIDGE_APP_DATABASE_URL=CANARY, HOME="synthetic_home", SSL_CERT_FILE="synthetic_cert")
        with patch.dict(probe.os.environ, data, clear=True), \
             patch.object(Path, "read_text", side_effect=[json.dumps(receipt), json.dumps(offline_evidence())]), \
             patch.object(Path, "open") as claim, patch.object(Path, "exists", return_value=False), \
             patch.object(probe, "supervise", return_value={"diagnostic_complete": True}) as launch:
            result, code = probe.live()
        launch.assert_called_once()
        command, output, child_env = launch.call_args.args
        self.assertNotIn(CANARY, str(command))
        self.assertEqual(child_env["BRIDGE_APP_DATABASE_URL"], CANARY)
        self.assertEqual(child_env["HOME"], "synthetic_home")
        self.assertEqual(child_env["SSL_CERT_FILE"], "synthetic_cert")
        self.assertEqual(child_env["VERCEL_ENV"], "production")
        self.assertEqual(code, 0)

    def test_create_payload_guard(self):
        value = {"ref_type": "branch", "ref": probe.LIVE_BRANCH, "repository": {"full_name": probe.REPO}}
        self.assertTrue(ci.event_allowed(value, "live"))
        value["ref_type"] = "tag"
        self.assertFalse(ci.event_allowed(value, "live"))

    def test_preflight_evidence_exact_pass(self):
        runs, jobs = evidence()
        self.assertTrue(ci.validate_preflight(runs, jobs, SHA))

    def test_skipped_live_job_is_only_optional_row(self):
        runs, jobs = evidence()
        jobs["jobs"].append({"name": "Live once", "head_sha": SHA, "run_attempt": 1, "status": "completed", "conclusion": "skipped"})
        jobs["total_count"] = 2
        self.assertTrue(ci.validate_preflight(runs, jobs, SHA))
        jobs["jobs"][1]["conclusion"] = "success"
        self.assertFalse(ci.validate_preflight(runs, jobs, SHA))

    def test_preflight_run_ambiguity_and_drift_rejected(self):
        for key, value in (("head_sha", "b"*40), ("head_branch", "main"), ("run_attempt", 2),
                           ("run_number", 2), ("conclusion", "failure"), ("status", "in_progress"),
                           ("event", "workflow_dispatch"), ("path", "other.yml"), ("name", "Other")):
            runs, jobs = evidence()
            runs["workflow_runs"][0][key] = value
            self.assertFalse(ci.validate_preflight(runs, jobs, SHA))
        runs, jobs = evidence()
        runs["total_count"] = 2
        self.assertFalse(ci.validate_preflight(runs, jobs, SHA))

    def test_preflight_extra_or_incomplete_job_rejected(self):
        runs, jobs = evidence()
        jobs["jobs"][0]["steps"][0]["conclusion"] = "skipped"
        self.assertFalse(ci.validate_preflight(runs, jobs, SHA))
        runs, jobs = evidence()
        jobs["jobs"].append({"name": "Unknown"})
        jobs["total_count"] = 2
        self.assertFalse(ci.validate_preflight(runs, jobs, SHA))

    def test_gate_rejects_secret_before_any_fetch(self):
        data = env("live")
        data["BRIDGE_APP_DATABASE_URL"] = CANARY
        fetch = Mock()
        with self.assertRaises(ValueError):
            ci.gate(data, fetch=fetch)
        fetch.assert_not_called()

    def test_gate_public_metadata_failure_does_not_write_receipt(self):
        data = env("live")
        event = {"ref_type": "branch", "ref": probe.LIVE_BRANCH, "repository": {"full_name": probe.REPO}}
        with patch.object(Path, "read_text", side_effect=[json.dumps(event), json.dumps(offline_evidence())]), \
             patch.object(Path, "write_text") as write:
            with self.assertRaises(OSError):
                ci.gate(data, fetch=Mock(side_effect=OSError("rate_limited")))
            write.assert_not_called()

    def test_gate_writes_only_fixed_receipt_after_two_gets(self):
        runs, jobs = evidence()
        event = {"ref_type": "branch", "ref": probe.LIVE_BRANCH, "repository": {"full_name": probe.REPO}}
        fetch = Mock(side_effect=[runs, jobs])
        with patch.object(Path, "read_text", side_effect=[json.dumps(event), json.dumps(offline_evidence())]), \
             patch.object(Path, "write_text") as write:
            result = ci.gate(env("live"), fetch=fetch)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(set(result), {"schema", "head_sha", "preflight_passed"})
        self.assertTrue(result["preflight_passed"])
        write.assert_called_once()

    def test_bootstrap_rejects_secret_and_never_downloads(self):
        data = env("preflight")
        data["BRIDGE_APP_DATABASE_URL"] = CANARY
        fetch = Mock()
        with self.assertRaises(ValueError):
            bootstrap.stage(data, fetch=fetch)
        fetch.assert_not_called()

    def test_public_download_integrity_and_no_retry(self):
        url = "https://synthetic.invalid/wheel"
        data = b"synthetic public package"
        response = Mock(status=200)
        response.geturl.return_value = url
        response.read.return_value = data
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(bootstrap.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(bootstrap.download(url, hashlib.sha256(data).hexdigest(), len(data)), data)
        opener.open.assert_called_once_with(url, timeout=5)
        response.read.assert_called_once_with(len(data) + 1)

    def test_public_download_rejects_redirect_hash_size_and_status(self):
        url = "https://synthetic.invalid/wheel"
        data = b"public"
        for actual_url, status, actual_data in ((url + "/redirect", 200, data), (url, 404, data),
                                                (url, 200, b"wrong!"), (url, 200, data + b"!")):
            response = Mock(status=status)
            response.geturl.return_value = actual_url
            response.read.return_value = actual_data
            opener = Mock()
            opener.open.return_value.__enter__ = Mock(return_value=response)
            opener.open.return_value.__exit__ = Mock(return_value=False)
            with patch.object(bootstrap.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(ValueError):
                    bootstrap.download(url, hashlib.sha256(data).hexdigest(), len(data))
            self.assertEqual(opener.open.call_count, 1)
        with self.assertRaises(ValueError):
            bootstrap.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_wheel_path_traversal_rejected_before_write(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("../outside.py", "bad")
        with self.assertRaises(ValueError):
            bootstrap.extract_wheel(data.getvalue(), HERE)

    def test_wheel_symlink_rejected(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            member = zipfile.ZipInfo("badlink")
            member.external_attr = 0o120777 << 16
            z.writestr(member, "outside")
        with self.assertRaises(ValueError):
            bootstrap.extract_wheel(data.getvalue(), HERE)

    def test_single_connect_site_and_no_sql_or_client_patch(self):
        tree = ast.parse(Path(probe.__file__).read_text())
        connects = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"]
        self.assertEqual(len(connects), 1)
        forbidden = {"execute", "executemany", "cursor", "commit", "rollback", "settrace", "getaddrinfo"}
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in forbidden for n in ast.walk(tree)))
        self.assertFalse(any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "connect" for t in n.targets) for n in ast.walk(tree)))

    def test_offline_root_is_rejected_before_runtime(self):
        with patch.object(ci.sys, "platform", "linux"), patch.object(ci.os, "getuid", return_value=0, create=True), \
             patch.object(probe, "runtime") as load:
            with self.assertRaises(ValueError):
                ci.offline("net:[1]", 1001, SHA)
        load.assert_not_called()

    def test_offline_receipt_rejects_incomplete_or_unsandboxed_proof(self):
        self.assertTrue(protocol.valid_offline(offline_evidence(), SHA))
        self.assertFalse(protocol.valid_offline({"status": "pass", "head_sha": SHA}, SHA))
        for key, value in (("nonroot", False), ("network_namespace_isolated", False), ("secret_present", True),
                           ("native_connect_calls", 1), ("socket_attempts", 1), ("errors", 1), ("runtime", None)):
            data = offline_evidence()
            data[key] = value
            self.assertFalse(protocol.valid_offline(data, SHA))


if __name__ == "__main__":
    unittest.main(verbosity=2)
