"""Offline maintenance-transport contracts. No SSH, cloud API or network calls.

The small real-process cleanup fixtures run only in the existing Bubblewrap
and seccomp harness. All transport launches in other tests are mocked.
"""
import ast
import base64
import contextlib
import copy
import ctypes
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from ops import ibm_unit_isolation_probe as p


BINDING = dict(run_id="39999999999", attempt=1, head="a" * 40)
ENV = dict(GITHUB_REPOSITORY=p.read_only.REPOSITORY,
           GITHUB_REF=p.read_only.REF, GITHUB_EVENT_NAME="workflow_dispatch",
           GITHUB_ACTOR="olegmed1-art", GITHUB_TRIGGERING_ACTOR="olegmed1-art",
           GITHUB_RUN_ID=BINDING["run_id"], GITHUB_RUN_ATTEMPT="1",
           GITHUB_SHA=BINDING["head"], GITHUB_EVENT_PATH="/fixture-event.json")
INPUTS = dict(mode="isolate_units", diagnose_ssh=False, test_oracle=False)
LIMITATIONS = ["EXPECTED_MACHINE_ID_UNAVAILABLE", "TRANSPORT_IDENTITY_ASSERTION_ONLY",
               "NO_REBOOT_VERIFICATION", "LOADED_GRAPH_ONLY", "DAEMON_RELOAD_GLOBAL_EFFECTS"]


def receipt(result="ISOLATED_TARGET_SCOPE"):
    return dict(schema="bridge.ibm.unit-isolation.receipt.v1", operation="isolate",
                **BINDING, result=result, reason="OK" if result == "ISOLATED_TARGET_SCOPE" else "INTERNAL_BLOCKED",
                snapshot_id="39999999999-aaaaaaaaaaaa", snapshot_sha256="b" * 64,
                snapshot_restore_sha256="b" * 64, changed_units=list(p.TARGETS),
                changes=[dict(unit=name, gate="VERIFIED", stop="OBSERVED_INACTIVE") for name in p.TARGETS],
                target_states=[dict(unit=name, active="inactive", sub="dead",
                                    job_pending=False, cgroup_empty=True) for name in p.TARGETS],
                host_identity="TRANSPORT_ASSERTION_ONLY", limitations=list(LIMITATIONS))


def chain(row=None):
    row = receipt() if row is None else row
    host_raw = (json.dumps(row, sort_keys=True, separators=(",", ":"))+"\n").encode()
    return [dict(event="RUN_BOUND_UNIT_ISOLATION", **BINDING),
            dict(event="TCP_OPEN", elapsed_seconds=.2, attempts=1),
            dict(event="ISOLATION_SSH_STARTED", **BINDING),
            dict(event="ISOLATION_SSH_FINISHED", **BINDING, ssh_exit=0, elapsed_seconds=.3,
                 stdout_bytes=len(host_raw), stderr_bytes=0,
                 stdout_sha256=hashlib.sha256(host_raw).hexdigest(),
                 stderr_sha256=hashlib.sha256(b"").hexdigest(), raw_output_exported=False),
            dict(event="UNIT_ISOLATION_RECEIPT", **BINDING, receipt=row)]


def encoded(rows):
    return b"\n".join(json.dumps(row, separators=(",", ":")).encode() for row in rows) + b"\n"


class OfflineCase(unittest.TestCase):
    def setUp(self):
        self.guard = contextlib.ExitStack()
        self.addCleanup(self.guard.close)
        for target in ("socket.socket", "subprocess.Popen", "subprocess.run", "subprocess.check_output"):
            self.guard.enter_context(mock.patch(target, side_effect=AssertionError("LIVE_TRANSPORT_FORBIDDEN")))

    def probe(self, rows=None, rc=0, err=b""):
        obj = p.PreparedIsolation(dict(BINDING), "safe fixture", "host fixture", 71, 72)
        obj.ready = True
        obj.bind_identity(dict(p.IDENTITY))
        obj.call = mock.Mock(return_value=(rc, encoded(chain() if rows is None else rows), err))
        return obj

    def run_probe(self, obj, **kwargs):
        with mock.patch.object(p, "emit") as emit:
            result = obj.run_once(**kwargs)
        return result, emit


