"""Offline contracts only: no SSH, provider, service, or network execution."""
import ast
import base64
import contextlib
import copy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import unittest
from unittest import mock

from ops import ibm_oracle_watchdog_inventory as g

HEAD = "a" * 40
ENV = {"GITHUB_REPOSITORY": g.REPOSITORY, "GITHUB_REPOSITORY_OWNER": g.OWNER,
       "GITHUB_REPOSITORY_OWNER_ID": g.OWNER_ID, "GITHUB_ACTOR": g.OWNER,
       "GITHUB_ACTOR_ID": g.OWNER_ID, "GITHUB_TRIGGERING_ACTOR": g.OWNER,
       "GITHUB_REF": g.REF, "GITHUB_WORKFLOW_REF": g.WORKFLOW_REF,
       "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
       "GITHUB_SHA": HEAD, "GITHUB_WORKFLOW_SHA": HEAD, "EXPECTED_MAIN_SHA": HEAD,
       "GITHUB_RUN_ID": "12345"}
BINDING = {"head": HEAD, "run_id": "12345", "attempt": 1}
SECRET = "PRIVATE_CREDENTIAL_SENTINEL_" * 3


def remote():
    ns = {"__name__": "offline_fixture"}
    exec(compile(g.REMOTE_SOURCE, "<offline-inventory>", "exec"), ns)
    return ns


def listing(names, files=False, state="enabled", description=SECRET):
    if files:
        rows = [["/etc/systemd/system/" + name, state] for name in names]
    else:
        rows = [[name, description, "loaded", "active", "waiting" if name.endswith(".timer") else "running",
                 "", "/ignored/object/path", 0, "", "/"] for name in names]
    return json.dumps({"type": "a(ss)" if files else "a(ssssssouso)", "data": [rows]}).encode()


class FakeCapture:
    def __init__(self, ns, names=None, unavailable=(), bad_timer=False):
        self.ns, self.names = ns, names or {"system": ["ibm-stop.timer"], "user": []}
        self.unavailable, self.bad_timer = set(unavailable), bad_timer
        self.count = self.used = 0
        self.calls = []

    def run(self, argv):
        if self.count >= self.ns["MAX_COMMANDS"]:
            raise self.ns["Limited"]("COMMAND_CAP")
        self.count += 1
        self.calls.append(argv)
        scope = "user" if "--user" in argv else "system"
        if scope in self.unavailable:
            return 1, SECRET.encode()
        if "GetNameOwner" in argv:
            raw = b's ":1.2"\n'
        elif "ListUnitsByPatterns" in argv:
            raw = listing(self.names[scope])
        elif "ListUnitFilesByPatterns" in argv:
            raw = listing(self.names[scope], files=True)
        elif "get-property" in argv:
            raw = SECRET.encode() if self.bad_timer else b's "ibm-stop.service"\nt 1791713000000000\nt 3000000\nt 1\n'
        else:
            raise AssertionError("UNREVIEWED_COMMAND")
        self.used += len(raw)
        return 0, raw


