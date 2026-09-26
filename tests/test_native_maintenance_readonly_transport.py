import base64
import json
import os
from pathlib import Path
import subprocess
import signal
import select
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ops import native_maintenance_readonly_transport as transport
from ops import native_maintenance_bundle as bundle


class TransportTests(unittest.TestCase):
    def execute(self, code, action=None, heartbeat=0.3, duration=2):
        reader, writer = os.pipe()
        def ready(record):
            self.assertEqual(record, {'state': 'READY'})
            if action == 'eof':
                os.close(writer)
            elif isinstance(action, bytes):
                os.write(writer, action)
        try:
            return transport.supervise([sys.executable, '-I', '-B', '-S', '-c', code],
                                       '/tmp', reader, heartbeat=heartbeat,
                                       duration=duration, ready=ready)
        finally:
            os.close(reader)
            if action != 'eof':
                os.close(writer)

    def test_actual_child_success(self):
        self.assertEqual(self.execute("print('ok')"), b'ok\n')

    def test_control_loss_and_bounded_duration(self):
        for action, expected, heartbeat, duration in (
            (b'CANCEL\n', 'CANCELLED', 1, 2),
            ('eof', 'CONTROL_EOF', 1, 2),
            (None, 'HEARTBEAT_TIMEOUT', 0.15, 2),
            (None, 'TOTAL_TIMEOUT', 2, 0.15),
            (b'WRONG\n', 'CONTROL_INVALID', 1, 2),
            (b'x' * 100, 'CONTROL_SIZE', 1, 2),
            (b'BEAT\nBEAT\n', 'CONTROL_RATE', 1, 2),
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(transport.Stopped, expected):
                self.execute('import time; time.sleep(60)', action, heartbeat, duration)

    def test_child_failure_and_output_limit(self):
        for code, expected in [('raise SystemExit(7)', 'AUDIT_FAILED'),
                               ("print('x' * 20000)", 'OUTPUT_SIZE')]:
            with self.assertRaisesRegex(transport.Stopped, expected):
                self.execute(code)

    def test_packet_timeout_eof_and_pipelining(self):
        reader, writer = os.pipe()
        try:
            with self.assertRaisesRegex(transport.Stopped, 'PACKET_TIMEOUT'):
                transport.receive(reader, time.monotonic() + 0.02)
            os.write(writer, b'{}\nBEAT\n')
            with self.assertRaisesRegex(transport.Stopped, 'EARLY_CONTROL'):
                transport.receive(reader, time.monotonic() + 1)
            os.close(writer)
            writer = None
            with self.assertRaisesRegex(transport.Stopped, 'CONTROL_EOF'):
                transport.receive(reader, time.monotonic() + 1)
        finally:
            os.close(reader)
            if writer is not None:
                os.close(writer)

    def test_cancel_kills_term_ignoring_group_and_reaps_leader(self):
        reader, writer = os.pipe()
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'pids'
            child_marker = Path(directory) / 'child'
            code = '''
import os,signal,time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child=os.fork()
proc_pid=next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('Pid:'))
Path(%r if child else %r).write_text(proc_pid)
time.sleep(60)
''' % (str(marker), str(child_marker))
            errors = []
            def cancel():
                deadline = time.monotonic() + 2
                while not (marker.exists() and child_marker.exists()) and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not (marker.exists() and child_marker.exists()):
                    errors.append('child did not start')
                os.write(writer, b'CANCEL\n')
            sender = threading.Thread(target=cancel)
            try:
                with self.assertRaisesRegex(transport.Stopped, 'CANCELLED'):
                    transport.supervise([sys.executable, '-I', '-B', '-S', '-c', code],
                                        directory, reader, ready=lambda _: sender.start())
                sender.join(timeout=3)
                self.assertFalse(errors)
                leader, child = int(marker.read_text()), int(child_marker.read_text())
                self.assertFalse(Path(f'/proc/{leader}').exists())
                deadline = time.monotonic() + 2
                while Path(f'/proc/{child}/stat').exists():
                    state = Path(f'/proc/{child}/stat').read_text().rsplit(')', 1)[1].split()[0]
                    if state == 'Z':
                        break
                    self.assertLess(time.monotonic(), deadline, 'grandchild still running')
                    time.sleep(0.01)
            finally:
                sender.join(timeout=3)
                os.close(reader)
                os.close(writer)

    @unittest.skipUnless(os.getuid() == 0, 'root-only remote entrypoint')
    def test_real_bootstrap_decode_and_client_cancel_eof(self):
        source = 'a' * 40
        payload = bundle.canonical({'version': 1, 'source_sha': source, 'files': {
            path: base64.b64encode(b'# fixture\n').decode() for path in bundle.FILES}})
        def source_read(repo, *args):
            path = args[-1].split(':', 1)[1]
            return (Path(__file__).resolve().parents[1] / path).read_bytes()
        with patch.object(bundle, 'git', source_read):
            code = transport.bootstrap('.', source, bundle.digest(payload), 'probe')
        for behavior, expected in [('cancel', 'CANCELLED'), ('eof', 'CONTROL_EOF')]:
            self.assertEqual(transport.exchange([sys.executable, '-I', '-B', '-S', '-c', code],
                                                payload, behavior), expected)
        # The independent expected digest rejects modified wire data before READY.
        with self.assertRaises(bundle.BundleError):
            transport.exchange([sys.executable, '-I', '-B', '-S', '-c', code], payload + b' ', 'cancel')

        process = subprocess.Popen([sys.executable, '-I', '-B', '-S', '-c', code],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.stdin.write(payload + b'\n')
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline()), {'state': 'READY'})
            process.send_signal(signal.SIGTERM)
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            output = process.stdout.readline()
            rest, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(json.loads(output), {'result': 'SIGNAL_CANCELLED'})
            self.assertEqual(rest, b'')
            self.assertEqual(errors, b'')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        obj = json.loads(payload)
        audit = {'audit': 'ACTIVE_HOLD_PASS', 'light_active': True, 'admission': 'HOLD',
                 'live_dsn_matches_root_file': True, 'database_login': 'READ_ONLY_PASS',
                 'database_binding': 'NEON_PROJECT_BRANCH_ENDPOINT_PASS',
                 'queue_nonterminal': 0, 'same_invocation': True}
        obj['files']['ops/oracle_light_active_hold_attest.py'] = base64.b64encode(
            ('print(' + repr(json.dumps(audit)) + ')').encode()).decode()
        payload = bundle.canonical(obj)
        with patch.object(bundle, 'git', source_read):
            code = transport.bootstrap('.', source, bundle.digest(payload), 'audit')
        self.assertEqual(transport.exchange([sys.executable, '-I', '-B', '-S', '-c', code],
                                            payload, 'audit'), 'ACTIVE_HOLD_PASS')


if __name__ == '__main__':
    unittest.main()
