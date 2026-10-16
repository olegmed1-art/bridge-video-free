"""Offline tests; real fork/flock/fsync only in isolated temporary directories."""
import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from ops import ibm_oracle_lease_qualification as q

HEAD = "a" * 40
BINDING = {"head": HEAD, "run_id": "12345", "attempt": 1}
ENV = {"GITHUB_REPOSITORY": q.transport.REPOSITORY, "GITHUB_REPOSITORY_OWNER": q.transport.OWNER,
       "GITHUB_REPOSITORY_OWNER_ID": q.transport.OWNER_ID, "GITHUB_ACTOR": q.transport.OWNER,
       "GITHUB_ACTOR_ID": q.transport.OWNER_ID, "GITHUB_TRIGGERING_ACTOR": q.transport.OWNER,
       "GITHUB_REF": q.transport.REF, "GITHUB_WORKFLOW_REF": q.WORKFLOW_REF,
       "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
       "GITHUB_SHA": HEAD, "GITHUB_WORKFLOW_SHA": HEAD, "EXPECTED_MAIN_SHA": HEAD,
       "GITHUB_RUN_ID": "12345"}
SECRET = "QUALIFICATION_CREDENTIAL_SENTINEL_" * 3


def remote():
    return q.remote_namespace()


def metadata():
    return [{"path": p, "uid": 0 if i < 2 else 1001, "mode": "0755", "device": os.makedev(8, 1), "inode": i+1}
            for i, p in enumerate(("/", "/home", "/home/ubuntu", "/home/ubuntu/.local", "/home/ubuntu/.local/share", q.PARENT))]