class InventoryContracts(unittest.TestCase):
    def setUp(self):
        self.ns = remote()
        patcher = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("LIVE_NETWORK_FORBIDDEN"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def observation(self, **kwargs):
        capture = FakeCapture(self.ns, **kwargs)
        value = self.ns["collect"](capture)
        return value, capture

    def validate(self, value):
        return g.validate_remote(json.dumps(value).encode())

    def test_canonical_names_instances_templates_and_rejections(self):
        for name in ("ibm-stop.timer", "bridge-school-ibm-backstop.service", "bridge-school-watchdog.timer",
                     "ibm-stop@run123.timer", "ibm-stop@.service", "bridge-watchdog@run_1.timer"):
            self.assertTrue(self.ns["canonical"](name), name)
        for name in ("sshd.service", "bridge-dashboard.service", "ibmsecret.service", "ibm-stop.socket",
                     "../ibm-stop.timer", "ibm-stop@../../key.timer", "ibm-stop@secret=key.timer",
                     "ibm-stop\\x2dfoo.timer", "ibm-stop.timer\nEnvironment=secret", "ibm-" + "x" * 100 + ".timer"):
            self.assertFalse(self.ns["canonical"](name), name)
        self.assertEqual("/org/freedesktop/systemd1/unit/ibm_2dstop_40run_5f1_2etimer",
                         self.ns["object_path"]("ibm-stop@run_1.timer"))

    def test_real_busctl_listing_schema_and_redaction(self):
        rows, reason = self.ns["lists"](listing(["ibm-stop.timer"]), "list-units")
        self.assertIsNone(reason)
        self.assertEqual("waiting", rows["ibm-stop.timer"]["sub"])
        self.assertNotIn(SECRET, json.dumps(rows))
        files, reason = self.ns["lists"](listing(["ibm-stop.timer"], True), "list-unit-files")
        self.assertEqual("enabled", files["ibm-stop.timer"]["unitfile"])
        self.assertNotIn("/etc", json.dumps(files))

    def test_malformed_duplicate_and_oversize_lists_stay_partial(self):
        fixtures = [b'{"type":"a(ss)","type":"a(ss)","data":[[]]}', b"secret",
                    listing(["ibm-stop.timer", "ibm-stop.timer"]), listing(["sshd.service"]),
                    json.dumps({"type": "a(ssssssouso)", "data": [[["ibm-stop.timer"]]]}).encode()]
        for raw in fixtures:
            _, reason = self.ns["lists"](raw, "list-units")
            self.assertEqual("MALFORMED_LIST", reason)
        _, reason = self.ns["lists"](listing(["ibm-stop.timer"] * 65), "list-units")
        self.assertEqual("ROW_CAP", reason)

    def test_unknown_states_never_export_arbitrary_strings(self):
        raw = json.loads(listing(["ibm-stop.timer"]))
        raw["data"][0][0][3] = SECRET
        rows, reason = self.ns["lists"](json.dumps(raw).encode(), "list-units")
        self.assertEqual("MALFORMED_LIST", reason)
        self.assertEqual("UNKNOWN", rows["ibm-stop.timer"]["active"])
        self.assertNotIn(SECRET, json.dumps(rows))

    def test_typed_timer_uint64_zero_infinity_stale_and_out_of_scope_trigger(self):
        timer = self.ns["timer"](b's "ibm-stop@run1.service"\nt 0\nt 18446744073709551615\nt 1\n')
        self.assertIsNone(timer["next_realtime_us"])
        self.assertIsNone(timer["next_monotonic_us"])
        self.assertEqual(1, timer["last_trigger_realtime_us"])
        self.assertEqual("COMPLETE", timer["status"])
        timer = self.ns["timer"](b's "sshd.service"\nt 1\nt 2\nt 3\n')
        self.assertEqual("PARTIAL", timer["status"])
        self.assertIsNone(timer["trigger_unit"])
        for raw in (b's "ibm-stop.service"\nt -1\nt 2\nt 3\n', b's "ibm-stop.service"\nt 18446744073709551616\nt 2\nt 3\n',
                    b's "ibm-stop.service"\ns "2026-10-11"\nt 2\nt 3\n', b's "ibm-stop.service"\nt 1\nt 2\n',
                    b's "ibm-stop.service"\nt 1\nt 2\nt 3\nExecStart=secret\n'):
            with self.assertRaises(self.ns["Limited"]):
                self.ns["timer"](raw)

    def test_command_scope_and_no_service_autoactivation(self):
        for scope in ("system", "user"):
            owner = self.ns["owner_command"](scope)
            self.assertIn("--auto-start=no", owner)
            self.assertEqual("GetNameOwner", owner[-3])
            for kind in ("list-units", "list-unit-files"):
                argv = self.ns["list_command"](scope, kind, ":1.2")
                self.assertIn("--auto-start=no", argv)
                self.assertIn("--allow-interactive-authorization=no", argv)
                self.assertIn("--json=short", argv)
                self.assertEqual(":1.2", argv[argv.index("call") + 1])
            argv = self.ns["property_command"](scope, "ibm-stop.timer", ":1.2")
            self.assertEqual(":1.2", argv[argv.index("get-property") + 1])
            self.assertEqual(list(self.ns["TIMER_PROPERTIES"]), argv[-4:])
            self.assertNotIn("Environment", " ".join(argv))
            with self.assertRaises(self.ns["Limited"]):
                self.ns["property_command"](scope, "ibm-stop.timer", "org.freedesktop.systemd1")
        with self.assertRaises(self.ns["Limited"]):
            self.ns["owner_value"](b's "secret"\n')

    def test_complete_observation_remains_unproven_even_with_stale_trigger(self):
        value, cap = self.observation()
        self.validate(value)
        self.assertEqual("COMPLETE", value["discovery"])
        self.assertFalse(value["watchdog_ready"])
        for key in ("installed_credentials", "independent_deadline", "stop_capability"):
            self.assertEqual("UNPROVEN", value[key])
        self.assertNotIn(SECRET, json.dumps(value))
        self.assertLessEqual(cap.count, g.REMOTE_COMMANDS)
        self.assertGreater(value["sample_started_realtime_us"], 0)
        self.assertGreaterEqual(value["sample_finished_monotonic_us"], value["sample_started_monotonic_us"])

    def test_unavailable_user_is_unknown_not_empty_complete(self):
        value, cap = self.observation(unavailable=("user",))
        self.validate(value)
        self.assertEqual("PARTIAL", value["discovery"])
        self.assertEqual("UNKNOWN", value["managers"]["user"]["status"])
        user_calls = [argv for argv in cap.calls if "--user" in argv]
        self.assertEqual(1, len(user_calls))
        self.assertIn("GetNameOwner", user_calls[0])

    def test_both_unavailable_and_malformed_timer_are_never_ready(self):
        value, _ = self.observation(unavailable=("system", "user"))
        self.validate(value)
        self.assertEqual("UNKNOWN", value["discovery"])
        value, _ = self.observation(bad_timer=True)
        self.validate(value)
        self.assertEqual("PARTIAL", value["discovery"])
        self.assertNotIn(SECRET, json.dumps(value))

    def test_names_details_timer_and_total_command_caps(self):
        names = ["ibm-aaa%d.timer" % i for i in range(30)] + ["ibm-stop@run1.timer", "ibm-watchdog.timer"]
        value, cap = self.observation(names={"system": names, "user": names})
        self.validate(value)
        self.assertEqual(g.REMOTE_COMMANDS, cap.count)
        self.assertLessEqual(cap.count + g.RUNNER_COMMANDS, 16)
        for manager in value["managers"].values():
            self.assertEqual("PARTIAL", manager["status"])
            self.assertEqual(24, len(manager["discovered_names"]))
            self.assertEqual(2, len(manager["units"]))
            self.assertTrue({row["name"] for row in manager["units"]} <= set(manager["discovered_names"]))
            self.assertIn("TIMER_CAP", manager["reasons"])
            self.assertIn("ibm-stop@run1.timer", manager["discovered_names"])
        self.assertLess(len(json.dumps(value).encode()), g.RECEIPT_BYTES)

    def test_missing_unitfile_metadata_is_explicit_partial(self):
        cap = FakeCapture(self.ns)
        original = cap.run
        def run(argv):
            rc, raw = original(argv)
            return (rc, listing([], True)) if "ListUnitFilesByPatterns" in argv else (rc, raw)
        cap.run = run
        value = self.ns["collect"](cap)
        self.validate(value)
        self.assertEqual("PARTIAL", value["discovery"])
        self.assertEqual("UNKNOWN", value["managers"]["system"]["units"][0]["unitfile"])

    def test_schema_rejects_fields_claims_name_mismatch_and_bool_numbers(self):
        original, _ = self.observation()
        mutations = [lambda v: v.update(Environment=SECRET), lambda v: v.update(watchdog_ready=True),
                     lambda v: v.update(stop_capability="PROVEN"), lambda v: v.update(commands=True),
                     lambda v: v["managers"]["system"].update(discovered_names=[]),
                     lambda v: v["managers"]["system"].update(units=[]),
                     lambda v: v["managers"]["system"]["units"][0]["timer"].update(next_realtime_us=True)]
        for change in mutations:
            value = copy.deepcopy(original)
            change(value)
            with self.assertRaises(g.InventoryError):
                self.validate(value)
        with self.assertRaises(g.InventoryError):
            g.validate_remote(b'{"schema":"a","schema":"b"}')

    def test_no_private_file_or_dynamic_package_reads_in_remote(self):
        tree = ast.parse(g.REMOTE_SOURCE)
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual({"ctypes", "json", "os", "pwd", "re", "selectors", "signal", "socket", "subprocess", "sys", "time"}, imports)
        for forbidden in ("sudo", "lstat", "readlink", "journalctl", "Environment=", "ExecStart", "ibm-diagnostic-v9", "ORACLE_SSH_PRIVATE_KEY"):
            self.assertNotIn(forbidden, g.REMOTE_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "eval", "exec", "__import__"})

    def test_host_identity_rejects_before_any_command(self):
        fake_lib = mock.Mock()
        fake_lib.prctl.return_value = 0
        with mock.patch.object(self.ns["ctypes"], "CDLL", return_value=fake_lib), \
             mock.patch.object(os, "getuid", return_value=0), mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(self.ns["signal"], "signal"), mock.patch.object(self.ns["signal"], "setitimer"), \
             mock.patch.object(subprocess, "Popen") as popen, contextlib.redirect_stdout(io.StringIO()) as out:
            self.ns["remote_main"]()
        popen.assert_not_called()
        value = json.loads(out.getvalue())
        self.assertEqual("HOST_IDENTITY", value["reason"])
        self.assertEqual("UNKNOWN", value["discovery"])
        self.validate(value)


