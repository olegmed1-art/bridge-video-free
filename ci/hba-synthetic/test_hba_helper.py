"""Linux synthetic native tests. Windows reports SKIP; never a native PASS."""
import errno
import io
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import json
import os
import stat
import struct
import shutil
import signal
import time
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

if sys.platform == "linux":
    import hba_helper as h

SYNTHETIC = b"# synthetic fixture only\nlocal all fixture_owner peer\nhost all fixture_login 127.0.0.1/32 reject\n"
ACCEPT = dict(outcome="COMMITTED", target_closed=True, sessions_zero=True,
              prepared_zero=True, worker_disconnected=True, exact_catalog=True)



from process_supervisor import run_fixture_process


# Capability refusals from the trusted existing unshare binary, LANG=C only.
# Exact one-line diagnostics; unknown errors/return codes remain failures.
_NAMESPACE_DENIALS = {
    b"unshare: unshare failed: Operation not permitted",
    b"unshare: unshare failed: Permission denied",
    b"unshare: write failed /proc/self/uid_map: Operation not permitted",
    b"unshare: write failed /proc/self/uid_map: Permission denied",
    b"unshare: write failed /proc/self/gid_map: Operation not permitted",
    b"unshare: write failed /proc/self/gid_map: Permission denied",
    b"unshare: write failed /proc/self/setgroups: Operation not permitted",
    b"unshare: write failed /proc/self/setgroups: Permission denied",
}


def namespace_capability_denied(result):
    return (result.returncode == 1 and result.stdout == b"" and
            any(result.stderr in (diagnostic, diagnostic + b"\n")
                for diagnostic in _NAMESPACE_DENIALS))


