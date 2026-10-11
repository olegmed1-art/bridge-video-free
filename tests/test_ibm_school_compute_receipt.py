"""Offline lifecycle/containment binding and narrow production workflow checks."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ops import ibm_school_compute_receipt as e
from ops import ibm_school_worlds_probe as p
from ops import ibm_worlds_validator as v
from tests.test_ibm_school_worlds_probe import B, RESULT, WORLDS, receipt


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with mock.patch.dict(os.environ, RUNNER_TEMP=str(self.root)):
            p.persist_receipt(receipt(), B, worlds=WORLDS, result=RESULT, independent=v.validate_result(RESULT))
        self.worlds = self.root / "ibm-school-worlds"
        self.control = self.root / "ibm-school-compute-control"
        self.control.mkdir(mode=0o700)
        at = {"at_utc": "2026-10-11T10:00:00.000001+00:00"}
        self.rows = [
            dict(event="SCHOOL_WORLDS_ROUTE_PREPARED", **B, **at, guest_network_requests=0),
            dict(event="START_SUBMITTING", mode="trial", max_window_seconds=600, running_window_seconds=None),
            dict(event="START_ACCEPTED"), dict(event="TRIAL_STATE", status="starting"),
            dict(event="TRIAL_STATE", status="running"),
            dict(event="SCHOOL_WORLDS_STARTED", **B, max_seconds=378, seconds_since_start=5.1,
                 stop_submit_by_seconds=420, confirmation_reserve_seconds=180),
            dict(event="SCHOOL_WORLDS_CAPTURED", **B, **at, remote_exit=0, stdout_bytes=24000,
                 stdout_sha256="a" * 64, stderr_bytes=0, stderr_sha256=e.EMPTY_SHA, raw_output_exported=False),
            dict(event="SCHOOL_WORLDS_VERIFIED_RECEIPT", **B, **at,
                 receipt=p.safe_receipt(receipt()), artifact_path=str(self.worlds)),
            dict(event="SCHOOL_WORLDS_RESULT", generated=True),
            dict(event="TRIAL_CONTAINMENT", reason="school_worlds_complete"),
            dict(event="STOP_STATE", status="running", startable=False, action_queue=e.QUEUE),
            dict(event="ORDINARY_STOP_SUBMITTING"), dict(event="ORDINARY_STOP_ACCEPTED"),
            *[dict(event="STOP_STATE", status="stopped", startable=True, action_queue=e.QUEUE) for _ in range(3)],
            dict(event="STOPPED_OBSERVED", action_queue=e.QUEUE, stop_post_unknown=False),
        ]

    def raw(self, rows=None):
        return b"".join(v.canonical_json_bytes(row) for row in (self.rows if rows is None else rows))

    def check(self, rows=None, **kw):
        return e.build_receipt(self.raw(rows), B, self.worlds, kw.get("controller_exit", 0), kw.get("capture_exit", 0))

    def test_valid_compute_and_containment_bind_all_five_artifacts(self):
        value = self.check()
        self.assertEqual(("COMPUTE_CONTAINED", "OK"), (value["result"], value["reason"]))
        self.assertEqual(B, {key: value[key] for key in B})
        self.assertEqual(5, len(value["files"]))
        self.assertEqual(3, value["provider_stop"]["consecutive_stopped_startable"])
        self.assertEqual(e.QUEUE, value["provider_stop"]["action_queue"])
        self.assertFalse(value["raw_logs_exported"])
        self.assertEqual("NOT_ESTABLISHED_BY_THIS_RECEIPT", value["artifact_delivery"])

    def test_real_controller_trace_with_offline_provider_matches_parser(self):
        from ops import ibm_trial_executor as controller
        from tests.test_ibm_trial_executor import Clock
        clock = Clock()
        fixtures = self.rows
        class Provider:
            state_value = "stopped"
            def backup(self): return dict(p.IDENTITY)
            def state(self): return self.state_value, self.state_value == "stopped"
            def action(self, action): self.state_value = "running" if action == "start" else "stopped"
        class Guest:
            kind = "school_worlds"
            binding = B
            def bind_identity(self, identity):
                if identity != p.IDENTITY: raise AssertionError("wrong identity")
            def run_once(self, **kwargs):
                for index in (6, 7):
                    row = dict(fixtures[index]); row.pop("at_utc")
                    p.emit(row.pop("event"), **row)
                return True
        with contextlib.redirect_stdout(io.StringIO()) as output:
            p.emit("SCHOOL_WORLDS_ROUTE_PREPARED", **B, guest_network_requests=0)
            code = controller.trial(Provider(), guest_probe=Guest(), maintenance=True,
                                    clock=clock.now, sleep=clock.sleep)
        self.assertEqual(0, code)
        result = e.build_receipt(output.getvalue().encode(), B, self.worlds, code, 0)
        self.assertEqual("COMPUTE_CONTAINED", result["result"])

    def test_process_failure_missing_exit_or_tee_failure_cannot_succeed(self):
        for controller, capture in ((3, 0), (4, 0), (130, 0), (0, 1), (None, 0), (0, None), (True, 0), (0, -1), (256, 0)):
            with self.subTest(controller=controller, capture=capture):
                value = self.check(controller_exit=controller, capture_exit=capture)
                self.assertEqual("INCOMPLETE", value["result"])
                self.assertEqual({}, value["files"])
                self.assertIsNone(value["provider_stop"])

    def test_each_required_event_is_unique_and_present(self):
        for event in ("SCHOOL_WORLDS_ROUTE_PREPARED", "START_SUBMITTING", "START_ACCEPTED", "SCHOOL_WORLDS_STARTED",
                      "SCHOOL_WORLDS_CAPTURED", "SCHOOL_WORLDS_VERIFIED_RECEIPT", "SCHOOL_WORLDS_RESULT", "TRIAL_CONTAINMENT", "STOPPED_OBSERVED"):
            index = next(i for i, row in enumerate(self.rows) if row["event"] == event)
            for rows in (self.rows[:index] + self.rows[index+1:], self.rows[:index] + [self.rows[index]] + self.rows[index:]):
                with self.subTest(event=event): self.assertEqual("INCOMPLETE", self.check(rows)["result"])

    def test_order_and_three_consecutive_terminal_provider_reads(self):
        variants = [self.rows[:-2] + self.rows[-1:], self.rows[:-1], self.rows + [dict(event="START_ACCEPTED")]]
        row = copy.deepcopy(self.rows); row[-3]["startable"] = False; variants.append(row)
        row = copy.deepcopy(self.rows); row[-3]["status"] = "stopping"; variants.append(row)
        row = copy.deepcopy(self.rows); row[9], row[10] = row[10], row[9]; variants.append(row)
        row = copy.deepcopy(self.rows); row[-2]["startable"] = 1; variants.append(row)
        row = copy.deepcopy(self.rows); row[-1]["action_queue"] = "EMPTY"; variants.append(row)
        for rows in variants:
            with self.subTest(rows=rows[-3:]): self.assertEqual("INCOMPLETE", self.check(rows)["result"])

    def test_earlier_stopped_reads_do_not_replace_final_consecutive_three(self):
        rows = copy.deepcopy(self.rows)
        rows[10:10] = [dict(event="STOP_STATE", status="stopped", startable=True, action_queue=e.QUEUE)] * 2
        self.assertEqual("COMPUTE_CONTAINED", self.check(rows)["result"])
        rows[-3]["startable"] = False
        self.assertEqual("INCOMPLETE", self.check(rows)["result"])

    def test_unknown_stop_post_is_explicit_and_only_observed_state_proves_containment(self):
        rows = copy.deepcopy(self.rows)
        rows[12] = dict(event="ORDINARY_STOP_OUTCOME_UNKNOWN", reason="transport_failed")
        rows[-1]["stop_post_unknown"] = True
        value = self.check(rows)
        self.assertEqual("COMPUTE_CONTAINED", value["result"])
        self.assertTrue(value["provider_stop"]["stop_post_unknown"])
        rows[-1]["stop_post_unknown"] = False
        self.assertEqual("INCOMPLETE", self.check(rows)["result"])

    def test_refusal_abort_or_failure_event_cannot_hide_inside_success(self):
        for event in ("ORDINARY_STOP_REFUSED", "STOP_READ_BLOCKED", "BLOCKED", "GUEST_DIAGNOSTIC_FAILED"):
            rows = self.rows[:-1] + [dict(event=event, reason="http_403")] + self.rows[-1:]
            with self.subTest(event=event): self.assertEqual("INCOMPLETE", self.check(rows)["result"])

    def test_mismatched_source_run_attempt_receipt_or_path(self):
        for index, key, value in ((0, "head", "b"*40), (5, "attempt", True), (6, "run_id", "2"),
                                  (7, "artifact_path", "/tmp/other"), (8, "generated", False),
                                  (5, "max_seconds", 379), (5, "stop_submit_by_seconds", 600),
                                  (6, "raw_output_exported", True), (6, "stderr_bytes", 1)):
            rows = copy.deepcopy(self.rows); rows[index][key] = value
            with self.subTest(index=index, key=key): self.assertEqual("INCOMPLETE", self.check(rows)["result"])
        rows = copy.deepcopy(self.rows); rows[7]["receipt"]["head"] = "b"*40
        self.assertEqual("INCOMPLETE", self.check(rows)["result"])

    def test_bounded_strict_log_schema_and_no_raw_export(self):
        variants = [b"PRIVATE_SENTINEL\n", b'{"event":"START_ACCEPTED","event":"START_ACCEPTED"}\n',
                    self.raw() + b'{"event":"OTHER","secret":"PRIVATE_SENTINEL"}\n', self.raw()[:-1],
                    b"x" * (e.MAX_LOG + 1), self.raw().replace(b'"max_seconds":378', b'"max_seconds":NaN')]
        for raw in variants:
            value = e.build_receipt(raw, B, self.worlds, 0, 0)
            self.assertEqual("INCOMPLETE", value["result"])
            self.assertNotIn("PRIVATE_SENTINEL", json.dumps(value))
        rows = copy.deepcopy(self.rows); rows[-1]["secret"] = "PRIVATE_SENTINEL"
        self.assertEqual("INCOMPLETE", self.check(rows)["result"])

    def test_every_artifact_digest_is_checked(self):
        for name in ("receipt.json", "manifest.json", "input.json", "worlds.json", "worlds.pbn"):
            path = self.worlds / name; original = path.read_bytes(); path.write_bytes(original + b" ")
            with self.subTest(name=name): self.assertEqual("INCOMPLETE", self.check()["result"])
            path.write_bytes(original)

    def test_manifest_binding_sources_and_lifecycle_are_independent(self):
        path = self.worlds / "manifest.json"; original = path.read_bytes()
        for key, value in (("source_commit", "b"*40), ("attempt", True), ("run_id", "2"), ("host_bundle_sha256", "0"*64),
                           ("service_changes", ["restart"]), ("reviewed_sources", {})):
            row = json.loads(original); row[key] = value; path.write_bytes(v.canonical_json_bytes(row))
            with self.subTest(key=key): self.assertEqual("INCOMPLETE", self.check()["result"])
        path.write_bytes(original)

    def test_no_symlink_or_special_file_reads(self):
        target = self.worlds / "worlds.json"; original = target.read_bytes()
        other = self.root / "other"; other.write_bytes(original)
        target.unlink(); target.symlink_to(other)
        self.assertEqual("INCOMPLETE", self.check()["result"])
        target.unlink(); os.mkfifo(target)
        self.assertEqual("INCOMPLETE", self.check()["result"])

    def cli(self, codes=("0", "0")):
        with mock.patch.object(p, "context", return_value=dict(B)), mock.patch.dict(os.environ, RUNNER_TEMP=str(self.root)), contextlib.redirect_stdout(io.StringIO()) as output:
            code = e.main(["--controller-exit", codes[0], "--capture-exit", codes[1]])
        return code, output.getvalue()

    def test_cli_publishes_only_validated_five_files_and_lifecycle(self):
        (self.control / "controller.jsonl").write_bytes(self.raw())
        code, _ = self.cli()
        destination = self.root / "ibm-school-compute-artifact"
        self.assertEqual(0, code)
        self.assertEqual({"receipt.json", "manifest.json", "input.json", "worlds.json", "worlds.pbn", "lifecycle.json"}, {path.name for path in destination.iterdir()})
        self.assertEqual(0o700, destination.stat().st_mode & 0o777)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in destination.iterdir()))
        self.assertEqual(3, self.cli()[0])  # Never overwrite a previous artifact directory.

    def test_cli_missing_cancelled_or_failed_evidence_exports_only_incomplete_lifecycle(self):
        (self.control / "controller.jsonl").write_bytes(self.raw())
        code, output = self.cli(("", ""))
        destination = self.root / "ibm-school-compute-artifact"
        self.assertEqual(3, code); self.assertIn("INCOMPLETE", output)
        self.assertEqual({"lifecycle.json"}, {path.name for path in destination.iterdir()})
        self.assertEqual("INCOMPLETE", json.loads((destination / "lifecycle.json").read_bytes())["result"])

    def test_interrupted_artifact_copy_cannot_publish_success_marker(self):
        (self.control / "controller.jsonl").write_bytes(self.raw())
        original = os.open
        def interrupted(path, flags, *args, **kwargs):
            if Path(path).name == "worlds.pbn" and flags & os.O_WRONLY:
                raise OSError("PRIVATE_SENTINEL")
            return original(path, flags, *args, **kwargs)
        with mock.patch.object(e.os, "open", side_effect=interrupted):
            code, output = self.cli()
        self.assertEqual(3, code)
        self.assertFalse((self.root / "ibm-school-compute-artifact" / "lifecycle.json").exists())
        self.assertNotIn("PRIVATE_SENTINEL", output)

    def test_exit_code_parser_has_no_coercion(self):
        for value in ("", "-1", "256", "01", "true", "0.0", " 0", "٠"):
            self.assertIsNone(e.exit_code(value))
        for value in ("0", "3", "4", "130", "255"): self.assertEqual(int(value), e.exit_code(value))


class WorkflowTests(unittest.TestCase):
    def test_narrow_manual_surface_and_no_new_permissions(self):
        text = Path(".github/workflows/ibm-school-compute.yml").read_text()
        self.assertIn("options: [status, school_worlds, stop]", text)
        self.assertIn("contents: read", text)
        for forbidden in ("actions: write", "contents: write", "id-token:", "schedule:", "workflow_run:", "diagnose_ssh:", "test_oracle:", "isolate_units --", "manual_console_trial --", "trial --"):
            self.assertNotIn(forbidden, text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("'ibm-vpc-power-controller'", text)
        self.assertIn("'ibm-vpc-independent-stop'", text)
        self.assertNotIn("test_ibm_trial_plan", text)
        self.assertEqual(4, text.count('ref: ${{ github.sha }}'))
        self.assertEqual(4, text.count('persist-credentials: false'))
        self.assertEqual(3, text.count("github.actor == 'olegmed1-art' && github.triggering_actor == 'olegmed1-art' }}"))

    def test_stop_does_not_depend_on_school_contract_or_oracle(self):
        text = Path(".github/workflows/ibm-school-compute.yml").read_text()
        stop = text.split("  stop:\n", 1)[1].split("  status:\n", 1)[0]
        self.assertNotIn("needs:", stop); self.assertNotIn("ORACLE", stop)
        self.assertIn("stop --ack ORDINARY_STOP_EXACT_IBM", stop)
        self.assertIn("github.ref == 'refs/heads/main'", stop)

    def test_compute_uploads_only_helper_export_after_containment_evidence(self):
        text = Path(".github/workflows/ibm-school-compute.yml").read_text()
        school = text.split("  school-worlds:\n", 1)[1].split("  stop:\n", 1)[0]
        self.assertIn("needs: contract", school)
        self.assertIn('codes=("${PIPESTATUS[@]}")', school)
        self.assertIn("--controller-exit", school); self.assertIn("--capture-exit", school)
        self.assertNotIn("continue-on-error", school)
        upload = school.split("uses: actions/upload-artifact@", 1)[1]
        self.assertIn("path: ${{ runner.temp }}/ibm-school-compute-artifact", upload)
        self.assertNotIn("controller.jsonl", upload)
        self.assertIn("if-no-files-found: error", upload)
        self.assertIn("retention-days: 7", upload)
        self.assertIn("name: ibm-school-worlds-${{ github.run_id }}", upload)


if __name__ == "__main__":
    unittest.main()