class RunnerContracts(unittest.TestCase):
    def context(self, env=None, responses=None):
        responses = responses or [(0, (HEAD + "\n").encode()), (0, b""), (0, (HEAD + "\t" + g.REF + "\n").encode())]
        with mock.patch.dict(os.environ, env or ENV, clear=True), mock.patch.object(g, "bounded_process", side_effect=responses):
            return g.context(g.Budget(time.monotonic() + 10, 16384))

    def test_exact_context_and_all_owner_identity_inputs(self):
        self.assertEqual(BINDING, self.context())
        for key in ENV:
            env = dict(ENV, **{key: "WRONG"})
            with self.subTest(key=key), self.assertRaises(g.InventoryError):
                self.context(env)

    def test_checkout_and_fresh_main_fail_closed(self):
        for responses in ([(0, b"wrong\n")], [(0, (HEAD + "\n").encode()), (1, b"")],
                          [(0, (HEAD + "\n").encode()), (0, b""), (0, ("b" * 40 + "\t" + g.REF + "\n").encode())]):
            with self.assertRaises(g.InventoryError):
                self.context(responses=responses)

    def test_context_failure_before_key_read_and_ssh(self):
        with mock.patch.dict(os.environ, {"ORACLE_SSH_PRIVATE_KEY": SECRET}, clear=True), \
             mock.patch.object(g, "memory") as memory, mock.patch.object(g, "bounded_process") as child:
            with self.assertRaises(g.InventoryError):
                g.run_inventory(g.Budget(time.monotonic() + 10, 16384))
            self.assertEqual(SECRET, os.environ["ORACLE_SSH_PRIVATE_KEY"])
        memory.assert_not_called()
        child.assert_not_called()

    def test_second_context_failure_prevents_ssh_and_closes_memfds(self):
        with mock.patch.dict(os.environ, {"ORACLE_SSH_PRIVATE_KEY": SECRET}, clear=True), \
             mock.patch.object(g, "context", side_effect=[BINDING, g.InventoryError("CURRENT_MAIN")]), \
             mock.patch.object(g, "pinned_scan", return_value=b"public"), \
             mock.patch.object(g, "bounded_process", return_value=(0, b"public")) as child, \
             mock.patch.object(g, "memory", side_effect=[501, 502]), mock.patch.object(os, "close") as close:
            with self.assertRaises(g.InventoryError):
                g.run_inventory(g.Budget(time.monotonic() + 10, 16384))
            self.assertNotIn("ORACLE_SSH_PRIVATE_KEY", os.environ)
        self.assertEqual(1, child.call_count)
        self.assertEqual([mock.call(502), mock.call(501)], close.call_args_list)

    def test_strict_ssh_one_fixed_destination_and_no_agent(self):
        argv = g.ssh_command(100, 101)
        self.assertEqual(["ubuntu@92.5.47.149", "/usr/bin/python3 -I -B -"], argv[-2:])
        for flag in ("ConnectionAttempts=1", "IdentityAgent=none", "StrictHostKeyChecking=yes", "ForwardAgent=no",
                     "IdentitiesOnly=yes", "GlobalKnownHostsFile=/dev/null", "AddKeysToAgent=no"):
            self.assertIn(flag, argv)
        self.assertNotIn(SECRET, repr(argv))

    def test_host_pin_exact_and_no_raw_scan_export(self):
        public = b"fixture-ed25519-public-key"
        token = base64.b64encode(public).decode()
        fp = "SHA256:" + base64.b64encode(hashlib.sha256(public).digest()).decode().rstrip("=")
        raw = (g.ORACLE + " ssh-ed25519 " + token + "\n").encode()
        with mock.patch.object(g, "ORACLE_FP", fp):
            self.assertEqual(raw, g.pinned_scan(raw))
            for bad in (raw + raw, raw.replace(g.ORACLE.encode(), b"other-host"), b"@cert-authority anything", SECRET.encode()):
                with self.assertRaises(g.InventoryError):
                    g.pinned_scan(bad)
        with self.assertRaises(g.InventoryError):
            g.pinned_scan(raw)

    @unittest.skipUnless(hasattr(fcntl, "F_ADD_SEALS"), "Host Python does not expose Linux memfd seal constants")
    def test_sealed_memory_no_disk_key_and_no_rewrite(self):
        fd = g.memory(bytearray(b"synthetic-key-material"), "offline-fixture")
        try:
            self.assertEqual(0o600, os.fstat(fd).st_mode & 0o777)
            seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
            self.assertEqual(15, seals & 15)
            with self.assertRaises(OSError):
                os.write(fd, b"overwrite")
        finally:
            os.close(fd)

    def test_cli_rejects_paths_and_exports_only_enum_failure(self):
        for argv in (["/home/ubuntu/private-key"], []):
            with mock.patch.object(g, "run_inventory", side_effect=RuntimeError(SECRET)) as run, \
                 contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(1, g.main(argv))
            value = json.loads(out.getvalue())
            self.assertEqual("ARGUMENTS" if argv else "INTERNAL", value["reason"])
            self.assertNotIn(SECRET, out.getvalue())
            self.assertEqual("", err.getvalue())
            self.assertFalse(value["watchdog_ready"])
            if argv:
                run.assert_not_called()

    def test_success_receipt_and_single_ssh_attempt_mock_only(self):
        ns = remote()
        observation = ns["collect"](FakeCapture(ns))
        with mock.patch.dict(os.environ, {"ORACLE_SSH_PRIVATE_KEY": SECRET}, clear=True), \
             mock.patch.object(g, "context", return_value=BINDING) as context, \
             mock.patch.object(g, "pinned_scan", return_value=b"public"), \
             mock.patch.object(g, "memory", side_effect=[501, 502]), mock.patch.object(os, "close"), \
             mock.patch.object(g, "bounded_process", side_effect=[(0, b"public"), (0, json.dumps(observation).encode())]) as child:
            result = g.run_inventory(g.Budget(time.monotonic() + 10, 16384))
        self.assertEqual(2, context.call_count)
        self.assertEqual(2, child.call_count)
        self.assertEqual("/usr/bin/ssh", child.call_args_list[1].args[0][0])
        self.assertEqual(g.REMOTE_SOURCE.encode(), child.call_args_list[1].kwargs["payload"])
        self.assertFalse(result["raw_output_exported"])
        self.assertNotIn(SECRET, json.dumps(result))


