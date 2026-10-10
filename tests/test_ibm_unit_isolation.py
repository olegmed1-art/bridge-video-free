"""Offline-only maintenance contract: fake systemd and private temporary files."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from ops import ibm_unit_isolation as m


class Clock:
    def __init__(self):
        self.t = 0.0
    def now(self):
        return self.t
    def sleep(self, n):
        self.t += n


def payload():
    return {"schema": m.REQUEST_SCHEMA, "operation": "isolate", "run_id": "123456789",
            "attempt": 1, "head": "a" * 40, "identity": dict(m.IDENTITY), "transport": dict(m.TRANSPORT)}


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.clock = Clock()
        self.calls = []
        self.rows = {}
        self.conditions = {u: [] for u in m.TARGETS}
        self.hook = None
        self.reloads = 0
        self.stop_requested = []
        self.secret = "NEVER_EXPORT_INLINE_SECRET"
        for path in ("etc/systemd/system", "run/systemd/system", "usr/lib/systemd/system", "var/lib", "sys/fs/cgroup", "usr/lib"):
            (self.root / path).mkdir(parents=True, exist_ok=True)
        (self.root / "usr/lib/os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        (self.root / "sys/fs/cgroup/cgroup.controllers").write_text("cpu memory pids\n")
        for unit in m.UNITS:
            active = unit in m.TARGETS
            row = {field: "" for field in m.COMMON + (m.SERVICE if unit.endswith(".service") else ())}
            row.update(Id=unit, Names=unit, LoadState="loaded", ActiveState="active" if active else "inactive",
                       SubState=("waiting" if unit == m.TIMER else "running") if active else "dead",
                       UnitFileState="enabled" if active else "static",
                       FragmentPath="/etc/systemd/system/" + unit,
                       ControlGroup="/system.slice/" + unit if active and unit != m.TIMER else "",
                       Transient="no", NeedDaemonReload="no", StopWhenUnneeded="no", RefuseManualStop="no",
                       FailureAction="none", SuccessAction="none", JobTimeoutAction="none",
                       After="basic.target network-online.target", Before="shutdown.target", Conflicts="shutdown.target")
            if unit.endswith(".service"):
                row.update(MainPID="44" if active else "0", ControlPID="0", ExecStop="", ExecStopPost="", TimeoutStopUSec="15s" if unit == m.CONTROL else "20s",
                           KillMode="control-group", KillSignal="15", SendSIGKILL="yes", FinalKillSignal="9",
                           TimeoutStopFailureMode="terminate", Restart="always")
            if unit == m.TIMER:
                row["Triggers"] = m.HEALTH
            if unit == m.HEALTH:
                row["TriggeredBy"] = m.TIMER
            if unit == m.CONTROL:
                row["Requires"] = m.OBSERVER
            if unit == m.BRIDGE:
                row["Requires"] = m.CONTROL + " " + m.OBSERVER
            if unit == m.OBSERVER:
                row["RequiredBy"] = m.CONTROL + " " + m.BRIDGE
            if unit == m.CONTROL:
                row["RequiredBy"] = m.BRIDGE
            self.rows[unit] = row
            (self.root / row["FragmentPath"].lstrip("/")).write_text("[Unit]\nDescription=fixture\n[Service]\nEnvironment=" + self.secret + "\n")
            if active and unit != m.TIMER:
                group = self.root / ("sys/fs/cgroup" + row["ControlGroup"])
                group.mkdir(parents=True)
                (group / "cgroup.events").write_text("populated 1\nfrozen 0\n")
                (group / "cgroup.procs").write_text("44\n")
        enablement = self.root / "etc/systemd/system/multi-user.target.wants"
        enablement.mkdir()
        (enablement / m.OBSERVER).symlink_to("../" + m.OBSERVER)

    def stop(self, unit):
        row = self.rows[unit]
        row.update(ActiveState="inactive", SubState="dead", Job="")
        if unit.endswith(".service"):
            row.update(MainPID="0", ControlPID="0")
        if row["ControlGroup"]:
            group = self.root / ("sys/fs/cgroup" + row["ControlGroup"])
            (group / "cgroup.events").write_text("populated 0\nfrozen 0\n")
            (group / "cgroup.procs").write_text("")
        row["ControlGroup"] = ""

    def runner(self, argv, *, timeout, limit):
        self.calls.append(list(argv))
        self.clock.t += .002
        if self.hook:
            self.hook(argv)
        if argv[:2] == ["/usr/bin/systemctl", "show"]:
            unit = argv[-1]
            fields = argv[-2].split("=", 1)[1].split(",")
            return ("\n".join(k + "=" + self.rows[unit][k] for k in fields) + "\n").encode()
        if argv[0] == "/usr/bin/busctl":
            obj = argv[5].split("/")[-1]
            unit = next(u for u in m.UNITS if obj == "".join(c if c.isalnum() else "_" + format(ord(c), "02x") for c in u))
            if argv[-1] in ("ExecStop", "ExecStopPost"):
                return json.dumps({"type": "a(sasbttttuii)", "data": [self.rows[unit][argv[-1]]] if self.rows[unit][argv[-1]] else []}).encode()
            return json.dumps({"type": "a(sbbsi)", "data": self.conditions[unit]}).encode()
        if argv == ["/usr/bin/systemctl", "daemon-reload"]:
            self.reloads += 1
            for unit in m.TARGETS:
                directory = self.root / "etc/systemd/system" / (unit + ".d")
                drops = sorted(directory.glob("*.conf")) if directory.exists() else []
                self.rows[unit]["DropInPaths"] = " ".join("/" + str(p.relative_to(self.root)) for p in drops)
                self.conditions[unit] = [r for r in self.conditions[unit] if not (r[0] == "ConditionPathExists" and ".bridge-ibm-isolation-" in r[3])]
                for path in drops:
                    for line in path.read_text().splitlines():
                        if line.startswith("ConditionPathExists="):
                            self.conditions[unit].append(["ConditionPathExists", False, False, line.split("=", 1)[1], 0])
            return b""
        if argv[:3] == ["/usr/bin/systemctl", "--no-block", "stop"]:
            unit = argv[-1]
            self.stop_requested.append(unit)
            self.stop(unit)
            return b""
        raise AssertionError("Unexpected offline runner command")

    def kwargs(self):
        return dict(root=self.root, runner=self.runner, clock=self.clock.now,
                    sleep=self.clock.sleep, host_check=lambda fs: None)


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fx = Fixture(self.temp.name)
        self.network = mock.patch("socket.socket.connect", side_effect=AssertionError("LIVE_NETWORK_FORBIDDEN"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def execute(self, p=None):
        result = m.execute(payload() if p is None else p, **self.fx.kwargs())
        self.assertNotIn(self.fx.secret, json.dumps(result))
        self.assertLess(len(m.canonical(result)), m.MAX_OUTPUT)
        return result

    def assert_blocked(self, result, reason=None):
        self.assertEqual("BLOCKED", result["result"], result)
        if reason:
            self.assertEqual(reason, result["reason"], result)
        self.assertFalse(result["changed_units"])
        self.assertFalse(self.fx.stop_requested)

    def test_success_scope_backup_and_order(self):
        originals = {u: (self.fx.root / self.fx.rows[u]["FragmentPath"].lstrip("/")).read_bytes() for u in m.UNITS}
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        self.assertEqual(list(m.STOP_ORDER), self.fx.stop_requested)
        self.assertEqual(result["snapshot_sha256"], result["snapshot_restore_sha256"])
        self.assertEqual(64, len(result["snapshot_sha256"]))
        archive = self.fx.root / m.RECOVERY_ROOT.lstrip("/") / result["snapshot_id"]
        self.assertEqual(0o700, archive.stat().st_mode & 0o777)
        self.assertEqual(0o400, (archive / "snapshot.json").stat().st_mode & 0o777)
        self.assertIn(self.fx.secret.encode(), __import__('base64').b64decode(json.loads((archive / "snapshot.json").read_text())["records"][0]["data"]))
        for unit in m.TARGETS:
            gate = list((self.fx.root / "etc/systemd/system" / (unit + ".d")).glob("*.conf"))[0]
            self.assertEqual(2, len(gate.read_text().splitlines()))
            marker = gate.read_text().split("=", 1)[1].strip()
            self.assertFalse((self.fx.root / marker.lstrip("/")).exists())
        for unit, data in originals.items():
            self.assertEqual(data, (self.fx.root / self.fx.rows[unit]["FragmentPath"].lstrip("/")).read_bytes())
        self.assertTrue(all(r["cgroup_empty"] for r in result["target_states"]))
        self.assertEqual(1, self.fx.reloads)
        for cmd in self.fx.calls:
            self.assertNotIn("start", cmd)
            self.assertNotIn("restart", cmd)
            self.assertNotIn("disable", cmd)
            self.assertNotIn("mask", cmd)
            self.assertFalse(any("docker" in arg for arg in cmd))

    def test_actual_timer_has_no_controlgroup_property(self):
        del self.fx.rows[m.TIMER]["ControlGroup"]
        original = self.fx.stop
        def stop(unit):
            if unit == m.TIMER:
                self.fx.rows[unit].update(ActiveState="inactive", SubState="dead", Job="")
            else:
                original(unit)
        self.fx.stop = stop
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        timer_queries = [c for c in self.fx.calls if c[:2] == ["/usr/bin/systemctl", "show"] and c[-1] == m.TIMER]
        self.assertTrue(timer_queries)
        self.assertTrue(all("ControlGroup" not in c[-2] for c in timer_queries))

    def test_known_readonly_ben_runtime_docker_handler_allowed(self):
        row = self.fx.rows[m.BEN]
        row.update(Requires="docker.service", After="docker.service network-online.target",
                   ExecStop="{ path=/usr/bin/docker ; argv[]=/usr/bin/docker stop -t 10 bridge-ben ; }",
                   TimeoutStopUSec="30s")
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        self.assertNotIn(m.BEN, self.fx.stop_requested)
        self.assertTrue(all(c[0] != "/usr/bin/docker" for c in self.fx.calls))

    def test_request_rejects_unknown_fields_and_wrong_identity(self):
        cases = []
        p = payload(); p["extra"] = "bad"; cases.append(p)
        p = payload(); p["identity"]["instance_id"] = "wrong"; cases.append(p)
        p = payload(); p["transport"]["strict_host_key_checking"] = "no"; cases.append(p)
        p = payload(); p["transport"]["ibm_hostkey_sha256"] = "wrong"; cases.append(p)
        p = payload(); p["attempt"] = True; cases.append(p)
        p = payload(); p["head"] = "secret\n"; cases.append(p)
        p = payload(); p["run_id"] = "../escape"; cases.append(p)
        for p in cases:
            with self.subTest(p=p):
                self.assert_blocked(self.execute(p), "REQUEST_INVALID")
        self.assertFalse(self.fx.calls)

    def test_strict_json_duplicate_and_nonfinite(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{broken}'):
            with self.assertRaises(m.Blocked):
                m.strict_json(raw)

    def test_alias_unknown_dependency_and_handler(self):
        for field, value, reason in (("Names", m.OBSERVER + " alias.service", "ALIAS_OR_UNIT_MISMATCH"),
                                     ("Requires", "unknown.service", "UNKNOWN_EDGE"),
                                     ("PropagatesStopTo", m.BEN, "UNKNOWN_EDGE"),
                                     ("UpheldBy", m.CONTROL, "UNKNOWN_EDGE"),
                                     ("OnFailure", "recover.service", "UNKNOWN_EDGE"),
                                     ("ExecStop", "/secret --password=secret", "STOP_HANDLER"),
                                     ("ExecStopPost", "/secret", "STOP_HANDLER")):
            original = self.fx.rows[m.OBSERVER][field]
            self.fx.rows[m.OBSERVER][field] = value
            with self.subTest(field=field):
                self.assert_blocked(self.execute(), reason)
            self.fx.rows[m.OBSERVER][field] = original

    def test_unknown_or_oversized_stop_policy(self):
        for field, value in (("TimeoutStopUSec", "infinity"), ("TimeoutStopUSec", "90s"),
                             ("KillMode", "process"), ("SendSIGKILL", "no"),
                             ("RefuseManualStop", "yes"), ("FailureAction", "reboot")):
            old = self.fx.rows[m.OBSERVER][field]
            self.fx.rows[m.OBSERVER][field] = value
            self.assert_blocked(self.execute(), "STOP_POLICY")
            self.fx.rows[m.OBSERVER][field] = old

    def test_busy_or_queued_companion_preflight(self):
        for field, value in (("ActiveState", "active"), ("Job", "9"), ("MainPID", "77")):
            old = self.fx.rows[m.HEALTH][field]
            self.fx.rows[m.HEALTH][field] = value
            self.assert_blocked(self.execute(), "COMPANION_BUSY")
            self.fx.rows[m.HEALTH][field] = old

    def test_root_and_os_required_before_any_write(self):
        with mock.patch.object(m.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(m.Blocked, "ROOT_REQUIRED"):
                m.local_host_check(m.RootFS(self.fx.root))
        fs = m.RootFS(self.fx.root)
        with mock.patch.object(m.platform, "system", return_value="Linux"), mock.patch.object(m.platform, "machine", return_value="x86_64"), mock.patch.object(m.os, "geteuid", return_value=0):
            m.local_host_check(fs)
            (self.fx.root / "usr/lib/os-release").write_text('ID=debian\nVERSION_ID="24.04"\n')
            with self.assertRaisesRegex(m.Blocked, "HOST_MISMATCH"):
                m.local_host_check(fs)

    def test_marker_and_existing_gate_collisions(self):
        change = payload()["run_id"] + "-" + payload()["head"][:12]
        marker = self.fx.root / ("etc/systemd/system/.bridge-ibm-isolation-" + change + ".allow")
        marker.symlink_to("/missing")
        self.assert_blocked(self.execute(), "COLLISION")
        marker.unlink()
        directory = self.fx.root / "etc/systemd/system" / (m.OBSERVER + ".d")
        directory.mkdir()
        (directory / ("90-bridge-ibm-isolation-" + change + ".conf")).write_text("existing")
        self.assert_blocked(self.execute(), "COLLISION")

    def test_symlink_parent_is_never_followed(self):
        directory = self.fx.root / "etc/systemd/system" / (m.OBSERVER + ".d")
        outside = self.fx.root / "outside"
        outside.mkdir()
        directory.symlink_to(outside)
        result = self.execute()
        self.assertEqual("BLOCKED", result["result"], result)
        self.assertEqual([], list(outside.iterdir()))

    def test_snapshot_file_limit(self):
        directory = self.fx.root / "etc/systemd/system" / (m.OBSERVER + ".d")
        directory.mkdir()
        drops = []
        for index in range(33):
            path = directory / (str(index) + ".conf")
            path.write_text("[Unit]\n")
            drops.append("/" + str(path.relative_to(self.fx.root)))
        self.fx.rows[m.OBSERVER]["DropInPaths"] = " ".join(drops)
        self.assert_blocked(self.execute(), "FILE_LIMIT")

    def test_snapshot_byte_limit(self):
        (self.fx.root / self.fx.rows[m.OBSERVER]["FragmentPath"].lstrip("/")).write_bytes(b"x" * (m.MAX_BYTES + 1))
        result = self.execute()
        self.assert_blocked(result)
        self.assertIn(result["reason"], {"FILE_DRIFT", "FILE_LIMIT"})

    def test_backup_failure_no_gate(self):
        original = m.RootFS.write_new
        def write(fs, path, data, **kwargs):
            if path.endswith("/snapshot.json"):
                raise OSError("secret storage error")
            return original(fs, path, data, **kwargs)
        with mock.patch.object(m.RootFS, "write_new", new=write):
            self.assert_blocked(self.execute(), "INTERNAL_BLOCKED")

    def test_restore_mismatch_no_gate(self):
        original = m.RootFS.restore_record
        def restore(fs, record, path):
            original(fs, record, path)
            if record["kind"] == "file":
                with open(Path(fs.root) / path.lstrip("/"), "ab") as out:
                    out.write(b"drift")
        with mock.patch.object(m.RootFS, "restore_record", new=restore):
            self.assert_blocked(self.execute(), "RESTORE_MISMATCH")

    def test_unit_drift_after_snapshot_blocks(self):
        original = m.Isolation.snapshot
        def snapshot(op):
            original(op)
            (self.fx.root / op.baseline[m.CONTROL]["FragmentPath"].lstrip("/")).write_text("drift")
        with mock.patch.object(m.Isolation, "snapshot", new=snapshot):
            self.assert_blocked(self.execute(), "FILE_DRIFT")

    def test_partial_gate_write_retained_and_intent_durable(self):
        original = m.RootFS.write_new
        def write(fs, path, data, **kwargs):
            if path.endswith(".conf") and m.CONTROL + ".d/" in path:
                original(fs, path, data[:8], **kwargs)
                raise OSError("secret partial write")
            return original(fs, path, data, **kwargs)
        with mock.patch.object(m.RootFS, "write_new", new=write):
            result = self.execute()
        self.assertEqual("PARTIAL_BLOCKED", result["result"])
        self.assertEqual([m.OBSERVER, m.CONTROL], result["changed_units"])
        self.assertFalse(self.fx.stop_requested)
        archive = self.fx.root / m.RECOVERY_ROOT.lstrip("/") / result["snapshot_id"]
        events = [json.loads(p.read_text()) for p in sorted(archive.glob("journal-*.json"))]
        self.assertTrue(any(e["event"] == "GATE_INTENT" and e["unit"] == m.CONTROL for e in events))
        self.assertEqual(2, len(list((self.fx.root / "etc/systemd/system").glob("*.d/*.conf"))))

    def test_reload_failure_retains_all_gates(self):
        def hook(argv):
            if argv == ["/usr/bin/systemctl", "daemon-reload"]:
                raise m.Blocked("COMMAND_FAILED")
        self.fx.hook = hook
        result = self.execute()
        self.assertEqual("PARTIAL_BLOCKED", result["result"])
        self.assertEqual(4, len(result["changed_units"]))
        self.assertFalse(self.fx.stop_requested)

    def test_effective_condition_reset_or_negation_blocks_stops(self):
        original = self.fx.runner
        def run(argv, **kwargs):
            answer = original(argv, **kwargs)
            if argv[0] == "/usr/bin/busctl" and argv[-1] == "Conditions" and self.fx.reloads:
                body = json.loads(answer)
                body["data"] = []
                return json.dumps(body).encode()
            return answer
        self.fx.runner = run
        result = self.execute()
        self.assertEqual("GATE_NOT_EFFECTIVE", result["reason"], result)
        self.assertFalse(self.fx.stop_requested)

    def test_preexisting_conditions_are_retained(self):
        self.fx.conditions[m.OBSERVER] = [["ConditionVirtualization", False, True, "container", 1]]
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        self.assertEqual(2, len(self.fx.conditions[m.OBSERVER]))

    def test_timer_race_aborts_without_stopping_companions(self):
        original = self.fx.stop
        def stop(unit):
            original(unit)
            if unit == m.TIMER:
                self.fx.rows[m.HEALTH]["Job"] = "7"
        self.fx.stop = stop
        result = self.execute()
        self.assertEqual("PARTIAL_BLOCKED", result["result"])
        self.assertEqual("COMPANION_BUSY", result["reason"])
        self.assertEqual([m.TIMER], self.fx.stop_requested)

    def test_companion_after_reload_aborts_before_first_stop(self):
        def hook(argv):
            if self.fx.reloads and argv[:2] == ["/usr/bin/systemctl", "show"] and argv[-1] == m.BEN:
                self.fx.rows[m.BEN]["ActiveState"] = "active"
                self.fx.rows[m.BEN]["SubState"] = "running"
        self.fx.hook = hook
        result = self.execute()
        self.assertEqual("COMPANION_BUSY", result["reason"])
        self.assertFalse(self.fx.stop_requested)

    def test_hung_stop_bounded_and_no_later_stops(self):
        self.fx.stop = lambda unit: None
        result = self.execute()
        self.assertEqual("STOP_INCOMPLETE", result["reason"], result)
        self.assertEqual([m.TIMER], self.fx.stop_requested)
        self.assertLess(self.fx.clock.t, m.GUEST_SECONDS)

    def test_populated_recorded_cgroup_blocks(self):
        original = self.fx.stop
        def stop(unit):
            group = self.fx.rows[unit]["ControlGroup"]
            original(unit)
            if unit == m.BRIDGE:
                (self.fx.root / ("sys/fs/cgroup" + group) / "cgroup.events").write_text("populated 1\n")
        self.fx.stop = stop
        result = self.execute()
        self.assertEqual("CGROUP_NOT_EMPTY", result["reason"], result)
        self.assertEqual([m.TIMER, m.BRIDGE], self.fx.stop_requested)

    def test_deadline_prevents_writes(self):
        def hook(argv):
            self.fx.clock.t += 10
        self.fx.hook = hook
        result = self.execute()
        self.assert_blocked(result)
        self.assertIn(result["reason"], {"DEADLINE", "BUDGET_INSUFFICIENT"})

    def test_cancellation_preserves_partial(self):
        original = m.RootFS.write_new
        def write(fs, path, data, **kwargs):
            result = original(fs, path, data, **kwargs)
            if path.endswith(".conf"):
                raise m.Blocked("CANCELLED")
            return result
        with mock.patch.object(m.RootFS, "write_new", new=write):
            result = self.execute()
        self.assertEqual("PARTIAL_BLOCKED", result["result"])
        self.assertEqual("CANCELLED", result["reason"])
        self.assertEqual(1, len(list((self.fx.root / "etc/systemd/system").glob("*.d/*.conf"))))

    def test_new_unloaded_dropin_during_snapshot_is_drift(self):
        original = m.Isolation.snapshot
        def snapshot(op):
            original(op)
            directory = self.fx.root / "etc/systemd/system" / (m.CONTROL + ".d")
            directory.mkdir()
            (directory / "99-foreign-reset.conf").write_text("[Unit]\nConditionPathExists=\n")
        with mock.patch.object(m.Isolation, "snapshot", new=snapshot):
            self.assert_blocked(self.execute(), "FILE_DRIFT")

    def test_enablement_target_drift_blocks(self):
        original = m.Isolation.snapshot
        def snapshot(op):
            original(op)
            path = self.fx.root / "etc/systemd/system/multi-user.target.wants" / m.OBSERVER
            path.unlink()
            path.symlink_to("/etc/systemd/system/" + m.CONTROL)
        with mock.patch.object(m.Isolation, "snapshot", new=snapshot):
            result = self.execute()
        self.assert_blocked(result)
        self.assertIn(result["reason"], {"FILE_DRIFT", "PATH_UNSAFE"})

    def test_xattrs_and_mode_are_restored_in_private_staging(self):
        source = self.fx.root / self.fx.rows[m.OBSERVER]["FragmentPath"].lstrip("/")
        source.chmod(0o640)
        os.setxattr(source, "user.test", b"private-attribute")
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        archive = self.fx.root / m.RECOVERY_ROOT.lstrip("/") / result["snapshot_id"]
        manifest = json.loads((archive / "snapshot.json").read_text())
        index = next(i for i, r in enumerate(manifest["records"]) if r["path"] == self.fx.rows[m.OBSERVER]["FragmentPath"])
        restored = archive / "restore-check" / str(index)
        self.assertEqual(0o640, restored.stat().st_mode & 0o777)
        self.assertEqual(b"private-attribute", os.getxattr(restored, "user.test"))
        self.assertEqual(source.read_bytes(), restored.read_bytes())

    def test_parent_filesystem_boot_device_mismatch_blocks(self):
        original = m.RootFS.mkdir
        def mkdir(fs, path, **kwargs):
            if path == m.RECOVERY_ROOT:
                fs.device += 1
            return original(fs, path, **kwargs)
        with mock.patch.object(m.RootFS, "mkdir", new=mkdir):
            self.assert_blocked(self.execute(), "PATH_UNSAFE")

    def test_handler_added_after_reload_blocks_first_stop(self):
        def hook(argv):
            if self.fx.reloads:
                self.fx.rows[m.BRIDGE]["ExecStopPost"] = "/unknown-handler secret"
        self.fx.hook = hook
        result = self.execute()
        self.assertEqual("STOP_HANDLER", result["reason"], result)
        self.assertEqual("PARTIAL_BLOCKED", result["result"])
        self.assertFalse(self.fx.stop_requested)

    def test_trigger_or_negated_gate_does_not_pass(self):
        original = self.fx.runner
        def run(argv, **kwargs):
            answer = original(argv, **kwargs)
            if argv[0] == "/usr/bin/busctl" and argv[-1] == "Conditions" and self.fx.reloads:
                body = json.loads(answer)
                for row in body["data"]:
                    row[1] = True
                    row[2] = True
                return json.dumps(body).encode()
            return answer
        self.fx.runner = run
        result = self.execute()
        self.assertEqual("GATE_NOT_EFFECTIVE", result["reason"], result)
        self.assertFalse(self.fx.stop_requested)

    def test_readonly_companion_activation_between_service_stops(self):
        original = self.fx.stop
        def stop(unit):
            original(unit)
            if unit == m.BRIDGE:
                self.fx.rows[m.BEN]["Job"] = "7"
        self.fx.stop = stop
        result = self.execute()
        self.assertEqual("COMPANION_BUSY", result["reason"], result)
        self.assertEqual([m.TIMER, m.BRIDGE], self.fx.stop_requested)

    def test_missing_metadata_is_not_defaulted_to_success(self):
        original = self.fx.runner
        def run(argv, **kwargs):
            answer = original(argv, **kwargs)
            if argv[:2] == ["/usr/bin/systemctl", "show"]:
                return b"\n".join(line for line in answer.splitlines() if not line.startswith(b"Job=")) + b"\n"
            return answer
        self.fx.runner = run
        self.assert_blocked(self.execute(), "METADATA_INVALID")

    def test_main_root_refusal_is_one_canonical_json_row(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(m.os, "geteuid", return_value=1000), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(3, m.main(payload()))
        self.assertEqual("", stderr.getvalue())
        result = json.loads(stdout.getvalue())
        self.assertEqual("ROOT_REQUIRED", result["reason"])
        self.assertEqual(m.canonical(result).decode() + "\n", stdout.getvalue())
        self.assertFalse(self.fx.calls)

    def test_main_alarm_and_parent_death_guards_installed_before_execute(self):
        class Lib:
            def prctl(self, *args):
                self.args = args
                return 0
        lib = Lib()
        expected = m.Isolation(payload(), **self.fx.kwargs()).receipt("BLOCKED", "HOST_MISMATCH")
        stdout = io.StringIO()
        with mock.patch.object(m.os, "geteuid", return_value=0), mock.patch.object(m.os, "getppid", return_value=42), mock.patch.object(m.platform, "system", return_value="Linux"), mock.patch.object(m.signal, "getitimer", return_value=(0.0, 0.0)), mock.patch.object(m.signal, "setitimer") as timer, mock.patch.object(m.signal, "signal"), mock.patch.object(m.ctypes, "CDLL", return_value=lib), mock.patch.object(m, "execute", return_value=expected) as execute, contextlib.redirect_stdout(stdout):
            self.assertEqual(3, m.main(payload()))
        self.assertEqual((1, signal.SIGTERM), lib.args)
        self.assertGreater(timer.call_args_list[0].args[1], 88)
        self.assertLessEqual(timer.call_args_list[0].args[1], 89)
        self.assertEqual((signal.ITIMER_REAL, 0), timer.call_args_list[-1].args)
        execute.assert_called_once()

    def test_rollback_removes_only_owned_gates_no_starts(self):
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        p = payload(); p.update(operation="rollback", run_id="987654321", snapshot_id=result["snapshot_id"], snapshot_sha256=result["snapshot_sha256"])
        rolled = m.rollback(p, **self.fx.kwargs())
        self.assertEqual("ROLLED_BACK_CONFIG_ONLY", rolled["result"], rolled)
        self.assertFalse(list((self.fx.root / "etc/systemd/system").glob("*.d/*.conf")))
        self.assertEqual(list(m.STOP_ORDER), self.fx.stop_requested)
        self.assertTrue((self.fx.root / m.RECOVERY_ROOT.lstrip("/") / result["snapshot_id"]).exists())
        self.assertTrue((self.fx.root / "etc/systemd/system/multi-user.target.wants" / m.OBSERVER).is_symlink())

    def test_rollback_drift_refuses_all_removals(self):
        result = self.execute()
        gate = next((self.fx.root / "etc/systemd/system").glob("*.d/*.conf"))
        gate.write_text("changed externally")
        p = payload(); p.update(operation="rollback", run_id="987654321", snapshot_id=result["snapshot_id"], snapshot_sha256=result["snapshot_sha256"])
        rolled = m.rollback(p, **self.fx.kwargs())
        self.assertEqual("BLOCKED", rolled["result"], rolled)
        self.assertEqual(4, len(list((self.fx.root / "etc/systemd/system").glob("*.d/*.conf"))))

    def test_inactive_units_do_not_receive_stop(self):
        for unit in m.TARGETS:
            self.fx.stop(unit)
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        self.assertFalse(self.fx.stop_requested)

    def test_import_has_no_side_effect(self):
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("MUTATION")), mock.patch.object(os, "open", side_effect=AssertionError("FILE_ACCESS")):
            scope = {"__name__": "inert_import"}
            source = Path(m.__file__).read_text()
            exec(compile(source, "inert", "exec"), scope)
        self.assertIn("execute", scope)


class BoundedCommandTests(unittest.TestCase):
    def test_selector_failure_cannot_spawn_helper(self):
        with mock.patch.object(m.selectors, "DefaultSelector", side_effect=OSError("allocation")), mock.patch.object(m.subprocess, "Popen") as popen:
            with self.assertRaises(OSError):
                m.bounded_command(["/never/executed"], timeout=1)
        popen.assert_not_called()

    def test_spawn_failure_closes_selector(self):
        selector = mock.Mock()
        with mock.patch.object(m.selectors, "DefaultSelector", return_value=selector), mock.patch.object(m.subprocess, "Popen", side_effect=OSError("spawn")):
            with self.assertRaises(OSError):
                m.bounded_command(["/never/executed"], timeout=1)
        selector.close.assert_called_once()

    def test_stderr_never_returned(self):
        raw = m.bounded_command([sys.executable, "-I", "-B", "-c", "import sys; print('safe'); print('secret',file=sys.stderr)"], timeout=2)
        self.assertEqual(b"safe\n", raw)

    def test_output_bound(self):
        with self.assertRaisesRegex(m.Blocked, "COMMAND_LIMIT"):
            m.bounded_command([sys.executable, "-I", "-B", "-c", "print('x'*100000)"], timeout=2, limit=1024)

    def test_exited_leader_closed_stdio_descendant_is_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child-pid"
            source = ("import subprocess; p=subprocess.Popen(['" + sys.executable + "','-c','import time;time.sleep(30)'],"
                      "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                      "open(" + repr(str(marker)) + ",'w').write(str(p.pid))")
            self.assertEqual(b"", m.bounded_command([sys.executable, "-I", "-B", "-c", source], timeout=2))
            pid = int(marker.read_text())
            for _ in range(100):
                try:
                    state = Path('/proc/' + str(pid) + '/stat').read_text().split()[2]
                except FileNotFoundError:
                    break
                if state == 'Z':
                    break
                time.sleep(.01)
            else:
                self.fail('Closed-stdio owned descendant remained running')

    def test_hanging_helper_and_descendant_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child-pid"
            source = ("import subprocess,time; p=subprocess.Popen(['" + sys.executable + "','-c','import time;time.sleep(30)']);"
                      "open(" + repr(str(marker)) + ",'w').write(str(p.pid));time.sleep(30)")
            started = time.monotonic()
            with self.assertRaisesRegex(m.Blocked, "DEADLINE"):
                m.bounded_command([sys.executable, "-I", "-B", "-c", source], timeout=.4)
            self.assertLess(time.monotonic() - started, 2)
            pid = int(marker.read_text())
            for _ in range(100):
                try:
                    state = Path('/proc/' + str(pid) + '/stat').read_text().split()[2]
                except FileNotFoundError:
                    break
                if state == 'Z':
                    break
                time.sleep(.01)
            else:
                self.fail('Owned descendant remained running')


if __name__ == "__main__":
    unittest.main()