class RunBindingTests(OfflineCase):
    def ctx(self, env=None, inputs=None, head=None, raw=None):
        event = json.dumps(dict(inputs=INPUTS if inputs is None else inputs)).encode() if raw is None else raw
        with mock.patch.dict(os.environ, ENV if env is None else env, clear=True), \
             mock.patch.object(Path, "read_bytes", return_value=event), \
             mock.patch.object(p, "bounded_process", return_value=(0, (head or BINDING["head"]).encode()+b"\n", b"")) as run:
            binding = p.context()
        run.assert_called_once_with(["/usr/bin/git", "rev-parse", "HEAD"], timeout=3)
        return binding

    def test_exact_owner_dispatch_binding(self):
        self.assertEqual(BINDING, self.ctx())

    def test_exact_false_strings_are_allowed(self):
        self.assertEqual(BINDING, self.ctx(inputs=dict(INPUTS, diagnose_ssh="false", test_oracle="false")))

    def test_repository_ref_event_and_both_owners_are_required(self):
        for key, value in [("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_REF", "refs/heads/main"),
                           ("GITHUB_EVENT_NAME", "push"), ("GITHUB_ACTOR", "other"),
                           ("GITHUB_TRIGGERING_ACTOR", "other")]:
            with self.subTest(key=key), self.assertRaises(p.IsolationError):
                self.ctx(dict(ENV, **{key: value}))

    def test_all_consumed_live_runs_are_refused(self):
        self.assertTrue({"38065148168", "38064916368", "37939530764", "37776596059", "37788143504"} <= p.CONSUMED_RUNS)
        for run in p.CONSUMED_RUNS:
            with self.subTest(run=run), self.assertRaisesRegex(p.IsolationError, "run_attempt"):
                self.ctx(dict(ENV, GITHUB_RUN_ID=run))

    def test_run_and_attempt_are_strict(self):
        for key, value in [("GITHUB_RUN_ID", ""), ("GITHUB_RUN_ID", "abc"),
                           ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ID", "01"),
                           ("GITHUB_RUN_ID", "١"), ("GITHUB_RUN_ID", "1"*21),
                           ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_ATTEMPT", "01")]:
            with self.subTest(key=key, value=value), self.assertRaises(p.IsolationError):
                self.ctx(dict(ENV, **{key: value}))

    def test_checkout_head_is_exact_lowercase_and_matches_git(self):
        for head in ("A" * 40, "a" * 39, "g" * 40, "a" * 41):
            with self.subTest(head=head), self.assertRaises(p.IsolationError):
                self.ctx(dict(ENV, GITHUB_SHA=head))
        with self.assertRaisesRegex(p.IsolationError, "checkout_binding"):
            self.ctx(head="b" * 40)

    def test_git_failure_cannot_bind_checkout(self):
        with mock.patch.dict(os.environ, ENV, clear=True), \
             mock.patch.object(p, "bounded_process", return_value=(1, BINDING["head"].encode(), b"")), \
             self.assertRaisesRegex(p.IsolationError, "checkout_binding"):
            p.context()

    def test_only_isolate_units_and_two_explicit_false_flags(self):
        for change in [dict(mode="trial_start"), dict(mode="status"), dict(mode="manual_console_trial"),
                       dict(diagnose_ssh=True), dict(test_oracle=True), dict(diagnose_ssh=0),
                       dict(test_oracle=None), dict(diagnose_ssh="False")]:
            with self.subTest(change=change), self.assertRaisesRegex(p.IsolationError, "dispatch_inputs"):
                self.ctx(inputs=dict(INPUTS, **change))
        for missing in ("mode", "diagnose_ssh", "test_oracle"):
            inputs = dict(INPUTS); inputs.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(p.IsolationError):
                self.ctx(inputs=inputs)

    def test_dispatch_duplicate_fields_and_nonfinite_json_are_refused(self):
        for raw in (b'{"inputs":{"mode":"isolate_units","mode":"status"}}', b'{"inputs":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(p.IsolationError):
                self.ctx(raw=raw)


class IdentityAttemptAndBudgetTests(OfflineCase):
    def test_missing_identity_cannot_call_or_consume_attempt(self):
        obj = self.probe(); obj.identity_bound = False
        with self.assertRaises(p.IsolationError):
            obj.run_once()
        self.assertFalse(obj.attempted); obj.call.assert_not_called()
        obj.call = p.PreparedIsolation.call.__get__(obj)
        with mock.patch.object(p, "bounded_process") as run, self.assertRaisesRegex(p.IsolationError, "fresh_api_identity_required"):
            obj.call("isolate", 110)
        run.assert_not_called()

    def test_every_identity_field_and_extra_fields_are_checked(self):
        for key in p.IDENTITY:
            bad = dict(p.IDENTITY); bad[key] = "wrong"
            obj = p.PreparedIsolation(dict(BINDING), "safe", "host", 71, 72)
            with self.subTest(key=key), self.assertRaisesRegex(p.IsolationError, "fresh_api_identity_required"):
                obj.bind_identity(bad)
            self.assertFalse(obj.identity_bound)
        for bad in (None, [], dict(p.IDENTITY, extra="private")):
            with self.subTest(bad=bad), self.assertRaises(p.IsolationError):
                obj.bind_identity(bad)

    def test_exact_identity_is_bound(self):
        obj = p.PreparedIsolation(dict(BINDING), "safe", "host", 71, 72)
        obj.bind_identity(dict(p.IDENTITY)); self.assertTrue(obj.identity_bound)

    def test_only_one_attempt_after_success(self):
        obj = self.probe(); self.assertTrue(self.run_probe(obj)[0])
        with self.assertRaisesRegex(p.IsolationError, "not_ready_or_replay"):
            obj.run_once()
        obj.call.assert_called_once()

    def test_transport_failure_consumes_attempt_before_io(self):
        obj = self.probe()
        def fail(*args):
            self.assertTrue(obj.attempted)
            raise p.IsolationError("transport_timeout")
        obj.call.side_effect = fail
        with self.assertRaisesRegex(p.IsolationError, "transport_timeout"):
            obj.run_once()
        with self.assertRaisesRegex(p.IsolationError, "not_ready_or_replay"):
            obj.run_once()
        obj.call.assert_called_once()

    def test_rejected_or_malformed_response_consumes_attempt(self):
        for rows, rc in [(chain(receipt("BLOCKED")), 3), ([dict(event="UNKNOWN")], 0)]:
            obj = self.probe(rows, rc)
            try:
                self.run_probe(obj)
            except p.IsolationError:
                pass
            with self.assertRaises(p.IsolationError):
                obj.run_once()
            obj.call.assert_called_once()

    def test_minimum_and_maximum_budgets(self):
        self.assertEqual((115, 378), (p.MIN_MAINTENANCE_SECONDS, p.MAINTENANCE_SECONDS))
        for seconds in (115, 115.5, 378):
            obj = self.probe()
            self.assertTrue(self.run_probe(obj, max_seconds=seconds)[0])
            obj.call.assert_called_once_with("isolate", seconds-5)

    def test_invalid_budget_never_consumes_or_launches(self):
        for seconds in (0, 114.999, 378.001, -1, True, False, "378", None, math.nan, math.inf):
            obj = self.probe()
            with self.subTest(seconds=seconds), self.assertRaisesRegex(p.IsolationError, "maintenance_budget"):
                obj.run_once(max_seconds=seconds)
            self.assertFalse(obj.attempted); obj.call.assert_not_called()

    def test_unprepared_route_cannot_launch(self):
        obj = self.probe(); obj.ready = False
        with self.assertRaises(p.IsolationError):
            obj.run_once()
        obj.call.assert_not_called()

    def test_close_is_idempotent_and_closed_transport_is_refused(self):
        obj = p.PreparedIsolation(dict(BINDING), "safe", "host", 71, 72)
        with mock.patch.object(p.os, "close") as close:
            obj.close(); obj.close()
        self.assertEqual([mock.call(71), mock.call(72)], close.call_args_list)
        with mock.patch.object(p, "bounded_process") as run, self.assertRaisesRegex(p.IsolationError, "transport_closed"):
            obj.call("preflight", 12)
        run.assert_not_called()


class ReceiptTests(OfflineCase):
    def check(self, row):
        return p.validate_receipt(row, BINDING)

    def test_full_success_receipt(self):
        row = receipt(); self.assertEqual(row, self.check(row))

    def test_blocked_unknown_states_are_valid_evidence_but_never_success(self):
        row = receipt("BLOCKED")
        row.update(snapshot_id=None, snapshot_sha256=None, snapshot_restore_sha256=None,
                   changed_units=[], changes=[])
        for state in row["target_states"]:
            state.update(active="unknown", sub="unknown", job_pending=None, cgroup_empty=None)
        self.assertEqual(row, self.check(row))
        self.assertFalse(self.run_probe(self.probe(chain(row)))[0])

    def test_missing_extra_and_private_receipt_fields_are_rejected(self):
        for key in receipt():
            row = receipt(); row.pop(key)
            with self.subTest(missing=key), self.assertRaises(p.IsolationError):
                self.check(row)
        for key in ("private_key", "raw_stdout", "machine_id", "journal", "stdout", "quiet"):
            row = receipt(); row[key] = "PRIVATE_SENTINEL"
            with self.subTest(extra=key), self.assertRaisesRegex(p.IsolationError, "receipt_schema"):
                self.check(row)

    def test_bool_or_wrong_typed_binding_is_rejected(self):
        for key, value in [("attempt", True), ("attempt", "1"), ("run_id", True),
                           ("run_id", 39999999999), ("head", "b"*40)]:
            row = receipt(); row[key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(p.IsolationError, "receipt_binding"):
                self.check(row)

    def test_schema_operation_result_identity_and_reason_are_closed(self):
        for key, value in [("schema", "other"), ("operation", "rollback"), ("result", "QUIET_IN_KNOWN_SCOPE"),
                           ("result", "UNKNOWN"), ("host_identity", "VERIFIED_MACHINE_ID"),
                           ("reason", "PRIVATE_SENTINEL: /secret/path"), ("reason", True)]:
            row = receipt(); row[key] = value
            with self.subTest(key=key), self.assertRaises(p.IsolationError):
                self.check(row)

    def test_snapshot_identifier_and_all_hashes_have_strict_types(self):
        for key in ("snapshot_id", "snapshot_sha256", "snapshot_restore_sha256"):
            values = (True, 1, [], "../private") if key == "snapshot_id" else (True, 1, [], "b"*63, "B"*64, "g"*64)
            for value in values:
                row = receipt(); row[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(p.IsolationError):
                    self.check(row)

    def test_success_requires_complete_and_restored_snapshot(self):
        for key in ("snapshot_id", "snapshot_sha256", "snapshot_restore_sha256"):
            row = receipt(); row[key] = None
            with self.subTest(key=key), self.assertRaises(p.IsolationError):
                self.check(row)

    def test_success_requires_restoration_hash_equal_to_snapshot_hash(self):
        row = receipt(); row["snapshot_restore_sha256"] = "c"*64
        with self.assertRaisesRegex(p.IsolationError, "receipt_incomplete"):
            self.check(row)

    def test_success_snapshot_identifier_is_bound_to_current_run_and_head(self):
        for value in ("39999999998-aaaaaaaaaaaa", "39999999999-bbbbbbbbbbbb", "private_snapshot", "39999999999"):
            row = receipt(); row["snapshot_id"] = value
            with self.subTest(value=value), self.assertRaises(p.IsolationError):
                self.check(row)

    def test_fixed_limitations_cannot_hide_or_add_claims(self):
        for limits in ([], LIMITATIONS[:-1], LIMITATIONS+ ["PRIVATE_SENTINEL"], list(reversed(LIMITATIONS)), True):
            row = receipt(); row["limitations"] = limits
            with self.subTest(limits=limits), self.assertRaises(p.IsolationError):
                self.check(row)

    def test_unit_lists_cannot_duplicate_expand_or_be_incomplete(self):
        for changed in ([], list(p.TARGETS)[:-1], [p.TARGETS[0]]*4, list(p.TARGETS)+["ssh.service"], "all"):
            row = receipt(); row["changed_units"] = changed
            with self.subTest(changed=changed), self.assertRaises(p.IsolationError):
                self.check(row)
        for states in ([], receipt()["target_states"][:-1], [receipt()["target_states"][0]]*4):
            row = receipt(); row["target_states"] = states
            with self.subTest(states=states), self.assertRaises(p.IsolationError):
                self.check(row)

    def test_target_state_schema_and_scope_are_closed(self):
        for changes in (dict(unit="ssh.service"), dict(active="quiet"), dict(sub="exited"), dict(private="PRIVATE_SENTINEL")):
            row = receipt(); row["target_states"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(p.IsolationError):
                self.check(row)

    def test_faked_quiet_pending_jobs_or_nonempty_cgroups_cannot_succeed(self):
        for change in (dict(active="active"), dict(sub="running"), dict(job_pending=True),
                       dict(cgroup_empty=False), dict(job_pending=None), dict(cgroup_empty=None),
                       dict(job_pending=0), dict(cgroup_empty=1)):
            row = receipt(); row["target_states"][0].update(change)
            with self.subTest(change=change), self.assertRaises(p.IsolationError):
                self.check(row)

    def test_changes_have_exact_fixed_schema_and_maximum_four(self):
        for changes in ([dict(unit=p.TARGETS[0], gate="VERIFIED", stop="OBSERVED_INACTIVE", private="PRIVATE_SENTINEL")],
                        [dict(unit="ssh.service", gate="VERIFIED", stop="OBSERVED_INACTIVE")],
                        [dict(unit=p.TARGETS[0], gate="UNKNOWN", stop="OBSERVED_INACTIVE")],
                        [dict(unit=p.TARGETS[0], gate="VERIFIED", stop="UNKNOWN")],
                        receipt()["changes"]+[receipt()["changes"][0]], True):
            row = receipt(); row["changes"] = changes
            with self.subTest(changes=changes), self.assertRaises(p.IsolationError):
                self.check(row)

    def test_success_requires_verified_gate_and_observed_stop_for_every_target(self):
        for change in (dict(gate="INTENDED"), dict(gate="CREATED"), dict(gate="REMOVED"),
                       dict(stop="REQUESTED"), dict(stop="NOT_REQUESTED")):
            row = receipt(); row["changes"][0].update(change)
            with self.subTest(change=change), self.assertRaises(p.IsolationError):
                self.check(row)
        for changes in ([], receipt()["changes"][:-1], [receipt()["changes"][0]]*4):
            row = receipt(); row["changes"] = changes
            with self.subTest(changes=changes), self.assertRaises(p.IsolationError):
                self.check(row)


class ResponseTests(OfflineCase):
    def test_exact_success_chain_exports_only_validated_receipt(self):
        obj = self.probe(); result, emit = self.run_probe(obj)
        self.assertTrue(result)
        self.assertEqual(["UNIT_ISOLATION_CAPTURED", "UNIT_ISOLATION_VERIFIED_RECEIPT"], [call.args[0] for call in emit.call_args_list])
        self.assertEqual(receipt(), emit.call_args_list[-1].kwargs["receipt"])

    def test_chain_missing_reordered_duplicated_or_extra_events_fail_closed(self):
        rows = chain()
        cases = [rows[:-1], rows[:1]+rows[2:], rows[:2]+[rows[1]]+rows[2:], rows[:3]+[rows[4], rows[3]],
                 rows+[dict(event="TCP_OPEN", elapsed_seconds=0, attempts=1)]]
        for case in cases:
            with self.subTest(events=[row["event"] for row in case]):
                result, emit = self.run_probe(self.probe(case))
                self.assertFalse(result)
                self.assertNotIn("UNIT_ISOLATION_VERIFIED_RECEIPT", [call.args[0] for call in emit.call_args_list])

    def test_unknown_event_raises_without_exporting_private_fields(self):
        obj = self.probe([dict(event="PRIVATE_SENTINEL", private="PRIVATE_SENTINEL")])
        with mock.patch.object(p, "emit") as emit, self.assertRaisesRegex(p.IsolationError, "response_events"):
            obj.run_once()
        self.assertNotIn("PRIVATE_SENTINEL", repr(emit.call_args_list))

    def test_foreign_binding_in_any_bound_event_is_refused(self):
        for index in (0, 2, 3, 4):
            for key, value in (("head", "b"*40), ("attempt", True), ("run_id", True)):
                rows = chain(); rows[index][key] = value
                with self.subTest(index=index, key=key), self.assertRaises(p.IsolationError):
                    self.run_probe(self.probe(rows))

    def test_nonzero_remote_or_ssh_exit_never_succeeds(self):
        self.assertFalse(self.run_probe(self.probe(rc=3))[0])
        rows = chain(); rows[3]["ssh_exit"] = 255
        with self.assertRaises(p.IsolationError):
            self.run_probe(self.probe(rows))

    def test_canonical_host_stdout_hash_length_and_empty_stderr_are_bound(self):
        for change in (dict(stdout_bytes=0), dict(stdout_sha256="d"*64),
                       dict(stderr_bytes=1), dict(stderr_sha256="d"*64)):
            rows = chain(); rows[3].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(p.IsolationError, "receipt_capture_binding"):
                self.run_probe(self.probe(rows))
        rows = chain()
        without_newline = json.dumps(rows[4]["receipt"], sort_keys=True, separators=(",", ":")).encode()
        rows[3].update(stdout_bytes=len(without_newline), stdout_sha256=hashlib.sha256(without_newline).hexdigest())
        with self.assertRaisesRegex(p.IsolationError, "receipt_capture_binding"):
            self.run_probe(self.probe(rows))

    def test_partial_and_blocked_rc3_receipts_are_verified_as_failure_evidence(self):
        for result in ("BLOCKED", "PARTIAL_BLOCKED"):
            rows = chain(receipt(result)); rows[3]["ssh_exit"] = 3
            success, emit = self.run_probe(self.probe(rows, rc=3))
            self.assertFalse(success)
            self.assertEqual("UNIT_ISOLATION_VERIFIED_RECEIPT", emit.call_args.args[0])
            self.assertEqual(result, emit.call_args.kwargs["receipt"]["result"])

    def test_tcp_attempts_elapsed_and_unknown_fields_are_strict(self):
        for change in (dict(attempts=True), dict(attempts=0), dict(attempts=302), dict(elapsed_seconds=True),
                       dict(elapsed_seconds=-1), dict(elapsed_seconds=300.001), dict(elapsed_seconds=math.inf),
                       dict(private="PRIVATE_SENTINEL")):
            rows = chain(); rows[1].update(change)
            with self.subTest(change=change), self.assertRaises(p.IsolationError):
                self.run_probe(self.probe(rows))

    def test_ssh_receipt_types_bounds_and_private_fields_are_strict(self):
        for change in (dict(ssh_exit=True), dict(stdout_bytes=True), dict(stderr_bytes=-1),
                       dict(stdout_bytes=16385), dict(stdout_sha256="x"*64), dict(stderr_sha256=True),
                       dict(elapsed_seconds=True), dict(elapsed_seconds=105.001),
                       dict(raw_output_exported=True), dict(private="PRIVATE_SENTINEL")):
            rows = chain(); rows[3].update(change)
            with self.subTest(change=change), self.assertRaises(p.IsolationError):
                self.run_probe(self.probe(rows))

    def test_receipt_holder_private_fields_are_refused(self):
        rows = chain(); rows[4]["private_key"] = "PRIVATE_SENTINEL"
        with self.assertRaises(p.IsolationError):
            self.run_probe(self.probe(rows))

    def test_raw_stderr_is_hashed_and_never_reflected(self):
        obj = self.probe(rc=3, err=b"PRIVATE_SENTINEL")
        result, emit = self.run_probe(obj)
        self.assertFalse(result); self.assertNotIn("PRIVATE_SENTINEL", repr(emit.call_args_list))
        capture = emit.call_args_list[0].kwargs
        self.assertEqual(hashlib.sha256(b"PRIVATE_SENTINEL").hexdigest(), capture["stderr_sha256"])
        self.assertIs(False, capture["raw_output_exported"])

    def test_output_response_types_limits_lines_and_strict_json(self):
        cases = [(True, b"{}", b""), (0, "text", b""), (0, b"{}", "text"),
                 (0, b"x"*(p.OUTPUT_BYTES+1), b""), (0, b"x"*16385, b""),
                 (0, b"{}\n"*13, b""), (0, b"", b""), (0, b"\n", b""),
                 (0, b'{"event":"TCP_OPEN","event":"TCP_OPEN"}', b""),
                 (0, b'{"event":"TCP_OPEN","elapsed_seconds":NaN}', b""), (0, b"[]", b"")]
        for value in cases:
            obj = self.probe(); obj.call.return_value = value
            with self.subTest(value=value[:1]), self.assertRaises(p.IsolationError):
                self.run_probe(obj)


class PreflightSourceAndCommandTests(OfflineCase):
    def test_preflight_requires_exact_bound_ready_record_without_stderr(self):
        expected = dict(event="ISOLATION_ROUTE_READY", **BINDING, hostpin="MATCH", guest_network_requests=0)
        obj = self.probe(); obj.ready = False
        obj.call.return_value = (0, json.dumps(expected).encode(), b"")
        with mock.patch.object(p, "emit"):
            obj.preflight()
        self.assertTrue(obj.ready); self.assertFalse(obj.attempted)
        obj.call.assert_called_once_with("preflight", 12)
        for change, rc, err in [(dict(head="b"*40), 0, b""), (dict(attempt=True), 0, b""),
                                (dict(guest_network_requests=False), 0, b""), (dict(guest_network_requests=1), 0, b""),
                                (dict(private="PRIVATE_SENTINEL"), 0, b""), ({}, 255, b""), ({}, 0, b"PRIVATE_SENTINEL")]:
            obj = self.probe(); obj.ready = False
            obj.call.return_value = (rc, json.dumps(dict(expected, **change)).encode(), err)
            with self.subTest(change=change, rc=rc, err=err), self.assertRaises(p.IsolationError):
                obj.preflight()
            self.assertFalse(obj.ready)

    def test_outer_ssh_uses_only_existing_key_and_sealed_trust(self):
        obj = p.PreparedIsolation(dict(BINDING), "safe", "host", 71, 72)
        obj.bind_identity(dict(p.IDENTITY))
        with mock.patch.object(p, "bounded_process", return_value=(0, b"", b"")) as run:
            obj.call("isolate", 110)
        argv = run.call_args.args[0]; kwargs = run.call_args.kwargs
        self.assertEqual(["/usr/bin/ssh", "-F", "/dev/null", "-T", "-i"], argv[:5])
        self.assertEqual("/proc/"+str(os.getpid())+"/fd/71", argv[5])
        options = [argv[index+1] for index, token in enumerate(argv) if token == "-o"]
        for value in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes", "HostKeyAlgorithms=ssh-ed25519",
                      "GlobalKnownHostsFile=/dev/null", "UpdateHostKeys=no", "VerifyHostKeyDNS=no", "CheckHostIP=no",
                      "AddKeysToAgent=no", "ForwardAgent=no", "ClearAllForwardings=yes", "ConnectionAttempts=1",
                      "UserKnownHostsFile=/proc/"+str(os.getpid())+"/fd/72"):
            self.assertIn(value, options)
        self.assertEqual(1, argv.count("-i")); self.assertEqual("ubuntu@"+p.read_only.ORACLE, argv[-2])
        self.assertEqual(["/usr/bin/python3", "-I", "-B", "-c", p.remote_source()], shlex.split(argv[-1]))
        payload = json.loads(kwargs["payload"])
        self.assertEqual(105, payload["seconds"]); self.assertEqual(110, kwargs["timeout"])
        self.assertEqual(dict(schema="bridge.ibm.unit-isolation.request.v1", operation="isolate", **BINDING,
                              identity=p.IDENTITY, transport=p.TRANSPORT), payload["request"])
        self.assertLessEqual(len(kwargs["payload"]), 131072)

    def test_preflight_has_no_ibm_request(self):
        obj = p.PreparedIsolation(dict(BINDING), "safe", "host", 71, 72)
        with mock.patch.object(p, "bounded_process", return_value=(0, b"", b"")) as run:
            obj.call("preflight", 12)
        payload = json.loads(run.call_args.kwargs["payload"])
        self.assertIsNone(payload["request"]); self.assertEqual(28, payload["seconds"])
        self.assertFalse(obj.identity_bound)

    def test_source_pins_match_both_reviewed_local_modules(self):
        root = Path(p.__file__).parent
        self.assertEqual(p.SAFE_SOURCE_SHA, hashlib.sha256((root/"ibm_ssh_probe_safe.py").read_bytes()).hexdigest())
        self.assertEqual(p.HOST_SOURCE_SHA, hashlib.sha256((root/"ibm_unit_isolation.py").read_bytes()).hexdigest())

    def test_actual_reviewed_source_payload_fits_128_kib(self):
        root = Path(p.__file__).parent
        payload = json.dumps(dict(phase="isolate", binding=BINDING,
                                 safe_source=(root/"ibm_ssh_probe_safe.py").read_text(),
                                 host_source=(root/"ibm_unit_isolation.py").read_text(),
                                 host_sha=p.HOST_SOURCE_SHA, seconds=368,
                                 request=dict(schema="bridge.ibm.unit-isolation.request.v1", operation="isolate",
                                              **BINDING, identity=p.IDENTITY, transport=p.TRANSPORT))).encode()
        self.assertLessEqual(len(payload), p.INPUT_BYTES)

    def test_source_mismatch_stops_before_credentials_or_network(self):
        with mock.patch.object(p, "context", return_value=BINDING), \
             mock.patch.object(Path, "read_text", return_value="tampered source"), \
             mock.patch.dict(os.environ, {"ORACLE_SSH_PRIVATE_KEY": "PRIVATE_SENTINEL"}, clear=True), \
             mock.patch.object(p, "bounded_process") as run, \
             mock.patch.object(p.read_only, "memory") as memory, \
             self.assertRaisesRegex(p.IsolationError, "source_pin"):
            p.prepare()
        run.assert_not_called(); memory.assert_not_called()

    def test_remote_template_compiles_and_enforces_alarm_parent_death_and_limits(self):
        source = p.remote_source(); compile(source, "offline_reviewed_transport", "exec")
        for value in ("prctl(1,signal.SIGTERM)", "prctl(1,signal.SIGKILL)", "os.getppid()!=parent",
                      "signal.setitimer(signal.ITIMER_REAL,28)", "signal.setitimer(signal.ITIMER_REAL,deadline-time.monotonic())",
                      "sys.stdin.buffer.read(131073)", "need(len(raw)<=131072)", "F_SEAL_WRITE", "F_SEAL_SHRINK",
                      "F_SEAL_GROW", "F_SEAL_SEAL", "os.killpg(p.pid,signal.SIGKILL)",
                      "105<=p['seconds']<=368", "seconds=105,limit=16384"):
            self.assertIn(value, source)
        self.assertNotIn("@HOST_SHA@", source); self.assertNotIn("@SAFE_SHA@", source)


class RemoteRouteTests(OfflineCase):
    """Run the Oracle script with fake OS, keyscan, TCP and IBM SSH only."""
    def remote(self, phase="isolate", bad_pin=False, marked=False, host_rc=0):
        public = base64.b64encode(b"fake-ed25519-public-key").decode()
        fingerprint = "SHA256:"+base64.b64encode(hashlib.sha256(base64.b64decode(public)).digest()).decode().rstrip("=")
        safe = "\n".join(["HOST='161.156.86.34'", "VERIFIED_USER='ubuntu'", "KEY='/existing/owner-key'", "KNOWN='/existing/known-hosts'",
                           "EXPECTED="+repr("SHA256:wrong" if bad_pin else fingerprint),
                           "class ProbeBudget:", " def __init__(self, deadline):self.deadline=deadline",
                           " def remaining(self):return 120", "def tcp_ready(budget):", " emit('TCP_OPEN',elapsed_seconds=0,attempts=1);return True"])
        host = "reviewed_host_fixture = 'quote-safe'\n"
        payload = dict(phase=phase, binding=BINDING, safe_source=safe, host_source=host,
                       host_sha=hashlib.sha256(host.encode()).hexdigest(), seconds=28 if phase == "preflight" else 105,
                       request=None if phase == "preflight" else dict(operation="isolate", **BINDING))
        source = p.REMOTE_TEMPLATE.replace("@SAFE_SHA@", hashlib.sha256(safe.encode()).hexdigest()).replace("@HOST_SHA@", payload["host_sha"])
        tree = ast.parse(source)
        # Supply fake bounded run and shared modules; never execute an SSH child.
        tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))
                     and not (isinstance(node, ast.FunctionDef) and node.name == "run")]
        run = mock.Mock(side_effect=[(0, (("@revoked " if marked else "")+"161.156.86.34 ssh-ed25519 "+public+"\n").encode(), b""),
                                     (host_rc, (json.dumps(receipt("BLOCKED" if host_rc else "ISOLATED_TARGET_SCOPE"), sort_keys=True, separators=(",", ":"))+"\n").encode(), b"")])
        fake_sys = types.SimpleNamespace(stdin=types.SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode())), exit=sys.exit)
        ns = dict(base64=base64, ctypes=ctypes, fcntl=fcntl, hashlib=hashlib, json=json, math=math,
                  os=os, re=re, shlex=shlex, signal=signal, socket=socket, stat=stat, subprocess=subprocess,
                  sys=fake_sys, time=time, run=run)
        with mock.patch.object(signal, "signal"), mock.patch.object(signal, "getitimer", return_value=(0, 0)), \
             mock.patch.object(signal, "setitimer") as timer, mock.patch.object(ctypes, "CDLL") as libc, \
             mock.patch.object(os, "getppid", return_value=42), \
             mock.patch.object(socket, "gethostname", return_value="autopilot-lite-vnic"), \
             mock.patch.object(os, "geteuid", return_value=1001), \
             mock.patch.object(os, "stat", return_value=types.SimpleNamespace(st_mode=stat.S_IFREG|0o600, st_uid=1001)), \
             mock.patch.object(os, "access", return_value=True), mock.patch.object(os, "memfd_create", return_value=81), \
             mock.patch.object(os, "fchmod"), mock.patch.object(os, "write", side_effect=lambda fd, raw: len(raw)), \
             mock.patch.object(os, "close"), mock.patch.object(fcntl, "fcntl"), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            libc.return_value.prctl.return_value = 0
            with self.assertRaises(SystemExit) as exit_result:
                exec(compile(tree, "offline_oracle_route", "exec"), ns)
        return exit_result.exception.code, [json.loads(line) for line in out.getvalue().splitlines()], run, timer

    def test_preflight_has_zero_ibm_ssh_and_no_guest_tcp(self):
        rc, rows, run, timer = self.remote("preflight")
        self.assertEqual(0, rc); self.assertEqual([dict(event="ISOLATION_ROUTE_READY", **BINDING, hostpin="MATCH", guest_network_requests=0)], rows)
        self.assertEqual(1, run.call_count); self.assertEqual("/usr/bin/ssh-keygen", run.call_args.args[0][0])

    def test_exactly_one_pinned_ibm_ssh_uses_sudo_n_and_existing_key(self):
        rc, rows, run, timer = self.remote()
        self.assertEqual(0, rc); self.assertEqual(2, run.call_count)
        argv = run.call_args.args[0]
        self.assertEqual(["/usr/bin/ssh", "-F", "/dev/null", "-T", "-i", "/existing/owner-key"], argv[:6])
        self.assertEqual("ubuntu@161.156.86.34", argv[-2]); self.assertEqual(1, argv.count("-i"))
        self.assertEqual(["/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", "-B", "-c", "reviewed_host_fixture = 'quote-safe'\n"], shlex.split(argv[-1]))
        for option in ("StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "ForwardAgent=no", "AddKeysToAgent=no", "UpdateHostKeys=no", "GlobalKnownHostsFile=/dev/null", "HostKeyAlgorithms=ssh-ed25519"):
            self.assertIn(option, argv)
        self.assertTrue(any(token.startswith("UserKnownHostsFile=/proc/") for token in argv))
        self.assertEqual(["RUN_BOUND_UNIT_ISOLATION", "TCP_OPEN", "ISOLATION_SSH_STARTED", "ISOLATION_SSH_FINISHED", "UNIT_ISOLATION_RECEIPT"], [row["event"] for row in rows])
        self.assertEqual(105, run.call_args.kwargs["seconds"]); self.assertEqual(16384, run.call_args.kwargs["limit"])
        self.assertEqual(mock.call(signal.ITIMER_REAL, 0), timer.call_args_list[-1])

    def test_wrong_or_marked_pin_prevents_any_ibm_ssh(self):
        for kwargs in (dict(bad_pin=True), dict(marked=True)):
            with self.subTest(kwargs=kwargs):
                rc, rows, run, timer = self.remote(**kwargs)
                self.assertEqual(3, rc); self.assertEqual(1, run.call_count)
                self.assertEqual("ISOLATION_ROUTE_BLOCKED", rows[-1]["event"])

    def test_rc3_host_receipt_is_exported_without_repeating_ibm_ssh(self):
        rc, rows, run, timer = self.remote(host_rc=3)
        self.assertEqual(3, rc); self.assertEqual(2, run.call_count)
        self.assertEqual("UNIT_ISOLATION_RECEIPT", rows[-1]["event"])
        self.assertEqual("BLOCKED", rows[-1]["receipt"]["result"])


class ProcessBoundsTests(OfflineCase):
    def fake_child(self):
        child = mock.Mock(pid=424242)
        child.stdin, child.stdout, child.stderr = (io.BytesIO() for _ in range(3))
        child.stdout.fileno = mock.Mock(return_value=101)
        child.stderr.fileno = mock.Mock(return_value=102)
        return child

    def remote_run(self):
        tree = ast.parse(p.remote_source())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"]
        ns = dict(os=os, signal=signal, ctypes=ctypes, selectors=p.selectors, subprocess=subprocess,
                  time=time, deadline=time.monotonic()+2,
                  need=lambda value: p.need(value, "transport_timeout"))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "offline_remote_bounded_run", "exec"), ns)
        return ns["run"]

    def test_exit_observation_precedes_group_cleanup_and_final_reap(self):
        for remote in (False, True):
            child = self.fake_child(); order = []
            selector = mock.Mock(); selector.get_map.return_value = {}
            def observe(*args):
                order.append("observe")
                self.assertEqual((os.P_PID, child.pid, os.WEXITED|os.WNOWAIT|os.WNOHANG), args)
                self.assertFalse(child.wait.called)
                return types.SimpleNamespace(si_code=os.CLD_EXITED, si_status=0)
            child.wait.side_effect = lambda **kw: order.append("reap") or 0
            with self.subTest(remote=remote), \
                 mock.patch.object(subprocess, "Popen", return_value=child), \
                 mock.patch.object(p.selectors, "DefaultSelector", return_value=selector), \
                 mock.patch.object(os, "set_blocking"), mock.patch.object(os, "waitid", side_effect=observe), \
                 mock.patch.object(os, "killpg", side_effect=lambda *args: order.append("kill")):
                result = self.remote_run()(["NEVER_EXECUTE"], seconds=1) if remote else p.bounded_process(["NEVER_EXECUTE"], timeout=1)
            self.assertEqual((0, b"", b""), result)
            self.assertEqual(["observe", "kill", "reap"], order)
            self.assertTrue(all(stream.closed for stream in (child.stdin, child.stdout, child.stderr)))

    def test_selector_allocation_failure_prevents_child_creation(self):
        for remote in (False, True):
            with self.subTest(remote=remote), \
                 mock.patch.object(subprocess, "Popen") as child, \
                 mock.patch.object(p.selectors, "DefaultSelector", side_effect=RuntimeError("OFFLINE_SELECTOR_FAILURE")), \
                 mock.patch.object(os, "killpg") as kill, self.assertRaisesRegex(RuntimeError, "OFFLINE_SELECTOR_FAILURE"):
                if remote:
                    self.remote_run()(["NEVER_EXECUTE"], seconds=1)
                else:
                    p.bounded_process(["NEVER_EXECUTE"], timeout=1)
            child.assert_not_called(); kill.assert_not_called()

    def test_process_creation_failure_closes_preallocated_selector(self):
        for remote in (False, True):
            selector = mock.Mock()
            with self.subTest(remote=remote), \
                 mock.patch.object(subprocess, "Popen", side_effect=RuntimeError("OFFLINE_CREATION_FAILURE")), \
                 mock.patch.object(p.selectors, "DefaultSelector", return_value=selector), \
                 mock.patch.object(os, "killpg") as kill, self.assertRaisesRegex(RuntimeError, "OFFLINE_CREATION_FAILURE"):
                if remote:
                    self.remote_run()(["NEVER_EXECUTE"], seconds=1)
                else:
                    p.bounded_process(["NEVER_EXECUTE"], timeout=1)
            selector.close.assert_called_once(); kill.assert_not_called()

    def test_input_and_time_limits_are_checked_before_process_creation(self):
        for payload in (b"x"*(131072+1), "text", bytearray(b"x")):
            with self.subTest(payload_type=type(payload)), self.assertRaisesRegex(p.IsolationError, "input_limit"):
                p.bounded_process(["NEVER_EXECUTE"], payload=payload)
        for seconds in (True, 0, -1, 378.001, math.nan, math.inf, "1"):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(p.IsolationError, "transport_budget"):
                p.bounded_process(["NEVER_EXECUTE"], timeout=seconds)

    def test_exact_input_boundary_passes_to_mocked_child(self):
        with mock.patch.object(p.subprocess, "Popen", side_effect=RuntimeError("MOCK_BOUNDARY_REACHED")) as child:
            with self.assertRaisesRegex(RuntimeError, "MOCK_BOUNDARY_REACHED"):
                p.bounded_process(["NEVER_EXECUTE"], payload=b"x"*131072)
        child.assert_called_once()
        self.assertTrue(child.call_args.kwargs["start_new_session"])
        self.assertEqual({"PATH":"/usr/bin:/bin", "LANG":"C.UTF-8", "LC_ALL":"C.UTF-8"}, child.call_args.kwargs["env"])