def mount():
    return {"filesystem": "ext4", "mountpoint": "/", "device_major": 8, "device_minor": 1,
            "classification": "LOCAL_BLOCK_BACKED_BY_MOUNT_METADATA", "mount_root": "/", "writable": True}


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.ns = remote()
        self.ns["UID"] = os.getuid()  # Local fixtures never impersonate UID 1001.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.fd = os.open(self.parent, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        patch = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK_FORBIDDEN"))
        patch.start()
        self.addCleanup(patch.stop)

    def budget(self, seconds=8):
        return self.ns["Budget"](time.monotonic() + seconds)

    def exercises(self):
        budget = self.budget()
        clients = self.ns["Clients"](budget, self.fd)
        try:
            trials = self.ns["exercise"](self.fd, BINDING, budget, clients)
        finally:
            clients.close()
        return trials, budget

    def fixture_collect(self, *, create_fail=False, mutate=None):
        ns, parent = self.ns, self.parent
        real_namespace = ns["Namespace"]
        class FixtureNamespace(real_namespace):
            def open(self):
                self.fds = [os.open(parent, ns["FLAGS"])]
                self.metadata = metadata()
                return self
            def recheck(self):
                return None
            def create(self, binding):
                if create_fail:
                    self.name = ns["child_name"](binding)
                    os.mkdir(self.name, 0o700, dir_fd=self.fds[-1])
                    self.created = True
                    raise OSError("fixture post-mkdir open failure")
                return super().create(binding)
        patches = [mock.patch.dict(ns, Namespace=FixtureNamespace, mount_metadata=lambda *_: mount()),
                   mock.patch.object(ns["pwd"], "getpwuid", return_value=type("User", (), {"pw_name": "ubuntu"})()),
                   mock.patch.object(ns["socket"], "gethostname", return_value="autopilot-lite-vnic")]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            if mutate:
                mutate(stack)
            return ns["collect"](BINDING)

    def test_binding_and_source_bound_name(self):
        name = self.ns["child_name"](BINDING)
        self.assertEqual(name, "qualification-12345-1-" + HEAD)
        for bad in ({**BINDING, "attempt": True}, {**BINDING, "attempt": 2}, {**BINDING, "head": "../x"},
                    {**BINDING, "run_id": "0"}, {**BINDING, "run_id": "1;id"}, {**BINDING, "x": 1}):
            with self.subTest(bad=bad), self.assertRaisesRegex(self.ns["Blocked"], "BINDING"):
                self.ns["child_name"](bad)

    def test_real_atomic_exchange_and_crash_before_after_commit(self):
        trials, budget = self.exercises()
        self.assertEqual([x["phase"] for x in trials], ["before", "after"])
        self.assertTrue(all(x["contender_blocked"] and x["recovered"] for x in trials))
        self.assertEqual(budget.clients, 4)
        self.assertLess(budget.captured, 20000)
        content = lambda name: json.loads((self.parent / name).read_bytes())
        self.assertEqual(content("current.json")["version"], 2)
        self.assertEqual(content("pending-before.json")["version"], 1)
        self.assertEqual(content("pending-after.json")["version"], 0)
        for i in range(3):
            self.assertEqual(content("version%03d.json" % i)["version"], i)
        self.assertEqual(os.stat(self.parent / "current.json").st_ino, os.stat(self.parent / "version002.json").st_ino)
        self.assertEqual(os.stat(self.parent / "pending-after.json").st_ino, os.stat(self.parent / "version000.json").st_ino)
        self.assertEqual(content("before-contender.json")["current_sha256"], trials[0]["current_sha256"])
        self.assertEqual(content("after-contender.json")["current_sha256"], trials[1]["current_sha256"])

    def test_collect_real_fixture_retains_proof_and_validates_schema(self):
        value = self.fixture_collect()
        self.assertEqual(value["result"], "QUALIFIED", value)
        self.assertEqual(value["phase"], "COMPLETE")
        self.assertTrue(value["scope_created"] and value["proof_retained"])
        self.assertFalse(value["ssh_disconnect_tested"] or value["power_loss_tested"] or value["reboot_tested"])
        self.assertEqual(value["persistence"], "MOUNT_CLASSIFIED_FSYNC_READBACK_ONLY")
        self.assertEqual(len(value["retained_entries"]), len(self.ns["FILES"]))
        root = self.parent / self.ns["child_name"](BINDING)
        for artifact in value["artifacts"]:
            raw = (root / artifact["name"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
            self.assertEqual(len(raw), artifact["bytes"])
        self.assertEqual(q.validate_remote(json.dumps(value).encode(), BINDING), value)
        self.assertLess(len(json.dumps(value).encode()), q.RECEIPT_BYTES - 512)

    def test_collision_blocks_without_reading_or_mutating_existing_scope(self):
        root = self.parent / self.ns["child_name"](BINDING)
        root.mkdir(mode=0o700)
        (root / "user-file").write_text("PREEXISTING")
        value = self.fixture_collect()
        self.assertEqual(value["reason"], "COLLISION")
        self.assertFalse(value["scope_created"])
        self.assertIsNone(value["namespace"])
        self.assertEqual(value["retained_entries"], [])
        self.assertEqual((root / "user-file").read_text(), "PREEXISTING")
        self.assertEqual(sorted(x.name for x in root.iterdir()), ["user-file"])
        q.validate_remote(json.dumps(value).encode(), BINDING)

    def test_post_mkdir_failure_retains_exact_scope_even_before_child_open(self):
        value = self.fixture_collect(create_fail=True)
        self.assertEqual(value["result"], "BLOCKED")
        self.assertEqual(value["phase"], "CREATE_SCOPE")
        self.assertTrue(value["scope_created"])
        self.assertEqual(value["namespace"], q.PARENT + "/" + self.ns["child_name"](BINDING))
        self.assertTrue((self.parent / self.ns["child_name"](BINDING)).is_dir())
        self.assertFalse(value["retained_entries_complete"])
        q.validate_remote(json.dumps(value).encode(), BINDING)

    def test_mid_write_failure_retains_partial_artifact_metadata(self):
        write = os.write
        calls = 0
        def failing_write(fd, raw):
            nonlocal calls
            calls += 1
            if calls == 1:
                write(fd, raw[:4])
                raise OSError("fixture mid-write")
            return write(fd, raw)
        value = self.fixture_collect(mutate=lambda stack: stack.enter_context(mock.patch.object(os, "write", side_effect=failing_write)))
        self.assertEqual(value["result"], "BLOCKED")
        self.assertTrue(value["scope_created"] and value["retained_entries_complete"])
        self.assertEqual(value["phase"], "BASELINE")
        self.assertEqual({x["name"]: x["bytes"] for x in value["retained_entries"]}, {"lock": 0, "version000.json": 4})
        self.assertFalse(value["proof_retained"])
        q.validate_remote(json.dumps(value).encode(), BINDING)

    def test_timeout_after_scope_preserves_artifacts_and_reports_phase(self):
        def fail(*args, **kwargs):
            raise self.ns["Blocked"]("DEADLINE")
        value = self.fixture_collect(mutate=lambda stack: stack.enter_context(mock.patch.dict(self.ns, exercise=fail)))
        self.assertEqual((value["reason"], value["phase"]), ("DEADLINE", "BASELINE"))
        self.assertTrue(value["scope_created"])
        self.assertTrue((self.parent / self.ns["child_name"](BINDING)).is_dir())

    def test_bad_host_prevents_parent_metadata_and_mkdir(self):
        with mock.patch.object(self.ns["socket"], "gethostname", return_value="wrong"), \
             mock.patch.object(self.ns["pwd"], "getpwuid", return_value=type("User", (), {"pw_name": "ubuntu"})()), \
             mock.patch.object(self.ns["Namespace"], "open", side_effect=AssertionError("NO_PARENT_READ")):
            value = self.ns["collect"](BINDING)
        self.assertEqual(value["reason"], "HOST_IDENTITY")
        self.assertFalse(value["scope_created"])

    def test_wrong_uid_and_writable_mode_rejected_without_modification(self):
        original = os.fstat(self.fd)
        with self.assertRaisesRegex(self.ns["Blocked"], "PARENT_METADATA"):
            self.ns["check_dir"](self.fd, original.st_uid + 1)
        for mode in (0o777, 0o775, 0o770, 0o707, 0o1755, 0o2755):
            os.chmod(self.parent, mode)
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(self.ns["Blocked"], "PARENT_METADATA"):
                self.ns["check_dir"](self.fd, os.getuid())
            self.assertEqual(os.stat(self.parent).st_mode & 0o7777, mode)
        os.chmod(self.parent, 0o700)

    def test_acl_name_presence_rejected_without_acl_value_read(self):
        for attr in ("system.posix_acl_access", "system.posix_acl_default"):
            with mock.patch.object(os, "listxattr", return_value=[attr]), \
                 mock.patch.object(os, "getxattr", side_effect=AssertionError("NO_ACL_VALUE_READ")), \
                 self.assertRaisesRegex(self.ns["Blocked"], "ACL"):
                self.ns["check_dir"](self.fd, os.getuid())

    def test_symlink_parent_component_never_followed(self):
        (self.parent / "home").symlink_to("/home")
        original_open = os.open
        def redirected(path, flags, *args, **kwargs):
            return original_open(self.parent if path == "/" else path, flags, *args, **kwargs)
        space = self.ns["Namespace"]()
        with mock.patch.object(os, "open", side_effect=redirected), \
             mock.patch.dict(self.ns, check_dir=lambda fd, uid: os.fstat(fd)), \
             self.assertRaisesRegex(self.ns["Blocked"], "PARENT_METADATA"):
            space.open()
        space.close()

    def test_child_collision_symlink_is_never_followed(self):
        name = self.ns["child_name"](BINDING)
        (self.parent / name).symlink_to("/tmp")
        value = self.fixture_collect()
        self.assertEqual(value["reason"], "COLLISION")
        self.assertFalse(value["scope_created"])
        self.assertTrue((self.parent / name).is_symlink())

    def test_mount_classification_rejects_foreign_pseudo_ro_bind_and_unknown(self):
        good = b"24 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
        self.assertEqual(self.ns["parse_mount"](good, os.makedev(8, 1)), mount())
        bad = [good.replace(b"ext4", fs) for fs in (b"tmpfs", b"overlay", b"nfs", b"fuse", b"btrfs", b"unknown")]
        bad += [good.replace(b"8:1", b"0:1"), good.replace(b"8:1", b"8:2"), good.replace(b" / / ", b" /subtree / "),
                good.replace(b"rw,relatime", b"ro,relatime"), good.replace(b"/dev/sda1 rw", b"/dev/sda1 ro"),
                good.replace(b"/dev/sda1", b"server:export"), good + good, b"", b"bad\n", b"x" * 32769]
        for raw in bad:
            with self.subTest(raw=raw[:80]), self.assertRaises(self.ns["Blocked"]):
                self.ns["parse_mount"](raw, os.makedev(8, 1))

    def test_nested_mount_takes_precedence(self):
        raw = (b"24 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
               b"25 24 0:7 / /home/ubuntu/.local/share/bridge-school rw - tmpfs tmpfs rw\n")
        with self.assertRaisesRegex(self.ns["Blocked"], "MOUNT"):
            self.ns["parse_mount"](raw, os.makedev(8, 1))

    def test_existing_file_is_never_truncated_or_overwritten(self):
        self.ns["create_file"](self.fd, "current.json", b"KEEP")
        with self.assertRaises(FileExistsError):
            self.ns["create_file"](self.fd, "current.json", b"LOSE")
        self.assertEqual((self.parent / "current.json").read_bytes(), b"KEEP")

    def test_symlink_file_read_and_create_block(self):
        (self.parent / "current.json").symlink_to("/etc/passwd")
        with self.assertRaises(OSError):
            self.ns["read_file"](self.fd, "current.json", self.budget())
        with self.assertRaises(OSError):
            self.ns["create_file"](self.fd, "current.json", b"x")

    def test_filename_allowlist(self):
        for name in ("../x", "/tmp/x", "environment", "current.json/../x", "secret"):
            with self.assertRaisesRegex(self.ns["Blocked"], "FILE_METADATA"):
                self.ns["create_file"](self.fd, name, b"x")
            with self.assertRaisesRegex(self.ns["Blocked"], "FILE_METADATA"):
                self.ns["read_file"](self.fd, name, self.budget())

    def test_atomic_exchange_rejects_wrong_identity_without_overwrite(self):
        for name, raw in (("version000.json", b"old"), ("version002.json", b"new"), ("current.json", b"foreign")):
            self.ns["create_file"](self.fd, name, raw)
        self.ns["link_file"](self.fd, "version002.json", "pending-after.json")
        with self.assertRaisesRegex(self.ns["Blocked"], "ATOMIC_EXCHANGE"):
            self.ns["atomic_exchange"](self.fd, "pending-after.json", "current.json")
        self.assertEqual((self.parent / "current.json").read_bytes(), b"foreign")
        self.assertEqual((self.parent / "pending-after.json").read_bytes(), b"new")

    def test_unsupported_exchange_has_no_fallback(self):
        for name, raw in (("version000.json", b"old"), ("version002.json", b"new")):
            self.ns["create_file"](self.fd, name, raw)
        self.ns["link_file"](self.fd, "version000.json", "current.json")
        self.ns["link_file"](self.fd, "version002.json", "pending-after.json")
        with mock.patch.object(self.ns["ctypes"], "CDLL", return_value=object()), \
             self.assertRaisesRegex(self.ns["Blocked"], "ATOMIC_EXCHANGE"):
            self.ns["atomic_exchange"](self.fd, "pending-after.json", "current.json")
        self.assertEqual((self.parent / "current.json").read_bytes(), b"old")
        self.assertEqual((self.parent / "pending-after.json").read_bytes(), b"new")

    def test_capture_limit_prevents_read_past_combined_budget(self):
        r, w = os.pipe()
        try:
            os.write(w, b"x" * 100)
            budget = self.budget(); budget.captured = self.ns["CAP"] - 5
            with self.assertRaisesRegex(self.ns["Blocked"], "OUTPUT_CAP"):
                budget.read(r, 100)
            self.assertEqual(budget.captured, self.ns["CAP"])
            self.assertEqual(len(os.read(r, 100)), 95)
        finally:
            os.close(r); os.close(w)

    def test_expired_budget_and_client_cap(self):
        budget = self.budget(-1)
        with self.assertRaisesRegex(self.ns["Blocked"], "DEADLINE"):
            budget.check()
        budget = self.budget(); budget.clients = 4
        clients = self.ns["Clients"](budget, self.fd)
        with self.assertRaisesRegex(self.ns["Blocked"], "CLIENT_CAP"):
            clients.spawn(lambda *_: None)
        self.assertFalse(clients.owned)

    def test_client_cleanup_kills_only_exact_created_pid(self):
        budget = self.budget()
        clients = self.ns["Clients"](budget, self.fd)
        def wait(infd, outfd):
            self.ns["wire_write"](outfd, b"READY\n")
            os.read(infd, 1)
        pid, _, out = clients.spawn(wait)
        self.assertEqual(clients.receive(out), b"READY\n")
        original_kill = os.kill
        calls = []
        def traced(target, sig):
            calls.append((target, sig)); return original_kill(target, sig)
        with mock.patch.object(os, "kill", side_effect=traced):
            clients.close()
        self.assertEqual(calls, [(pid, signal.SIGKILL)])
        self.assertFalse(clients.owned)
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_client_error_and_missing_readiness_fail_closed(self):
        clients = self.ns["Clients"](self.budget(), self.fd)
        try:
            pid, _, out = clients.spawn(lambda *_: (_ for _ in ()).throw(ValueError()))
            with self.assertRaisesRegex(self.ns["Blocked"], "CLIENT_FAILED"):
                clients.receive(out)
            with self.assertRaisesRegex(self.ns["Blocked"], "CLIENT_FAILED"):
                clients.reap(pid)
        finally:
            clients.close()

    def test_schema_forbids_claims_and_extra_or_duplicate_fields(self):
        good = self.fixture_collect()
        bads = []
        for key, value in (("ssh_disconnect_tested", True), ("power_loss_tested", True), ("watchdog_ready", True),
                           ("scope_created", False), ("fork_clients", 3), ("persistence", "DURABLE"), ("phase", "VERIFY"),
                           ("captured_bytes", 114688), ("proof_retained", False), ("remote_exec_commands", 1),
                           ("binding", {**BINDING, "attempt": 2}), ("namespace", "/tmp/x"), ("extra", "secret")):
            value2 = copy.deepcopy(good); value2[key] = value; bads.append(value2)
        for value in bads:
            with self.subTest(value=value), self.assertRaises(q.transport.InventoryError):
                q.validate_remote(json.dumps(value).encode(), BINDING)
        with self.assertRaises(q.transport.InventoryError):
            q.validate_remote(b'{"schema":"x","schema":"y"}', BINDING)
        with self.assertRaises(q.transport.InventoryError):
            q.validate_remote(b"x" * q.RECEIPT_BYTES, BINDING)

    def test_named_child_replacement_is_detected_against_pinned_descriptor(self):
        space = self.ns["Namespace"]()
        space.fds = [self.fd]
        space.name = "fixture-child"
        (self.parent / space.name).mkdir(mode=0o700)
        space.child = os.open(self.parent / space.name, self.ns["FLAGS"])
        self.addCleanup(os.close, space.child)
        info = os.fstat(self.fd)
        space.metadata = [{"path": "/", "uid": 0, "mode": format(info.st_mode & 0o7777, "04o"),
                           "device": info.st_dev, "inode": info.st_ino}]
        stat_call = os.stat
        def fake_root(path, *args, **kwargs):
            return info if path == "/" else stat_call(path, *args, **kwargs)
        with mock.patch.dict(self.ns, check_dir=lambda fd, uid: os.fstat(fd)), mock.patch.object(os, "stat", side_effect=fake_root):
            space.recheck()
            (self.parent / space.name).rename(self.parent / "retained-original-child")
            (self.parent / space.name).mkdir(mode=0o700)
            with self.assertRaisesRegex(self.ns["Blocked"], "PARENT_CHANGED"):
                space.recheck()
        self.assertTrue((self.parent / "retained-original-child").is_dir())

    def test_lock_name_must_reopen_exact_original_inode(self):
        self.ns["create_file"](self.fd, "lock", b"")
        expected = self.ns["entry_identity"](self.fd, "lock")
        lock = self.ns["acquire"](self.fd, expected)
        os.close(lock)
        (self.parent / "lock").rename(self.parent / "retained-original-lock")
        self.ns["create_file"](self.fd, "lock", b"")
        with self.assertRaisesRegex(self.ns["Blocked"], "LOCK"):
            self.ns["acquire"](self.fd, expected)
        self.assertTrue((self.parent / "retained-original-lock").exists())

    def test_child_capture_reservation_rejects_before_fork(self):
        budget = self.budget(); budget.captured = self.ns["CAP"] - 100
        before = budget.captured
        with self.assertRaisesRegex(self.ns["Blocked"], "OUTPUT_CAP"):
            budget.account(4099)
        self.assertEqual(budget.captured, before)

    def test_remote_wait_timeout_retains_child_scope_and_cleans_only_own_client(self):
        budget = self.budget(.1)
        clients = self.ns["Clients"](budget, self.fd)
        def quiet(infd, outfd):
            os.read(infd, 1)
        pid, _, out = clients.spawn(quiet)
        try:
            with self.assertRaises(self.ns["Blocked"]):
                clients.receive(out)
        finally:
            clients.close()
        self.assertFalse(clients.owned)
        self.assertTrue(self.parent.is_dir())

    def test_capture_exhaustion_preserves_scope_in_valid_blocked_receipt(self):
        def cap(dfd, binding, budget, *args):
            self.ns["create_file"](dfd, "lock", b"")
            budget.captured = self.ns["CAP"]
            raise self.ns["Blocked"]("OUTPUT_CAP")
        value = self.fixture_collect(mutate=lambda stack: stack.enter_context(mock.patch.dict(self.ns, exercise=cap)))
        self.assertEqual(value["reason"], "OUTPUT_CAP")
        self.assertEqual(value["captured_bytes"], self.ns["CAP"])
        self.assertTrue(value["scope_created"])
        self.assertEqual(value["retained_entries"], [{"name": "lock", "state": "OWNED_REGULAR", "bytes": 0}])
        self.assertEqual(q.validate_remote(json.dumps(value).encode(), BINDING), value)

    def test_schema_rejects_semantically_inconsistent_artifacts_binding_and_mount(self):
        good = self.fixture_collect()
        mutations = [
            lambda x: x["binding"].update(attempt=True),
            lambda x: x["mount"].update(device_minor=2),
            lambda x: x["artifacts"][0].update(sha256="0" * 64),
            lambda x: x["artifacts"][-1].update(bytes=x["artifacts"][-1]["bytes"] + 1),
            lambda x: x["retained_entries"][0].update(bytes=0),
        ]
        for mutate in mutations:
            value = copy.deepcopy(good); mutate(value)
            with self.assertRaises(q.transport.InventoryError):
                q.validate_remote(json.dumps(value).encode(), BINDING)

    def test_alarm_immediately_after_mkdir_cannot_hide_created_scope(self):
        mkdir = os.mkdir
        def interrupt(path, mode=0o777, **kwargs):
            mkdir(path, mode, **kwargs)
            os.kill(os.getpid(), signal.SIGALRM)
        value = self.fixture_collect(mutate=lambda stack: stack.enter_context(mock.patch.object(os, "mkdir", side_effect=interrupt)))
        self.assertEqual(value["reason"], "DEADLINE")
        self.assertTrue(value["scope_created"])
        self.assertIsNotNone(value["namespace"])
        self.assertTrue((self.parent / self.ns["child_name"](BINDING)).is_dir())
        q.validate_remote(json.dumps(value).encode(), BINDING)

    def test_alarm_after_reap_cannot_leave_stale_pid_for_cleanup(self):
        clients = self.ns["Clients"](self.budget(), self.fd)
        def waiting(infd, outfd):
            self.ns["wire_write"](outfd, b"READY\n")
            os.read(infd, 1)
        pid, _, out = clients.spawn(waiting)
        self.assertEqual(clients.receive(out), b"READY\n")
        waitpid = os.waitpid
        def interrupt(target, flags):
            result = waitpid(target, flags)
            if result[0]:
                os.kill(os.getpid(), signal.SIGALRM)
            return result
        old = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(self.ns["Blocked"]("DEADLINE")))
        try:
            with mock.patch.object(os, "waitpid", side_effect=interrupt), self.assertRaisesRegex(self.ns["Blocked"], "DEADLINE"):
                clients.reap(pid, killed=True)
            self.assertFalse(clients.owned)
            with mock.patch.object(os, "kill", side_effect=AssertionError("NO_STALE_PID_KILL")):
                clients.close()
        finally:
            signal.signal(signal.SIGALRM, old)
            if clients.owned:
                clients.close()

    def test_source_has_no_remote_exec_delete_service_or_environment_reads(self):
        tree = ast.parse(q.REMOTE_SOURCE)
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertTrue({"fork", "fsync", "mkdir", "flock", "prctl", "renameat2"} <= attrs)
        self.assertFalse({"system", "popen", "Popen", "execv", "execve", "unlink", "rmdir", "chmod", "chown", "killpg", "getenv", "environ"} & attrs)
        self.assertNotIn("sudo", q.REMOTE_SOURCE)
        self.assertNotIn("journalctl", q.REMOTE_SOURCE)
        self.assertLess(len(q.REMOTE_SOURCE.encode()) + 200, 65536)


class RunnerTests(unittest.TestCase):
    def budget(self):
        return q.transport.Budget(time.monotonic() + 5, q.RUNNER_CAPTURE_BYTES, q.RUNNER_COMMANDS)

    def fake_process(self, argv, budget, **kwargs):
        budget.started += 1
        if argv[1:3] == ["rev-parse", "HEAD"]:
            return 0, (HEAD + "\n").encode()
        if argv[1:3] == ["diff", "--quiet"]:
            return 0, b""
        if "ls-remote" in argv:
            return 0, (HEAD + "\t" + q.transport.REF + "\n").encode()
        raise AssertionError(argv)

    def test_context_exact_admission_checks(self):
        with mock.patch.dict(os.environ, ENV, clear=True), mock.patch.object(q.transport, "bounded_process", side_effect=self.fake_process):
            self.assertEqual(q.context(self.budget()), BINDING)
        for key in ENV:
            bad = dict(ENV); bad[key] = "wrong"
            with self.subTest(key=key), mock.patch.dict(os.environ, bad, clear=True), \
                 mock.patch.object(q.transport, "bounded_process", side_effect=AssertionError("BEFORE_NETWORK")), \
                 self.assertRaises(q.transport.InventoryError):
                q.context(self.budget())

    def test_context_rejects_stale_head_and_dirty_checkout(self):
        for failing in ("rev-parse", "diff", "ls-remote"):
            def process(argv, budget, **kwargs):
                if failing in argv:
                    return 1, b""
                return self.fake_process(argv, budget, **kwargs)
            with mock.patch.dict(os.environ, ENV, clear=True), mock.patch.object(q.transport, "bounded_process", side_effect=process), \
                 self.assertRaises(q.transport.InventoryError):
                q.context(self.budget())

    def test_no_secret_access_before_admission(self):
        with mock.patch.dict(os.environ, {"ORACLE_SSH_PRIVATE_KEY": SECRET}, clear=True), \
             mock.patch.object(q.transport, "memory", side_effect=AssertionError("SECRET_TOUCHED")), \
             self.assertRaisesRegex(q.transport.InventoryError, "CONTEXT"):
            q.run_qualification(self.budget())
        self.assertNotIn(SECRET, q.REMOTE_SOURCE)

    def test_revalidation_prevents_ssh_after_main_changes(self):
        binding = BINDING
        with mock.patch.dict(os.environ, {**ENV, "ORACLE_SSH_PRIVATE_KEY": SECRET}, clear=True), \
             mock.patch.object(q, "context", side_effect=[binding, q.transport.InventoryError("CURRENT_MAIN")]), \
             mock.patch.object(q.transport, "memory", side_effect=[80, 81]), \
             mock.patch.object(q.transport, "pinned_scan", return_value=b"public-pin"), \
             mock.patch.object(q.transport, "bounded_process", return_value=(0, b"scan")) as proc, \
             mock.patch.object(os, "close") as close, self.assertRaisesRegex(q.transport.InventoryError, "CURRENT_MAIN"):
            q.run_qualification(self.budget())
        self.assertEqual(proc.call_count, 1)
        self.assertIn("ssh-keyscan", proc.call_args.args[0][0])
        self.assertEqual({x.args[0] for x in close.call_args_list}, {80, 81})

    def test_one_ssh_fixed_target_payload_and_receipt(self):
        ns = remote(); remote_value = ns["blank"](BINDING); remote_value["reason"] = "MOUNT"
        responses = [(0, b"scan"), (0, json.dumps(remote_value).encode())]
        with mock.patch.dict(os.environ, {**ENV, "ORACLE_SSH_PRIVATE_KEY": SECRET}, clear=True), \
             mock.patch.object(q, "context", side_effect=[BINDING, BINDING]) as context, \
             mock.patch.object(q.transport, "memory", side_effect=[80, 81]), \
             mock.patch.object(q.transport, "pinned_scan", return_value=b"public-pin"), \
             mock.patch.object(q.transport, "bounded_process", side_effect=responses) as proc, mock.patch.object(os, "close"):
            value = q.run_qualification(self.budget())
        self.assertEqual(value["result"], "BLOCKED")
        self.assertEqual(context.call_count, 2)
        ssh = proc.call_args_list[-1]
        self.assertEqual(ssh.args[0][-2:], ["ubuntu@92.5.47.149", "/usr/bin/python3 -I -B -"])
        self.assertNotIn(SECRET, ssh.kwargs["payload"].decode())
        ast.parse(ssh.kwargs["payload"])
        self.assertIn("StrictHostKeyChecking=yes", ssh.args[0])
        self.assertIn("IdentityAgent=none", ssh.args[0])

    def test_isolated_cli_imports_without_network_or_secret(self):
        result = subprocess.run(["/usr/bin/python3", "-I", "-B", str(Path(q.__file__).resolve()), "--offline-test"],
                                capture_output=True, env={"PATH": "/usr/bin:/bin"}, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"")
        value = json.loads(result.stdout)
        self.assertEqual(value["reason"], "ARGUMENTS")
        self.assertEqual(value["remote_scope_state"], "NOT_ATTEMPTED")

    def test_main_errors_are_sanitized_and_uncertain_scope_is_not_zero_changes(self):
        with mock.patch.object(q, "run_qualification", side_effect=OSError(SECRET)), contextlib.redirect_stdout(io.StringIO()) as out:
            result = q.main([])
        self.assertEqual(result, 1)
        self.assertNotIn(SECRET, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["remote_scope_state"], "NOT_ATTEMPTED")

    def test_uncertain_ssh_errors_retain_binding_and_exact_candidate_scope(self):
        for reason in ("DEADLINE", "TRANSPORT", "REMOTE_SCHEMA"):
            def fail(budget, recovery):
                recovery.update(binding=BINDING, candidate_namespace=q.PARENT + "/qualification-12345-1-" + HEAD,
                                remote_attempted=True)
                raise q.transport.InventoryError(reason)
            with self.subTest(reason=reason), mock.patch.object(q, "run_qualification", side_effect=fail), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(q.main([]), 1)
            value = json.loads(out.getvalue())
            self.assertEqual(value["binding"], BINDING)
            self.assertEqual(value["candidate_namespace"], q.PARENT + "/qualification-12345-1-" + HEAD)
            self.assertTrue(value["remote_attempted"])
            self.assertEqual(value["remote_scope_state"], "UNOBSERVED_MAY_EXIST")
            self.assertFalse(value["automatic_cleanup"])

    def test_exact_limits(self):
        self.assertEqual(q.REMOTE_SECONDS, 20)
        self.assertEqual(q.OUTER_SECONDS, 60)
        self.assertEqual(q.RUNNER_COMMANDS + q.REMOTE_COMMANDS, 8)
        self.assertEqual(q.MAX_FORK_CLIENTS, 4)
        self.assertEqual(q.REMOTE_CAPTURE_BYTES + q.RUNNER_CAPTURE_BYTES, 128 * 1024)
        self.assertEqual(q.RECEIPT_BYTES, 12 * 1024)


if __name__ == "__main__":
    unittest.main()