@unittest.skipUnless(sys.platform == "linux", "NOT_RUN: native Linux required")
class NativeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hba-synthetic-", dir="/tmp")
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        (self.root / ".synthetic-hba").write_bytes(b"HBA_SYNTHETIC_ONLY_V1\n")
        self.file = self.root / "pg_hba.conf"
        self.file.write_bytes(SYNTHETIC)
        os.chmod(self.file, 0o644)
        self.helpers = []

    def tearDown(self):
        for helper in self.helpers:
            helper.close()
        self.temp.cleanup()

    def helper(self):
        helper = h.SyntheticHelper(self.root)
        self.helpers.append(helper)
        return helper

    def test_roundtrip_exact_bytes_uid_gid_mode_mtime(self):
        helper = self.helper()
        original = helper.original
        helper.install()
        self.assertEqual(self.file.read_bytes(), h.PREFIX + SYNTHETIC)
        helper.restore(**ACCEPT)
        self.assertEqual(self.file.read_bytes(), SYNTHETIC)
        final = h.snapshot(helper.fd, "pg_hba.conf")
        for key in ("uid", "gid", "mode", "mtime_ns", "attrs", "sha256"):
            self.assertEqual(final[key], original[key], key)

    def test_user_xattr_roundtrip(self):
        try:
            os.setxattr(self.file, "user.fixture", b"synthetic-attribute")
        except OSError as exc:
            self.skipTest("NOT_RUN xattr unsupported: " + str(exc.errno))
        helper = self.helper()
        helper.install()
        self.assertEqual(os.getxattr(self.file, "user.fixture"), b"synthetic-attribute")
        helper.restore(**ACCEPT)
        self.assertEqual(os.getxattr(self.file, "user.fixture"), b"synthetic-attribute")

    def test_native_posix_acl_roundtrip(self):
        # Linux POSIX ACL xattr; named synthetic UID need not exist as an account.
        undefined = 0xffffffff
        acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in (
            (1, 6, undefined), (2, 4, os.geteuid() + 1), (4, 4, undefined),
            (16, 6, undefined), (32, 0, undefined)))
        try:
            os.setxattr(self.file, "system.posix_acl_access", acl)
        except OSError as exc:
            self.skipTest("NOT_RUN POSIX ACL unsupported: " + str(exc.errno))
        helper = self.helper()
        before = os.getxattr(self.file, "system.posix_acl_access")
        helper.install()
        self.assertEqual(os.getxattr(self.file, "system.posix_acl_access"), before)
        helper.restore(**ACCEPT)
        self.assertEqual(os.getxattr(self.file, "system.posix_acl_access"), before)

    def test_attribute_copy_error_refuses_before_publish(self):
        try:
            os.setxattr(self.file, "user.fixture", b"synthetic")
        except OSError:
            self.skipTest("NOT_RUN xattrs unavailable")
        helper = self.helper()
        with patch.object(h.os, "setxattr", side_effect=PermissionError("synthetic denial")):
            with self.assertRaises(PermissionError):
                helper.install()
        self.assertEqual(self.file.read_bytes(), SYNTHETIC)

    def test_fsync_file_before_rename_and_directory_after(self):
        helper = self.helper()
        events = []
        real_sync, real_replace = h.os.fsync, h.os.replace
        def synced(fd):
            events.append("dir_sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_sync")
            return real_sync(fd)
        def replaced(src, dst, **kwargs):
            events.append("target_rename" if dst == "pg_hba.conf" else "journal_rename")
            return real_replace(src, dst, **kwargs)
        with patch.object(h.os, "fsync", synced), patch.object(h.os, "replace", replaced):
            helper.install()
        index = events.index("target_rename")
        self.assertIn("file_sync", events[:index])
        self.assertEqual(events[index + 1], "dir_sync")

    def test_file_fsync_failure_preserves_original(self):
        helper = self.helper()
        with patch.object(h.os, "fsync", side_effect=OSError("synthetic fsync failure")):
            with self.assertRaises(OSError):
                helper.install()
        self.assertEqual(self.file.read_bytes(), SYNTHETIC)

    def test_directory_fsync_failure_after_rename_is_unknown(self):
        helper = self.helper()
        real_sync = h.os.fsync
        count = [0]
        def fail_second_dir(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                count[0] += 1
                if count[0] == 2:
                    raise OSError("synthetic directory flush failure")
            return real_sync(fd)
        with patch.object(h.os, "fsync", fail_second_dir):
            with self.assertRaises(OSError):
                helper.install()
        self.assertEqual(self.file.read_bytes(), h.PREFIX + SYNTHETIC)
        self.assertEqual(helper.state, "REPLACE_INTENT_UNKNOWN")
        with self.assertRaises(h.Refused):
            helper.restore(**ACCEPT)

    def test_atomic_rename_changes_inode(self):
        helper = self.helper()
        prior = self.file.stat().st_ino
        helper.install()
        self.assertNotEqual(self.file.stat().st_ino, prior)

    def test_intent_fsync_failure_blocks_same_object_replay(self):
        helper = self.helper()
        real_sync = h.os.fsync
        def fail_dir(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("synthetic intent flush uncertainty")
            return real_sync(fd)
        with patch.object(h.os, "fsync", fail_dir):
            with self.assertRaises(OSError):
                helper.install()
        self.assertTrue(helper.poisoned)
        self.assertEqual(self.file.read_bytes(), SYNTHETIC)
        with self.assertRaises(h.Refused):
            helper.install()
        with self.assertRaises(h.Refused):
            helper.restore(**ACCEPT)

    def test_restore_intent_failure_blocks_same_object_replay(self):
        helper = self.helper()
        helper.install()
        real_sync = h.os.fsync
        def fail_dir(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("synthetic restore flush uncertainty")
            return real_sync(fd)
        with patch.object(h.os, "fsync", fail_dir):
            with self.assertRaises(OSError):
                helper.restore(**ACCEPT)
        self.assertTrue(helper.poisoned)
        self.assertEqual(self.file.read_bytes(), h.PREFIX + SYNTHETIC)
        with self.assertRaises(h.Refused):
            helper.restore(**ACCEPT)

    def test_open_directory_fd_sees_new_inode_not_actual_bind_proof(self):
        helper = self.helper()
        observer = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            helper.install()
            self.assertEqual(h.snapshot(observer, "pg_hba.conf")["data"], h.PREFIX + SYNTHETIC)
        finally:
            os.close(observer)

    def test_pinned_file_fd_retains_old_inode_bind_hazard_model(self):
        helper = self.helper()
        observer = os.open(self.file, os.O_RDONLY)
        try:
            helper.install()
            self.assertEqual(os.read(observer, 524288), SYNTHETIC)
            self.assertEqual(self.file.read_bytes(), h.PREFIX + SYNTHETIC)
        finally:
            os.close(observer)

    def test_foreign_edit_before_final_check_not_overwritten(self):
        helper = self.helper()
        def edit():
            self.file.write_bytes(b"foreign fixture edit\n")
        with self.assertRaises(h.Refused):
            helper.install(before_guard=edit)
        self.assertEqual(self.file.read_bytes(), b"foreign fixture edit\n")

    def test_foreign_restore_edit_not_overwritten(self):
        helper = self.helper()
        helper.install()
        def edit():
            self.file.write_bytes(b"foreign restore edit\n")
        with self.assertRaises(h.Refused):
            helper.restore(**ACCEPT, before_guard=edit)
        self.assertEqual(self.file.read_bytes(), b"foreign restore edit\n")

    def test_unknown_watchdog_never_restores(self):
        helper = self.helper()
        helper.install()
        before = self.file.read_bytes()
        verdict = helper.watchdog("UNKNOWN")
        self.assertEqual(verdict["action"], "NONE_KEEP_CLOSED_REQUIRE_REVIEW")
        with self.assertRaises(h.Refused):
            helper.restore(**{**ACCEPT, "outcome": "UNKNOWN"})
        self.assertEqual(self.file.read_bytes(), before)

    def test_foreign_edit_watchdog_observes_without_writing(self):
        helper = self.helper()
        helper.install()
        self.file.write_bytes(b"foreign watchdog edit\n")
        self.assertFalse(helper.watchdog("UNKNOWN")["file_matches"])
        self.assertEqual(self.file.read_bytes(), b"foreign watchdog edit\n")

    def test_expired_coordination_refuses(self):
        helper = self.helper()
        helper.expires = 0
        with self.assertRaises(h.Refused):
            helper.install()
        self.assertEqual(self.file.read_bytes(), SYNTHETIC)

    def test_second_cooperative_editor_cannot_enter(self):
        self.helper()
        with self.assertRaises(BlockingIOError):
            h.SyntheticHelper(self.root)

    def test_attempt_cannot_replay_after_restart(self):
        helper = self.helper()
        helper.install()
        helper.close()
        with self.assertRaises(h.Refused):
            h.SyntheticHelper(self.root)

    def test_backup_corruption_refuses_restore(self):
        helper = self.helper()
        helper.install()
        (self.root / ".original.bytes").write_bytes(b"corrupt backup\n")
        with self.assertRaises(h.Refused):
            helper.restore(**ACCEPT)
        self.assertEqual(self.file.read_bytes(), h.PREFIX + SYNTHETIC)

    def test_symlink_refuses(self):
        self.file.unlink()
        self.file.symlink_to(self.root / ".synthetic-hba")
        with self.assertRaises(OSError):
            h.SyntheticHelper(self.root)

    def test_hardlink_refuses(self):
        os.link(self.file, self.root / "other.conf")
        with self.assertRaises(h.Refused):
            h.SyntheticHelper(self.root)

    def test_live_entrypoint_refuses(self):
        with self.assertRaises(h.Refused):
            h.execute_live()

    def test_concurrent_instance_use_is_refused(self):
        helper = self.helper()
        caught = []
        def other_thread():
            try:
                helper.install()
            except h.Refused:
                caught.append(True)
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(caught, [True])
        self.assertEqual(self.file.read_bytes(), SYNTHETIC)

    def test_incomplete_db_acceptance_refuses_restore(self):
        helper = self.helper()
        helper.install()
        with self.assertRaises(h.Refused):
            helper.restore(**{**ACCEPT, "sessions_zero": False})


    def assert_closed_refuses_without_filesystem(self, helper, operation):
        helper.close()
        # Catch the dir_fd=None/CWD bug before any read, staged write or stat.
        with ExitStack() as stack:
            for name in ("snapshot", "exclusive_file"):
                stack.enter_context(patch.object(h, name, side_effect=AssertionError("closed filesystem access")))
            for name in ("open", "fstat", "stat", "replace", "fsync", "close"):
                stack.enter_context(patch.object(h.os, name, side_effect=AssertionError("closed filesystem access")))
            with self.assertRaises(h.Refused):
                operation()

    def test_install_after_close_refuses_before_staged_write(self):
        helper = self.helper()
        self.assert_closed_refuses_without_filesystem(helper, helper.install)

    def test_restore_after_close_refuses_before_backup_read(self):
        helper = self.helper()
        helper.install()
        self.assert_closed_refuses_without_filesystem(helper, lambda: helper.restore(**ACCEPT))

    def test_watchdog_after_close_refuses_before_cwd_read(self):
        helper = self.helper()
        self.assert_closed_refuses_without_filesystem(helper, lambda: helper.watchdog("UNKNOWN"))

    def test_journal_after_close_refuses_before_write(self):
        helper = self.helper()
        self.assert_closed_refuses_without_filesystem(helper, lambda: helper._journal("PREPARED"))

    def test_lock_validation_after_close_refuses_before_stat(self):
        helper = self.helper()
        self.assert_closed_refuses_without_filesystem(helper, helper._lock_valid)

    def test_guard_after_close_refuses_before_stat(self):
        helper = self.helper()
        self.assert_closed_refuses_without_filesystem(helper, helper._guard)

    def test_replace_after_close_refuses_before_write(self):
        helper = self.helper()
        self.assert_closed_refuses_without_filesystem(helper, lambda: helper._replace(SYNTHETIC, "RESTORED"))

    def test_repeated_close_is_idempotent_without_filesystem(self):
        helper = self.helper()
        helper.close()
        with patch.object(h.os, "close", side_effect=AssertionError("second close touched filesystem")):
            helper.close()
        self.assertTrue(helper.closed)
        self.assertIsNone(helper.fd)
        self.assertIsNone(helper.lock)


    def assert_corrupt_staged_payload_refuses(self, helper, operation, expected_current):
        real_snapshot = h.snapshot
        def corrupt_before_snapshot(dfd, name):
            if name.startswith(".staged-"):
                fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=dfd)
                try:
                    os.write(fd, b"!")
                finally:
                    os.close(fd)
            return real_snapshot(dfd, name)
        with patch.object(h, "snapshot", corrupt_before_snapshot):
            with self.assertRaisesRegex(h.Refused, "Staged bytes differ"):
                operation()
        self.assertEqual(self.file.read_bytes(), expected_current)
        self.assertTrue(helper.poisoned)

    def test_corrupt_staged_install_bytes_refuse_before_publish(self):
        helper = self.helper()
        self.assert_corrupt_staged_payload_refuses(helper, helper.install, SYNTHETIC)

    def test_corrupt_staged_restore_bytes_refuse_before_publish(self):
        helper = self.helper()
        helper.install()
        self.assert_corrupt_staged_payload_refuses(
            helper, lambda: helper.restore(**ACCEPT), h.PREFIX + SYNTHETIC)

    def test_process_timeout_kills_and_reaps_owned_child(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            run_fixture_process([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                                timeout=0.15, env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        self.assertIsNotNone(caught.exception.supervisor_returncode)
        self.assertTrue(caught.exception.drain_receipt["all_owned_descendants_drained"])
        self.assertLess(time.monotonic() - started, 5)

    def namespace_tools(self):
        unshare, mount = shutil.which("unshare"), shutil.which("mount")
        if not unshare or not mount:
            self.skipTest("NOT_RUN: existing unshare/mount unavailable; never install")
        help_result = run_fixture_process([unshare, "--help"], timeout=2,
                                          env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        if b"--kill-child" not in help_result.stdout or b"--pid" not in help_result.stdout:
            self.skipTest("NOT_RUN: required kill-child/PID namespace unsupported; no fallback")
        return unshare, mount

    def test_namespace_timeout_kills_descendants_and_reaps_wrapper(self):
        unshare, _ = self.namespace_tools()
        # Probe availability first; timeout only an isolated synthetic PID tree.
        prefix = [unshare, "--user", "--map-root-user", "--mount", "--pid",
                  "--propagation", "private", "--fork", "--kill-child=KILL"]
        env = {"PATH": "/usr/bin:/bin", "LANG": "C"}
        probe = run_fixture_process(prefix + [sys.executable, "-I", "-c", "pass"],
                                    timeout=3, env=env)
        if namespace_capability_denied(probe):
            self.skipTest("NOT_RUN: unprivileged isolated namespace denied")
        self.assertEqual(probe.returncode, 0, probe.stderr.decode(errors="replace"))
        child = ("import subprocess,sys,time; "
                 "subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']); "
                 "time.sleep(60)")
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            run_fixture_process(prefix + [sys.executable, "-I", "-c", child],
                                timeout=0.3, env=env)
        self.assertIsNotNone(caught.exception.supervisor_returncode)
        # PID namespace init death terminates all descendants; wrapper is reaped.


    def assert_policy_flag_refused(self, value):
        target_inode = self.file.stat().st_ino
        real_ioctl = h.fcntl.ioctl
        def unsupported(fd, request, buffer, mutate):
            if os.fstat(fd).st_ino == target_inode:
                buffer[0] = value
                return 0
            return real_ioctl(fd, request, buffer, mutate)
        with patch.object(h.fcntl, "ioctl", unsupported):
            with self.assertRaises(h.Refused):
                self.helper()
        self.assertEqual(self.file.read_bytes(), SYNTHETIC)

    def test_nodump_policy_flag_refused(self):
        self.assert_policy_flag_refused(0x40)

    def test_noatime_policy_flag_refused(self):
        self.assert_policy_flag_refused(0x80)

    def test_sync_policy_flag_refused(self):
        self.assert_policy_flag_refused(0x08)

    def test_unknown_inode_flag_refused(self):
        def ioctl(fd, request, buffer, mutate):
            buffer[0] = 0x10
        with patch.object(h.fcntl, "ioctl", ioctl):
            with self.assertRaises(h.Refused):
                fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    h.snapshot(fd, "pg_hba.conf")
                finally:
                    os.close(fd)

    def test_inode_flag_change_during_snapshot_refused(self):
        answers = iter((0, h.KERNEL_MANAGED_EXTENTS))
        with patch.object(h, "read_inode_flags", side_effect=lambda fd: next(answers)):
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(h.Refused):
                    h.snapshot(fd, "pg_hba.conf")
            finally:
                os.close(fd)

    def test_new_private_file_exact_0600_with_restrictive_umask(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        prior = os.umask(0o777)
        try:
            h.exclusive_file(fd, ".umask-fixture", b"private synthetic bytes")
        finally:
            os.umask(prior)
            os.close(fd)
        self.assertEqual(stat.S_IMODE((self.root / ".umask-fixture").stat().st_mode), 0o600)

    def test_new_private_mode_drift_refuses_before_data_write(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with patch.object(h.os, "fchmod", return_value=None), patch.object(
                    h, "write_all", side_effect=AssertionError("unsafe private write")):
                prior = os.umask(0o777)
                try:
                    with self.assertRaises(h.Refused):
                        h.exclusive_file(fd, ".denied-mode", b"private synthetic bytes")
                finally:
                    os.umask(prior)
        finally:
            os.close(fd)


    def test_real_descendant_drain_including_escaped_process_group(self):
        child = ("import subprocess,sys,time; "
                 "subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'], "
                 "start_new_session=True); time.sleep(60)")
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            run_fixture_process([sys.executable, "-I", "-c", child],
                                timeout=1, env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        receipt = caught.exception.drain_receipt
        self.assertTrue(receipt["all_owned_descendants_drained"])
        self.assertTrue(receipt["echild_verified"])
        self.assertGreaterEqual(receipt["adopted_children_reaped"], 1)

    def test_namespace_timeout_receipt_proves_descendant_drain(self):
        unshare, _ = self.namespace_tools()
        prefix = [unshare, "--user", "--map-root-user", "--mount", "--pid",
                  "--propagation", "private", "--fork", "--kill-child=KILL"]
        env = {"PATH": "/usr/bin:/bin", "LANG": "C"}
        probe = run_fixture_process(prefix + [sys.executable, "-I", "-c", "pass"],
                                    timeout=3, env=env)
        if namespace_capability_denied(probe):
            self.skipTest("NOT_RUN: unprivileged namespaces denied")
        self.assertEqual(probe.returncode, 0)
        child = ("import subprocess,sys,time; "
                 "subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']); "
                 "time.sleep(60)")
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            run_fixture_process(prefix + [sys.executable, "-I", "-c", child],
                                timeout=1, env=env)
        self.assertTrue(caught.exception.drain_receipt["all_owned_descendants_drained"])
        self.assertTrue(caught.exception.drain_receipt["echild_verified"])


    def test_namespace_classifier_recognizes_observed_uid_map_denial(self):
        diagnostic = b"unshare: write failed /proc/self/uid_map: Operation not permitted\n"
        self.assertTrue(namespace_capability_denied(
            subprocess.CompletedProcess(["unshare"], 1, b"", diagnostic)))

    def test_namespace_classifier_keeps_unknown_failures(self):
        for diagnostic in (b"unshare: invalid option", b"Traceback: fixture failure",
                           b"unshare: write failed /proc/self/uid_map: Operation not permitted\nextra failure",
                           b"\nunshare: write failed /proc/self/uid_map: Operation not permitted\n",
                           b"unshare: write failed /proc/self/uid_map: Operation not permitted\n\n"):
            with self.subTest(diagnostic=diagnostic):
                self.assertFalse(namespace_capability_denied(
                    subprocess.CompletedProcess(["unshare"], 1, b"", diagnostic)))

    def test_namespace_classifier_never_skips_success_or_killed_child(self):
        diagnostic = b"unshare: write failed /proc/self/uid_map: Operation not permitted"
        for code in (0, -9, 77):
            with self.subTest(code=code):
                self.assertFalse(namespace_capability_denied(
                    subprocess.CompletedProcess(["unshare"], code, b"", diagnostic)))

    def test_namespace_classifier_is_used_by_all_four_scenarios(self):
        scenarios = (
            self.test_namespace_timeout_kills_descendants_and_reaps_wrapper,
            self.test_namespace_timeout_receipt_proves_descendant_drain,
            self.test_actual_directory_bind_visibility_if_namespace_available,
            self.test_actual_file_bind_inode_pin_if_namespace_available,
        )
        diagnostics = (
            (b"unshare: write failed /proc/self/uid_map: Operation not permitted\n",
             unittest.SkipTest),
            (b"unshare failed: unexpected fixture error\n", AssertionError),
            (b"unshare: unshare failed: Operation not permitted\nextra failure\n",
             AssertionError),
        )
        # Exercise all four real entrypoints with synthetic process results.
        # No unshare, mount, child process, helper install or live access occurs.
        for scenario in scenarios:
            for diagnostic, expected in diagnostics:
                with self.subTest(scenario=scenario.__name__, diagnostic=diagnostic):
                    result = subprocess.CompletedProcess(["fixture-unshare"], 1, b"", diagnostic)
                    with patch.object(self, "namespace_tools",
                                      return_value=("fixture-unshare", "fixture-mount")), \
                            patch("test_hba_helper.run_fixture_process",
                                  return_value=result) as run:
                        with self.assertRaises(expected):
                            scenario()
                        run.assert_called_once()

    def test_actual_bind_classifies_embedded_mount_errors(self):
        snippets = [value for value in self.actual_bind.__func__.__code__.co_consts
                    if isinstance(value, str) and "result = subprocess.run([mount," in value]
        self.assertEqual(len(snippets), 1)
        child = compile(snippets[0], "<synthetic-bind-child>", "exec")
        hint = b"       dmesg(1) may have more information after failed mount system call.\n"
        for file_bind in (False, True):
            with tempfile.TemporaryDirectory(prefix="hba-synthetic-classifier-", dir="/tmp") as target:
                target_path = Path(target) / "pg_hba.conf" if file_bind else Path(target)
                denial = ("mount: " + str(target_path) + ": permission denied.\n").encode()
                cases = (
                    (32, b"", denial, 77),
                    (32, b"", denial + hint, 77),
                    (32, b"", denial + b"unexpected extra error\n", 78),
                    (32, b"unexpected output", denial, 78),
                    (1, b"", denial, 78),
                    (77, b"", denial, 78),
                    (-9, b"", denial, 78),
                    (32, b"", b"mount: wrong filesystem type\n", 78),
                )
                for code, stdout, stderr, expected in cases:
                    with self.subTest(file_bind=file_bind, code=code, stderr=stderr):
                        result = subprocess.CompletedProcess(["fixture-mount"], code, stdout, stderr)
                        output, errors = io.StringIO(), io.StringIO()
                        argv = ["fixture-child", str(Path(__file__).parent.resolve()),
                                str(self.root), target, "1" if file_bind else "0", "fixture-mount"]
                        with patch.object(sys, "argv", argv), \
                                patch.object(sys, "path", list(sys.path)), \
                                patch("subprocess.run", return_value=result) as mount_run, \
                                patch.object(h, "SyntheticHelper",
                                             side_effect=AssertionError("helper must not run")) as helper, \
                                redirect_stdout(output), redirect_stderr(errors):
                            with self.assertRaises(SystemExit) as caught:
                                exec(child, {"__name__": "__synthetic_bind_child__"})
                        self.assertEqual(caught.exception.code, expected)
                        mount_run.assert_called_once()
                        helper.assert_not_called()
                        if expected == 77:
                            self.assertEqual(output.getvalue(), "SKIP_BIND_PERMISSION_DENIED\n")
                            self.assertEqual(errors.getvalue(), "")
                        else:
                            self.assertEqual(output.getvalue(), "")
                            self.assertEqual(errors.getvalue(), stderr.decode(errors="replace"))

    def test_actual_bind_requires_exact_denial_receipt(self):
        for scenario in (self.test_actual_directory_bind_visibility_if_namespace_available,
                         self.test_actual_file_bind_inode_pin_if_namespace_available):
            for code, stdout, stderr, expected in (
                    (77, b"SKIP_BIND_PERMISSION_DENIED\n", b"", unittest.SkipTest),
                    (77, b"SKIP_BIND_UNAVAILABLE\n", b"", AssertionError),
                    (77, b"SKIP_BIND_PERMISSION_DENIED\n", b"extra error", AssertionError),
                    (78, b"SKIP_BIND_PERMISSION_DENIED\n", b"", AssertionError),
                    (32, b"", b"mount: wrong filesystem type", AssertionError)):
                with self.subTest(scenario=scenario.__name__, code=code, stdout=stdout, stderr=stderr):
                    result = subprocess.CompletedProcess(["fixture-unshare"], code, stdout, stderr)
                    with patch.object(self, "namespace_tools",
                                      return_value=("fixture-unshare", "fixture-mount")), \
                            patch("test_hba_helper.run_fixture_process",
                                  return_value=result) as run:
                        with self.assertRaises(expected):
                            scenario()
                        run.assert_called_once()

    def actual_bind(self, file_bind):
        unshare, mount = self.namespace_tools()
        with tempfile.TemporaryDirectory(prefix="hba-synthetic-view-", dir="/tmp") as target:
            child = r'''
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import hba_helper as h
source, target, file_bind, mount = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]=='1', sys.argv[5]
if file_bind:
    (target/'pg_hba.conf').write_bytes(b'empty synthetic target\n')
    source_path, target_path = source/'pg_hba.conf', target/'pg_hba.conf'
else:
    source_path, target_path = source, target
result = subprocess.run([mount,'--bind',str(source_path),str(target_path)], capture_output=True, timeout=5)
if result.returncode:
    # util-linux LANG=C EPERM forms only; unknown mount errors stay failures.
    message = ('mount: '+str(target_path)+': permission denied.\n').encode()
    hint = b'       dmesg(1) may have more information after failed mount system call.\n'
    if result.returncode == 32 and result.stdout == b'' and result.stderr in (message, message+hint):
        print('SKIP_BIND_PERMISSION_DENIED'); sys.exit(77)
    sys.stderr.write(result.stderr.decode(errors='replace'))
    sys.exit(78)
helper = h.SyntheticHelper(source)
original = helper.original['data']
try:
    helper.install()
    visible = (target/'pg_hba.conf').read_bytes()
    expected = original if file_bind else h.PREFIX+original
    if visible != expected: raise AssertionError('mount visibility mismatch')
    print('PASS_FILE_BIND_HAZARD' if file_bind else 'PASS_DIRECTORY_BIND_VISIBILITY')
finally:
    helper.close()
'''
            result = run_fixture_process([unshare, "--user", "--map-root-user", "--mount", "--pid", "--propagation", "private", "--fork", "--kill-child=KILL",
                                     sys.executable, "-I", "-B", "-c", child, str(Path(__file__).parent.resolve()),
                                     str(self.root), target, "1" if file_bind else "0", mount],
                                    timeout=15,
                                    env={"PATH": "/usr/bin:/bin", "LANG": "C"})
            if (namespace_capability_denied(result) or
                    (result.returncode == 77 and
                     result.stdout == b"SKIP_BIND_PERMISSION_DENIED\n" and
                     result.stderr == b"")):
                self.skipTest("NOT_RUN: unprivileged isolated mount namespace denied")
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertIn(b"PASS_", result.stdout)

    def test_actual_directory_bind_visibility_if_namespace_available(self):
        self.actual_bind(False)

    def test_actual_file_bind_inode_pin_if_namespace_available(self):
        self.actual_bind(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
