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
        self.version = {"type": "s", "data": "255.4-1ubuntu8.8"}
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
                           TimeoutStopFailureMode="terminate", Restart="always",
                           PrivateTmp="yes" if unit in (m.OBSERVER, m.CONTROL, m.BRIDGE, m.HEALTH) else "no")
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
            if argv[-1] == "Version":
                if argv != ["/usr/bin/busctl", "--system", "--json=short", "get-property",
                            "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                            "org.freedesktop.systemd1.Manager", "Version"]:
                    raise AssertionError("Unexpected manager property request")
                return json.dumps(self.version).encode()
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

    def private_tmp_edges(self):
        for unit in (m.OBSERVER, m.CONTROL, m.BRIDGE, m.HEALTH):
            self.rows[unit]["Wants"] = "tmp.mount"
            self.rows[unit]["After"] += " tmp.mount systemd-tmpfiles-setup.service"


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

    def graph_preflight(self, reason="UNKNOWN_EDGE"):
        """The diagnostic may read exact metadata but may perform no writes."""
        with contextlib.ExitStack() as stack:
            writes = [stack.enter_context(mock.patch.object(m.RootFS, method))
                      for method in ("mkdir", "write_new", "restore_record", "unlink_owned")]
            snapshot = stack.enter_context(mock.patch.object(m.Isolation, "snapshot"))
            result = self.execute()
            self.assert_blocked(result, reason)
            for write in writes + [snapshot]:
                write.assert_not_called()
        self.assertIsNone(result["snapshot_id"])
        self.assertIsNone(result["snapshot_sha256"])
        self.assertIsNone(result["snapshot_restore_sha256"])
        self.assertFalse(self.fx.reloads)
        self.assertTrue(all(c[:2] == ["/usr/bin/systemctl", "show"] or c ==
                            ["/usr/bin/busctl", "--system", "--json=short", "get-property",
                             "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                             "org.freedesktop.systemd1.Manager", "Version"] for c in self.fx.calls))
        return result

    def unit_show_calls(self):
        return [c[-1] for c in self.fx.calls if c[:2] == ["/usr/bin/systemctl", "show"]]

    def test_private_tmp_compatibility_adds_exactly_twelve_directed_triples(self):
        accepted = set()
        for unit in m.UNITS:
            for field in m.EDGES:
                for peer in ("tmp.mount", "systemd-tmpfiles-setup.service"):
                    with self.subTest(unit=unit, field=field, peer=peer):
                        row = dict(self.fx.rows[unit])
                        # Even forged PrivateTmp=yes must not authorize BEN or
                        # TIMER. The live show never queries it for a timer.
                        row["PrivateTmp"] = "yes"
                        row[field] = " ".join(filter(None, (row[field], peer)))
                        with self.assertRaisesRegex(m.EdgeBlocked, "UNKNOWN_EDGE"):
                            m.validate_edges(unit, row)
                        try:
                            m.validate_edges(unit, row, systemd_255=True)
                        except m.EdgeBlocked:
                            pass
                        else:
                            accepted.add((unit, field, peer))
        expected = {(unit, field, peer)
                    for unit in (m.OBSERVER, m.CONTROL, m.BRIDGE, m.HEALTH)
                    for field, peer in (("Wants", "tmp.mount"), ("After", "tmp.mount"),
                                        ("After", "systemd-tmpfiles-setup.service"))}
        self.assertEqual(12, len(accepted))
        self.assertEqual(expected, accepted)

    def test_private_tmp_compatibility_requires_yes_and_true_version_gate(self):
        for unit in (m.OBSERVER, m.CONTROL, m.BRIDGE, m.HEALTH):
            for field, peer in (("Wants", "tmp.mount"), ("After", "tmp.mount"),
                                ("After", "systemd-tmpfiles-setup.service")):
                for private_tmp in (None, "no", "", "true", "YES", "disconnected"):
                    row = dict(self.fx.rows[unit])
                    row[field] = peer
                    if private_tmp is None:
                        row.pop("PrivateTmp")
                    else:
                        row["PrivateTmp"] = private_tmp
                    with self.subTest(unit=unit, field=field, value=private_tmp):
                        with self.assertRaisesRegex(m.EdgeBlocked, "UNKNOWN_EDGE"):
                            m.validate_edges(unit, row, systemd_255=True)
                for verified in (False, None, 1, "true"):
                    row = dict(self.fx.rows[unit]); row[field] = peer
                    with self.subTest(unit=unit, field=field, verified=verified):
                        with self.assertRaisesRegex(m.EdgeBlocked, "UNKNOWN_EDGE"):
                            m.validate_edges(unit, row, systemd_255=verified)

    def test_private_tmp_known_edges_isolate_and_preserve_actual_metadata(self):
        self.fx.private_tmp_edges()
        originals = {u: (self.fx.root / self.fx.rows[u]["FragmentPath"].lstrip("/")).read_bytes()
                     for u in m.UNITS}
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        self.assertIsNone(result["graph_evidence"])
        self.assertEqual(list(m.STOP_ORDER), self.fx.stop_requested)
        self.assertEqual(result["snapshot_sha256"], result["snapshot_restore_sha256"])
        archive = self.fx.root / m.RECOVERY_ROOT.lstrip("/") / result["snapshot_id"]
        metadata = json.loads((archive / "snapshot.json").read_text())["metadata"]
        for unit in m.UNITS:
            if unit == m.TIMER:
                self.assertNotIn("PrivateTmp", metadata[unit])
            else:
                self.assertEqual("no" if unit == m.BEN else "yes", metadata[unit]["PrivateTmp"])
            self.assertEqual(originals[unit], (self.fx.root / self.fx.rows[unit]["FragmentPath"].lstrip("/")).read_bytes())
        versions = [c for c in self.fx.calls if c[-1] == "Version"]
        self.assertEqual(1, len(versions))
        self.assertNotIn(self.fx.version["data"], json.dumps(result))
        for cmd in self.fx.calls:
            if cmd[:2] == ["/usr/bin/systemctl", "show"]:
                self.assertEqual(cmd[-1] != m.TIMER, "PrivateTmp" in cmd[-2].split("=", 1)[1].split(","))
            self.assertNotIn("set-property", cmd)

    def test_private_tmp_unknown_peers_keep_bounded_v2_evidence(self):
        self.fx.private_tmp_edges()
        self.fx.rows[m.OBSERVER]["Wants"] += " unexpected.mount"
        self.fx.rows[m.HEALTH]["After"] += " unexpected.service"
        result = self.graph_preflight()
        failures = result["graph_evidence"]["failures"]
        self.assertEqual([(m.OBSERVER, "Wants", ["unexpected.mount"]),
                          (m.HEALTH, "After", ["unexpected.service"])],
                         [(f["unit"], f["property"], f["unexpected"]) for f in failures])
        for failure in failures:
            raw = self.fx.rows[failure["unit"]][failure["property"]]
            self.assertEqual(m.digest(raw.encode()), failure["value_sha256"])
            self.assertEqual(len(raw.split()) - 1, failure["omitted_count"])
        self.assertFalse(result["graph_evidence"]["truncated"])
        self.assertEqual(list(m.UNITS), self.unit_show_calls())

    def test_private_tmp_arbitrary_mounts_and_service_names_stay_blocked(self):
        for unit in (m.OBSERVER, m.CONTROL, m.BRIDGE, m.HEALTH):
            for field in ("Wants", "After"):
                for peer in ("var-tmp.mount", "tmp-other.mount", "home.mount",
                             "systemd-tmpfiles-clean.service", "systemd-tmpfiles-setup-dev.service"):
                    with self.subTest(unit=unit, field=field, peer=peer):
                        row = dict(self.fx.rows[unit]); row[field] = peer
                        with self.assertRaisesRegex(m.EdgeBlocked, "UNKNOWN_EDGE"):
                            m.validate_edges(unit, row, systemd_255=True)

    def test_private_tmp_wrong_or_malformed_property_blocks_before_mutation(self):
        self.fx.private_tmp_edges()
        for unit in (m.OBSERVER, m.CONTROL, m.BRIDGE, m.HEALTH, m.BEN):
            old = self.fx.rows[unit]["PrivateTmp"]
            for value in ("true", "false", "YES", "1", "", "disconnected", self.fx.secret):
                with self.subTest(unit=unit, value=value):
                    self.fx.rows[unit]["PrivateTmp"] = value
                    self.graph_preflight("METADATA_INVALID")
            self.fx.rows[unit]["PrivateTmp"] = old
        self.fx.rows[m.OBSERVER]["PrivateTmp"] = "no"
        result = self.graph_preflight()
        self.assertEqual(["Wants", "After"], [f["property"] for f in result["graph_evidence"]["failures"]])

    def test_private_tmp_missing_property_is_not_defaulted(self):
        original = self.fx.runner
        def run(argv, **kwargs):
            answer = original(argv, **kwargs)
            if argv[:2] == ["/usr/bin/systemctl", "show"] and argv[-1] == m.OBSERVER:
                return b"\n".join(line for line in answer.splitlines() if not line.startswith(b"PrivateTmp=")) + b"\n"
            return answer
        self.fx.runner = run
        self.graph_preflight("METADATA_INVALID")
        self.assertFalse(any(c[-1] == "Version" for c in self.fx.calls))

    def test_private_tmp_unsupported_versions_cannot_authorize_edges(self):
        self.fx.private_tmp_edges()
        for version in ("", "254", "256", "1255", "2550", "v255", "systemd 255",
                        "255.", "255\n", " 255", "255-evil", "255.4 secret", self.fx.secret):
            with self.subTest(version=version):
                self.fx.calls.clear()
                self.fx.version = {"type": "s", "data": version}
                result = self.graph_preflight()
                self.assertEqual(8, len(result["graph_evidence"]["failures"]))
                self.assertEqual(1, sum(c[-1] == "Version" for c in self.fx.calls))
                self.assertEqual(list(m.UNITS), self.unit_show_calls())

    def test_private_tmp_version_requires_exact_bounded_typed_string(self):
        self.fx.private_tmp_edges()
        for value in ({}, {"type": "s"}, {"type": "as", "data": ["255"]},
                      {"type": "s", "data": ["255"]}, {"type": "s", "data": 255},
                      {"type": "s", "data": True}, {"type": "s", "data": "255", "extra": "hidden"},
                      {"type": "s", "data": "255." + "a" * 93}):
            with self.subTest(value=value):
                self.fx.version = value
                result = self.graph_preflight("METADATA_INVALID")
                self.assertIsNone(result["graph_evidence"])

    def test_private_tmp_version_query_is_exact_bounded_and_cached(self):
        self.fx.private_tmp_edges()
        for version in ("255", "255.4", "255.4-1ubuntu8.8"):
            with self.subTest(version=version):
                self.fx.calls.clear()
                self.fx.version = {"type": "s", "data": version}
                limits = []
                def run(argv, **kwargs):
                    limits.append(kwargs)
                    return self.fx.runner(argv, **kwargs)
                args = self.fx.kwargs(); args["runner"] = run
                op = m.Isolation(payload(), **args)
                for _ in range(2):
                    for unit in m.UNITS:
                        op.show(unit)
                self.assertIs(op.systemd_255, True)
                self.assertEqual(1, sum(c[-1] == "Version" for c in self.fx.calls))
                self.assertTrue(all(0 < k["timeout"] <= 3 and k["limit"] == m.MAX_OUTPUT for k in limits))
                self.assertFalse(any("Environment" in c for c in self.fx.calls))
                self.assertFalse(any(c[:2] == ["/usr/bin/systemctl", "show"] and c[-1] not in m.UNITS
                                     for c in self.fx.calls))

    def test_private_tmp_no_or_excluded_roles_do_not_query_manager(self):
        for unit in (m.OBSERVER, m.CONTROL, m.BRIDGE, m.HEALTH):
            self.fx.rows[unit]["PrivateTmp"] = "no"
        self.fx.rows[m.BEN]["PrivateTmp"] = "yes"
        op = m.Isolation(payload(), **self.fx.kwargs())
        for unit in m.UNITS:
            op.show(unit)
        self.assertFalse(any(c[-1] == "Version" for c in self.fx.calls))
        self.assertIsNone(op.systemd_255)

    def test_private_tmp_metadata_drift_after_reload_blocks_all_stops(self):
        def hook(argv):
            if self.fx.reloads:
                self.fx.rows[m.OBSERVER]["PrivateTmp"] = "no"
        self.fx.hook = hook
        result = self.execute()
        self.assertEqual("PARTIAL_BLOCKED", result["result"], result)
        self.assertEqual("FILE_DRIFT", result["reason"], result)
        self.assertFalse(self.fx.stop_requested)

    def test_private_tmp_companion_drift_after_snapshot_blocks_all_gates(self):
        original = m.Isolation.snapshot
        def snapshot(op):
            original(op)
            self.fx.rows[m.HEALTH]["PrivateTmp"] = "no"
        with mock.patch.object(m.Isolation, "snapshot", new=snapshot):
            self.assert_blocked(self.execute(), "FILE_DRIFT")
        self.assertFalse(list((self.fx.root / "etc/systemd/system").glob("*.d/*.conf")))

    def test_private_tmp_does_not_authorize_stop_handlers(self):
        self.fx.private_tmp_edges()
        for field in ("ExecStop", "ExecStopPost"):
            with self.subTest(field=field):
                self.fx.rows[m.OBSERVER][field] = self.fx.secret
                self.assert_blocked(self.execute(), "STOP_HANDLER")
                self.fx.rows[m.OBSERVER][field] = ""
        self.assertFalse(self.fx.reloads)
        self.assertFalse(list((self.fx.root / "etc/systemd/system").glob("*.d/*.conf")))

    def test_private_tmp_old_snapshot_missing_property_cannot_remove_gates(self):
        self.fx.private_tmp_edges()
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        archive = self.fx.root / m.RECOVERY_ROOT.lstrip("/") / result["snapshot_id"]
        path = archive / "snapshot.json"
        manifest = json.loads(path.read_text())
        for row in manifest["metadata"].values():
            row.pop("PrivateTmp", None)
        raw = m.canonical(manifest)
        path.chmod(0o600); path.write_bytes(raw); path.chmod(0o400)
        p = payload(); p.update(operation="rollback", run_id="987654321", snapshot_id=result["snapshot_id"],
                                snapshot_sha256=m.digest(raw))
        with mock.patch.object(m.RootFS, "unlink_owned") as unlink:
            rolled = m.rollback(p, **self.fx.kwargs())
        unlink.assert_not_called()
        self.assertEqual("BLOCKED", rolled["result"], rolled)
        self.assertEqual("FILE_DRIFT", rolled["reason"], rolled)
        self.assertEqual(4, len(list((self.fx.root / "etc/systemd/system").glob("*.d/*.conf"))))
        self.assertEqual(list(m.STOP_ORDER), self.fx.stop_requested)

    def test_private_tmp_rollback_rechecks_version_before_removals(self):
        self.fx.private_tmp_edges()
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        self.fx.version = {"type": "s", "data": "256"}
        self.fx.calls.clear()
        p = payload(); p.update(operation="rollback", run_id="987654321", snapshot_id=result["snapshot_id"],
                                snapshot_sha256=result["snapshot_sha256"])
        with mock.patch.object(m.RootFS, "unlink_owned") as unlink:
            rolled = m.rollback(p, **self.fx.kwargs())
        unlink.assert_not_called()
        self.assertEqual("BLOCKED", rolled["result"], rolled)
        self.assertEqual("UNKNOWN_EDGE", rolled["reason"], rolled)
        self.assertEqual(1, sum(c[-1] == "Version" for c in self.fx.calls))
        self.assertEqual(4, len(list((self.fx.root / "etc/systemd/system").glob("*.d/*.conf"))))
        self.assertEqual(list(m.STOP_ORDER), self.fx.stop_requested)

    def test_graph_private_tmp_no_edges_remain_blocked_with_exact_multiple_unit_evidence(self):
        # Without actual PrivateTmp=yes, even the narrow v255 compatibility
        # cannot accept After edges. Requires=tmp.mount is never permitted.
        for unit in (m.OBSERVER, m.CONTROL, m.BRIDGE):
            self.fx.rows[unit]["PrivateTmp"] = "no"
            self.fx.rows[unit]["Requires"] += " tmp.mount"
            self.fx.rows[unit]["After"] += " tmp.mount systemd-tmpfiles-setup.service"
        result = self.graph_preflight()
        self.assertEqual("bridge.ibm.unit-isolation.receipt.v2", result["schema"])
        evidence = result["graph_evidence"]
        self.assertFalse(evidence["truncated"])
        self.assertEqual(6, len(evidence["failures"]))
        for unit in (m.OBSERVER, m.CONTROL, m.BRIDGE):
            failures = [r for r in evidence["failures"] if r["unit"] == unit]
            self.assertEqual(["Requires", "After"], [r["property"] for r in failures])
            self.assertEqual(["tmp.mount"], failures[0]["unexpected"])
            self.assertEqual(["tmp.mount", "systemd-tmpfiles-setup.service"], failures[1]["unexpected"])
            for row in failures:
                value = self.fx.rows[unit][row["property"]]
                self.assertEqual(m.digest(value.encode()), row["value_sha256"])
                self.assertEqual(len(value.split()), row["token_count"])
                self.assertEqual(row["token_count"] - len(row["unexpected"]), row["omitted_count"])
        self.assertEqual(list(m.UNITS), self.unit_show_calls())
        for state in result["target_states"][:3]:
            self.assertEqual("unknown", state["active"])
            self.assertEqual("unknown", state["sub"])
            self.assertIsNone(state["job_pending"])
            self.assertIsNone(state["cgroup_empty"])

    def test_graph_evidence_disambiguates_same_legacy_receipt(self):
        self.fx.rows[m.OBSERVER]["PrivateTmp"] = "no"
        results = []
        for field, peer in (("Requires", "tmp.mount"), ("After", "systemd-tmpfiles-setup.service")):
            old = self.fx.rows[m.OBSERVER][field]
            self.fx.rows[m.OBSERVER][field] = peer
            results.append(self.graph_preflight())
            self.fx.rows[m.OBSERVER][field] = old
        self.assertNotEqual(results[0]["graph_evidence"], results[1]["graph_evidence"])
        # v1 had no graph field and refused to retain its first rejected row.
        # Removing new evidence again loses the property and peer completely.
        old_receipts = [{k: v for k, v in result.items() if k != "graph_evidence"} for result in results]
        self.assertEqual(old_receipts[0], old_receipts[1])
        self.assertEqual("unknown", old_receipts[0]["target_states"][0]["active"])

    def test_graph_collects_all_six_exact_units_before_any_handler_or_write(self):
        for index, unit in enumerate(m.UNITS):
            self.fx.rows[unit]["OnFailure"] = "unexpected-" + str(index) + ".service"
        result = self.graph_preflight()
        self.assertEqual(list(m.UNITS), [r["unit"] for r in result["graph_evidence"]["failures"]])
        self.assertEqual(list(m.UNITS), self.unit_show_calls())
        self.assertTrue(all(r["active"] == "unknown" for r in result["target_states"]))

    def test_graph_all_relations_and_units_still_reject_unknown_members(self):
        for unit in m.UNITS:
            for field in m.EDGES:
                with self.subTest(unit=unit, field=field):
                    row = dict(self.fx.rows[unit])
                    row[field] = "unreviewed.service"
                    evidence = m.GraphEvidence()
                    with self.assertRaisesRegex(m.EdgeBlocked, "UNKNOWN_EDGE"):
                        m.validate_edges(unit, row, evidence=evidence)
                    self.assertTrue(any(r["property"] == field and r["violation"] == "UNEXPECTED_MEMBER"
                                        for r in evidence.failures))

    def test_graph_duplicate_allowed_member_and_missing_trigger_are_distinct(self):
        self.fx.rows[m.OBSERVER]["After"] = "basic.target basic.target"
        self.fx.rows[m.TIMER]["Triggers"] = ""
        result = self.graph_preflight()
        failure, trigger = result["graph_evidence"]["failures"]
        self.assertEqual("DUPLICATE_MEMBER", failure["violation"])
        self.assertEqual(["basic.target"], failure["unexpected"])
        self.assertEqual(2, failure["token_count"])
        self.assertEqual(1, failure["omitted_count"])
        self.assertEqual("TRIGGER_MISMATCH", trigger["violation"])
        self.assertEqual("Triggers", trigger["property"])
        self.assertEqual(0, trigger["token_count"])
        self.assertEqual([], trigger["unexpected"])
        self.assertEqual(m.digest(b""), trigger["value_sha256"])

    def test_graph_raw_trigger_equality_is_not_relaxed_by_tokenization(self):
        for unit, value in ((m.TIMER, " " + m.HEALTH), (m.OBSERVER, " ")):
            old = self.fx.rows[unit]["Triggers"]
            self.fx.rows[unit]["Triggers"] = value
            result = self.graph_preflight()
            self.assertEqual("TRIGGER_MISMATCH", result["graph_evidence"]["failures"][0]["violation"])
            self.fx.rows[unit]["Triggers"] = old

    def test_graph_cardinality_and_name_caps_are_explicit(self):
        names = ["peer-" + str(i) + ".service" for i in range(33)]
        self.fx.rows[m.OBSERVER]["Requires"] = " ".join(names)
        result = self.graph_preflight()
        evidence = result["graph_evidence"]
        self.assertTrue(evidence["truncated"])
        self.assertEqual(["CARDINALITY", "UNEXPECTED_MEMBER"], [r["violation"] for r in evidence["failures"]])
        for failure in evidence["failures"]:
            self.assertEqual(names[:8], failure["unexpected"])
            self.assertEqual(33, failure["token_count"])
            self.assertEqual(25, failure["omitted_count"])

    def test_graph_invalid_values_are_counted_hashed_and_never_echoed(self):
        invalid = [self.fx.secret, "https://private.example/token", "/bin/sh", "--password=secret",
                   "$(curl)", "x;y.service", "secret=token.service", "\u0430.service", "bad\\x00.service"]
        value = " ".join(invalid + ["tmp.mount"])
        self.fx.rows[m.OBSERVER]["Requires"] = value
        result = self.graph_preflight()
        failure = result["graph_evidence"]["failures"][0]
        self.assertEqual(["tmp.mount"], failure["unexpected"])
        self.assertEqual(len(invalid) + 1, failure["token_count"])
        self.assertEqual(len(invalid), failure["omitted_count"])
        self.assertEqual(m.digest(value.encode()), failure["value_sha256"])
        self.assertFalse(result["graph_evidence"]["truncated"])
        for token in invalid:
            self.assertNotIn(token, json.dumps(result, ensure_ascii=False))

    def test_graph_row_cap_does_not_short_circuit_exact_reads(self):
        for unit in m.UNITS:
            for field in m.EDGES:
                self.fx.rows[unit][field] = "unexpected.service"
        result = self.graph_preflight()
        self.assertTrue(result["graph_evidence"]["truncated"])
        self.assertEqual(16, len(result["graph_evidence"]["failures"]))
        self.assertEqual(list(m.UNITS), self.unit_show_calls())
        self.assertLessEqual(len(m.canonical(result["graph_evidence"])), 8192)

    def test_graph_byte_cap_and_receipt_output_cap(self):
        names = ["u" + str(i) + "-" + "a" * 244 + ".service" for i in range(8)]
        self.assertTrue(all(len(n) == 255 for n in names))
        for unit in m.UNITS:
            for field in ("Requires", "After", "OnFailure"):
                self.fx.rows[unit][field] = " ".join(names)
        result = self.graph_preflight()
        evidence = result["graph_evidence"]
        self.assertTrue(evidence["truncated"])
        self.assertLessEqual(len(m.canonical(evidence)), 8192)
        self.assertLess(len(m.canonical(result)), 16384)
        self.assertTrue(all(r["omitted_count"] == r["token_count"] - len(r["unexpected"])
                            for r in evidence["failures"]))
        self.assertTrue(any(len(r["unexpected"]) < 8 for r in evidence["failures"]))
        self.assertEqual(16, len(evidence["failures"]))
        self.assertEqual(set(m.UNITS), {r["unit"] for r in evidence["failures"]})
        self.assertEqual(list(m.UNITS), self.unit_show_calls())

    def test_graph_evidence_preserved_if_later_metadata_or_deadline_fails(self):
        for failure in ("METADATA_INVALID", "DEADLINE"):
            with self.subTest(failure=failure):
                self.fx.calls.clear()
                self.fx.clock.t = 0
                self.fx.rows[m.OBSERVER]["Requires"] = "tmp.mount"
                if failure == "METADATA_INVALID":
                    self.fx.rows[m.CONTROL]["ActiveState"] = "raw-secret-metadata"
                else:
                    self.fx.rows[m.CONTROL]["ActiveState"] = "active"
                    def hook(argv):
                        if argv[-1] == m.OBSERVER:
                            self.fx.clock.t += 91
                    self.fx.hook = hook
                result = self.graph_preflight(failure)
                self.assertEqual("tmp.mount", result["graph_evidence"]["failures"][0]["unexpected"][0])
                self.assertNotIn("raw-secret-metadata", json.dumps(result))

    def test_graph_invalid_show_payload_never_becomes_evidence_or_valid_state(self):
        original = self.fx.runner
        for corrupt in (lambda b: b + b"Requires=tmp.mount\n",
                        lambda b: b.replace(b"Requires=\n", b"Requires=bad\xff.service\n"),
                        lambda b: b.replace(b"Requires=\n", b"Requires=" + b"a" * 8192 + b"\n"),
                        lambda b: b"x" * (m.MAX_OUTPUT + 1)):
            self.fx.runner = lambda argv, corrupt=corrupt, **kw: corrupt(original(argv, **kw))
            result = self.graph_preflight(None)
            self.assertIn(result["reason"], {"METADATA_INVALID", "COMMAND_LIMIT"})
            self.assertIsNone(result["graph_evidence"])
            self.assertTrue(all(r["active"] == "unknown" for r in result["target_states"]))

    def test_success_scope_backup_and_order(self):
        originals = {u: (self.fx.root / self.fx.rows[u]["FragmentPath"].lstrip("/")).read_bytes() for u in m.UNITS}
        result = self.execute()
        self.assertEqual("ISOLATED_TARGET_SCOPE", result["result"], result)
        self.assertIsNone(result["graph_evidence"])
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
        self.fx.private_tmp_edges()
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
        self.fx.private_tmp_edges()
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
        self.fx.private_tmp_edges()
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
        self.fx.private_tmp_edges()
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


class DiagnosticUnitNameTests(unittest.TestCase):
    def test_exact_safe_unit_spellings(self):
        for name in ("tmp.mount", "systemd-tmpfiles-setup.service", "-.mount", "foo@bar.service",
                     "foo@.service", "sys-devices-virtual-net-eth0.device", r"foo\x2dbar.service",
                     "a" * 247 + ".service"):
            with self.subTest(name=name):
                self.assertTrue(m.is_canonical_unit_name(name))

    def test_noncanonical_or_nonunit_strings_are_rejected(self):
        for name in (None, True, 3, "", ".service", "a" * 248 + ".service", "a.service\n",
                     "a.service b.service", "https://secret.service", "/bin/sh.service", "a/b.service",
                     "\u0430.service", "x@y@z.service", "a.unknown", "TOKEN=secret", "$(id).service",
                     "secret;curl.service", r"a\x2D.service", r"a\x00.service", r"a\q.service"):
            with self.subTest(name=name):
                self.assertFalse(m.is_canonical_unit_name(name))


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