class PhysicalBudgetContracts(unittest.TestCase):
    def child(self, code):
        return [sys.executable, "-I", "-B", "-c", code]

    def test_runner_stderr_counts_physically_and_stops_at_exact_limit(self):
        budget = g.Budget(time.monotonic() + 3, 73)
        with self.assertRaisesRegex(g.InventoryError, "OUTPUT_CAP"):
            g.bounded_process(self.child("import os;os.write(2,b'x'*10000)"), budget)
        self.assertEqual(73, budget.captured)

    def test_remote_stderr_counts_physically_and_cumulative_cap(self):
        ns = remote()
        ns["CAP"] = 73
        cap = ns["Capture"](time.monotonic() + 3, os.getuid())
        cap.run(self.child("import os;os.write(1,b'a'*11);os.write(2,b'b'*17)"))
        self.assertEqual(28, cap.used)
        with self.assertRaisesRegex(ns["Limited"], "OUTPUT_CAP"):
            cap.run(self.child("import os;os.write(2,b'x'*10000)"))
        self.assertEqual(73, cap.used)
        self.assertEqual(128 * 1024, g.RUNNER_CAPTURE_BYTES + g.REMOTE_CAPTURE_BYTES)

    def test_absolute_deadline_not_reset_and_post_exit_check(self):
        budget = g.Budget(time.monotonic() + .15, 1024)
        with self.assertRaisesRegex(g.InventoryError, "DEADLINE"):
            g.bounded_process(self.child("import time;time.sleep(10)"), budget, seconds=10)
        before = budget.started
        with self.assertRaisesRegex(g.InventoryError, "DEADLINE"):
            g.bounded_process(self.child("pass"), budget)
        self.assertEqual(before, budget.started)
        ns = remote()
        cap = ns["Capture"](time.monotonic() - 1, os.getuid())
        with self.assertRaisesRegex(ns["Limited"], "DEADLINE"):
            cap.run(self.child("pass"))
        self.assertEqual(0, cap.count)

    def test_command_limits_no_extra_spawn(self):
        budget = g.Budget(time.monotonic() + 2, 100, commands=0)
        with mock.patch.object(subprocess, "Popen") as popen, self.assertRaisesRegex(g.InventoryError, "COMMAND_CAP"):
            g.bounded_process(self.child("pass"), budget)
        popen.assert_not_called()
        ns = remote()
        cap = ns["Capture"](time.monotonic() + 2, os.getuid())
        cap.count = ns["MAX_COMMANDS"]
        with mock.patch.object(subprocess, "Popen") as popen, self.assertRaisesRegex(ns["Limited"], "COMMAND_CAP"):
            cap.run(self.child("pass"))
        popen.assert_not_called()

    def test_selector_setup_failure_kills_owned_child(self):
        created = []
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            created.append(child)
            return child
        for remote_mode in (False, True):
            ns = remote()
            with mock.patch.object(subprocess, "Popen", side_effect=spawn), \
                 mock.patch.object(g.selectors, "DefaultSelector", side_effect=RuntimeError("fixture")):
                with self.assertRaises(RuntimeError):
                    if remote_mode:
                        ns["Capture"](time.monotonic() + 2, os.getuid()).run(self.child("import time;time.sleep(10)"))
                    else:
                        g.bounded_process(self.child("import time;time.sleep(10)"), g.Budget(time.monotonic() + 2, 1024))
            self.assertIsNotNone(created[-1].poll())
            self.assertTrue(created[-1].stdout.closed)
            self.assertTrue(created[-1].stderr.closed)

    def test_children_inherit_blocked_signals_and_parent_mask_restored(self):
        before = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        rc, raw = g.bounded_process(self.child("import signal; m=signal.pthread_sigmask(signal.SIG_BLOCK,set());print(int(signal.SIGALRM in m and signal.SIGTERM in m))"),
                                    g.Budget(time.monotonic() + 2, 1024))
        self.assertEqual((0, b"1\n"), (rc, raw))
        self.assertEqual(before, signal.pthread_sigmask(signal.SIG_BLOCK, set()))


if __name__ == "__main__":
    unittest.main()