class SandboxedProcessCleanupTests(unittest.TestCase):
    """Harmless Python processes only, in the pre-existing offline sandbox."""
    def setUp(self):
        if Path.cwd() != Path("/work/checkout") or not Path("/work/run-mock-checks.py").is_file():
            self.skipTest("Real local fixtures require the existing Bubblewrap/seccomp harness")
        status = Path("/proc/self/status").read_text()
        if "Seccomp:\t2" not in status:
            self.skipTest("Network-denying seccomp filter must be active")

    def call(self, source, **kwargs):
        return p.bounded_process([sys.executable, "-I", "-B", "-c", source], **kwargs)

    def assert_not_running(self, pid):
        deadline = time.monotonic()+1
        while time.monotonic() < deadline:
            try:
                state = Path("/proc/"+str(pid)+"/stat").read_text().split(")", 1)[1].split()[0]
            except FileNotFoundError:
                return
            if state == "Z":
                return
            time.sleep(.01)
        self.fail("Owned fixture process is still alive after cleanup")

    def test_capture_stdin_and_both_outputs_without_parent_credentials(self):
        with mock.patch.dict(os.environ, PRIVATE_SENTINEL="PRIVATE_SENTINEL"):
            rc, out, err = self.call("import os,sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stderr.write(os.environ.get('PRIVATE_SENTINEL','absent'))", payload=b"fixture", timeout=2)
        self.assertEqual((0, b"fixture", b"absent"), (rc, out, err))

    def test_hanging_child_is_killed_and_reaped(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"pid"
            source = "import os,time;open("+repr(str(path))+",'w').write(str(os.getpid()));time.sleep(10)"
            with self.assertRaisesRegex(p.IsolationError, "transport_timeout"):
                self.call(source, timeout=.15)
            self.assert_not_running(int(path.read_text()))

    def test_combined_overoutput_kills_fixture_child(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"pid"
            source = "import os,sys,time;open("+repr(str(path))+",'w').write(str(os.getpid()));sys.stdout.write('x'*128);sys.stdout.flush();sys.stderr.write('y'*128);sys.stderr.flush();time.sleep(10)"
            with self.assertRaisesRegex(p.IsolationError, "output_limit"):
                self.call(source, timeout=2, limit=200)
            self.assert_not_running(int(path.read_text()))

    def test_exited_leader_with_closed_pipe_descendant_is_still_killed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"pid"
            source = "import os,time\npid=os.fork()\nif pid:\n open("+repr(str(path))+",'w').write(str(pid));os._exit(0)\nos.close(0);os.close(1);os.close(2);time.sleep(10)"
            self.assertEqual((0, b"", b""), self.call(source, timeout=2))
            self.assert_not_running(int(path.read_text()))

    def test_exited_leader_with_pipe_holding_descendant_times_out_and_kills_group(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"pid"
            source = "import os,time\npid=os.fork()\nif pid:\n open("+repr(str(path))+",'w').write(str(pid));os._exit(0)\ntime.sleep(10)"
            with self.assertRaisesRegex(p.IsolationError, "transport_timeout"):
                self.call(source, timeout=.15)
            self.assert_not_running(int(path.read_text()))


if __name__ == "__main__":
    unittest.main()
