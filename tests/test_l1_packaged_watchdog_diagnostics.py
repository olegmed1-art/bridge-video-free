"""Offline negative control-flow fixtures; no Docker, application, DB or network."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'tools' / 'l1_packaged_qualification'
spec = importlib.util.spec_from_file_location('diagnostic_watchdog', HERE / 'packaged_l1_watchdog.py')
W = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = W
spec.loader.exec_module(W)
D = W.driver
SPEC = D.load_spec(HERE / 'PACKAGED_L1_CHECK_SPEC.json')
IMAGE, CID, TOKEN = 'sha256:' + 'a' * 64, 'b' * 64, 'c' * 32
SECRET = 'diagnostic-fixture-private-value-DO-NOT-PRINT'


def emit(value):
    return W.Captured(0, json.dumps(value).encode(), b'')


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FixtureDocker:
    """Only a deterministic command fixture, never a packaged acceptance result."""
    def __init__(self, clock, faults=None, delays=None, running=False):
        self.clock, self.faults, self.delays = clock, faults or {}, delays or {}
        self.running, self.started = running is True, False
        self.run_after_start = bool(running)
        self.calls, self.counts = [], {}

    def __call__(self, args, timeout):
        verb = args[0] + (':' + args[1] if args[0] == 'image' else '')
        self.counts[verb] = self.counts.get(verb, 0) + 1
        key = (verb, self.counts[verb])
        self.calls.append((list(args), timeout))
        self.clock.now += self.delays.get(key, 0)
        if verb == 'start':
            self.started = True
            self.running = self.run_after_start
        if key in self.faults:
            value = self.faults[key]
            if isinstance(value, Exception):
                raise value
            return value
        if verb == 'info':
            return emit(dict(OSType='linux', Architecture='amd64', CgroupVersion='2',
                MemoryLimit=True, SwapLimit=True, CpuCfsQuota=True, PidsLimit=True,
                NCPU=2, MemTotal=2147483648))
        if verb == 'image:inspect':
            return emit([dict(Id=IMAGE, Os='linux', Architecture='amd64', Config=dict(
                User='65532:65532', WorkingDir='/app', Entrypoint=None, Volumes=None,
                Cmd=['uvicorn', 'bridge_school_api.main:app', '--host', '0.0.0.0', '--port', '8000'],
                Env=[], Labels={'org.opencontainers.image.revision': D.SOURCE,
                    'school.source-tree': D.TREE, 'school.context-sha256': 'd' * 64,
                    'school.dependencies-sha256': 'e' * 64}))])
        if verb == 'create':
            return W.Captured(0, CID.encode(), b'')
        if verb == 'inspect':
            command = W.create_arguments(IMAGE, TOKEN, HERE / 'packaged_l1_driver.py', HERE / 'PACKAGED_L1_CHECK_SPEC.json')
            return emit([dict(Id=CID, Image=IMAGE, Name='/l1-packaged-' + TOKEN,
                Config=dict(Labels={W.LABEL: TOKEN}, User='65532:65532', WorkingDir='/app',
                    Tty=False, OpenStdin=False, Entrypoint=['python'],
                    Cmd=command[command.index(IMAGE) + 1:], Env=['BRIDGE_API_TOKEN=' + D.TOKEN]),
                HostConfig=dict(Memory=536870912, MemorySwap=536870912, NanoCpus=1000000000,
                    PidsLimit=64, ReadonlyRootfs=True, NetworkMode='none', Privileged=False,
                    PublishAllPorts=False, CgroupnsMode='private', CapDrop=['ALL'],
                    SecurityOpt=['no-new-privileges']),
                Mounts=[dict(Type='bind', RW=False, Destination=dst, Source=str(HERE / src))
                    for dst, src in [('/qualification/driver.py', 'packaged_l1_driver.py'),
                                     ('/qualification/spec.json', 'PACKAGED_L1_CHECK_SPEC.json')]],
                State=dict(Running=self.running, Status='running' if self.running else
                    ('exited' if self.started else 'created'), OOMKilled=False, ExitCode=0))])
        if verb == 'start':
            return W.Captured(7, b'password=' + SECRET.encode(),
                ('Traceback (most recent call last):\n  File "/qualification/driver.py", line 320, in run_qualified\n'
                 '    secret = "' + SECRET + '"\nModuleNotFoundError: ' + SECRET + '\n').encode())
        if verb == 'kill':
            self.running = False
            return W.Captured(0, b'', b'')
        if verb == 'rm':
            return W.Captured(0, b'', b'')
        raise AssertionError('unexpected fixture command')


class DiagnosticsTests(unittest.TestCase):
    def run_fixture(self, faults=None, delays=None, running=False, verifier=None):
        clock = Clock()
        fake = FixtureDocker(clock, faults, delays, running)
        admission = dict(image_id=IMAGE, context_sha256='d' * 64, dependencies_sha256='e' * 64)
        run = W.DockerRun(fake, admission, SPEC, HERE / 'packaged_l1_driver.py',
                          HERE / 'PACKAGED_L1_CHECK_SPEC.json', TOKEN)
        # This stub reaches cleanup branches only; it never qualifies an application.
        with patch.object(W.time, 'monotonic', clock), patch.object(W, 'verify_run_result',
                side_effect=verifier, return_value=None):
            out = run.run()
        return out, fake

    def assert_safe(self, out):
        text = json.dumps(out, ensure_ascii=True)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(D.TOKEN, text)
        self.assertLess(len(text.encode()), 100000)
        self.assertLessEqual(len(out['diagnostics']['commands']), 16)
        for row in out['diagnostics']['commands']:
            self.assertTrue(row['exit_code'] is None or type(row['exit_code']) is int)
            for name in ('stdout', 'stderr'):
                self.assertLessEqual(len(row[name]['text'].encode()), 2048)

    def test_driver_exit_is_retained_and_secret_safe(self):
        out, fake = self.run_fixture()
        self.assertEqual(out['code'], 'DRIVER_EXIT')
        self.assertEqual(out['primary_code'], 'DRIVER_EXIT')
        row = next(x for x in out['diagnostics']['commands'] if x['command'] == 'start-attach')
        self.assertEqual(row['exit_code'], 7)
        self.assertIn('ModuleNotFoundError', row['stderr']['text'])
        self.assertIn('driver.py:320', row['stderr']['text'])
        self.assertEqual(out['cleanup'], 'OWNED_STOPPED_CONTAINER_REMOVED')
        self.assertEqual(sum(a[0] == 'create' for a, _ in fake.calls), 1)
        self.assert_safe(out)

    def test_preflight_failures_also_have_bounded_diagnostics(self):
        for verb in ('info', 'image:inspect', 'create', 'inspect'):
            with self.subTest(verb=verb):
                out, _ = self.run_fixture({(verb, 1): W.Captured(19, SECRET.encode(), SECRET.encode())})
                self.assertEqual(out['status'], 'FAIL_CLOSED')
                self.assertTrue(any(r['exit_code'] == 19 for r in out['diagnostics']['commands']))
                self.assert_safe(out)

    def test_unknown_exception_messages_are_never_printed(self):
        out, _ = self.run_fixture({('info', 1): ValueError(SECRET)})
        self.assertEqual(out['code'], 'UNEXPECTED_FAILURE')
        self.assertIsNone(out['diagnostics']['commands'][0]['exit_code'])
        self.assert_safe(out)

    def test_timeout_overflow_and_malformed_output_retain_streams(self):
        cases = [W.Captured(-9, SECRET.encode(), SECRET.encode(), timed_out=True),
                 W.Captured(-9, SECRET.encode(), SECRET.encode(), overflow=True),
                 W.Captured(0, b'{invalid', SECRET.encode())]
        for captured in cases:
            with self.subTest(captured=captured.returncode):
                out, _ = self.run_fixture({('start', 1): captured})
                self.assertEqual(out['status'], 'FAIL_CLOSED')
                row = next(r for r in out['diagnostics']['commands'] if r['command'] == 'start-attach')
                self.assertEqual(row['exit_code'], captured.returncode)
                self.assertEqual(row['timed_out'], captured.timed_out)
                self.assertEqual(row['overflow'], captured.overflow)
                self.assert_safe(out)

    def test_cleanup_failure_does_not_replace_primary_diagnostics(self):
        for verb, nth, running in [('inspect', 2, False), ('kill', 1, 'after-start'), ('rm', 1, False)]:
            with self.subTest(verb=verb):
                out, _ = self.run_fixture({(verb, nth): W.Captured(23, SECRET.encode(), SECRET.encode())}, running=running)
                self.assertEqual(out['status'], 'FAIL_CLOSED')
                self.assertEqual(out['primary_code'], 'DRIVER_EXIT')
                self.assertTrue(any(r['exit_code'] == 23 for r in out['diagnostics']['commands']))
                self.assertEqual(out['cleanup'], 'NO_CONFIRMED_OWNED_CLEANUP')
                self.assert_safe(out)

    def test_post_result_failure_and_cleanup_keep_driver_command(self):
        for faults, rejection in [({}, D.Rejected('RESULT_BOUNDARY')),
                ({('inspect', 2): W.Captured(31, b'', SECRET.encode())}, None)]:
            out, _ = self.run_fixture({('start', 1): W.Captured(0, b'{}', b''), **faults}, verifier=rejection)
            self.assertEqual(out['status'], 'FAIL_CLOSED')
            self.assertTrue(any(r['command'] == 'start-attach' and r['exit_code'] == 0
                                for r in out['diagnostics']['commands']))
            self.assert_safe(out)

    def test_deadline_overwrite_keeps_original_driver_exit(self):
        out, fake = self.run_fixture(delays={('start', 1): 55.001})
        self.assertEqual(out['code'], 'ACTIVE_CLEANUP_BOUND_EXCEEDED')
        self.assertEqual(out['primary_code'], 'DRIVER_EXIT')
        self.assertEqual(out['cleanup'], 'NO_CONFIRMED_OWNED_CLEANUP')
        self.assertFalse(any(a[0] in ('rm', 'kill') for a, _ in fake.calls))
        self.assert_safe(out)

    def test_exact_55_seconds_does_not_authorize_extra_command(self):
        out, fake = self.run_fixture(delays={('start', 1): 55})
        self.assertEqual(out['status'], 'FAIL_CLOSED')
        self.assertEqual(out['primary_code'], 'DRIVER_EXIT')
        self.assertFalse(any(a[0] in ('rm', 'kill') for a, _ in fake.calls))
        self.assert_safe(out)

    def test_record_limits_and_non_utf8_hide_unknown_text(self):
        out, _ = self.run_fixture({('start', 1): W.Captured(17,
            b'\xff' + SECRET.encode() * 20000,
            (b'  File "/qualification/driver.py", line 320, in run\n' * 10000) + SECRET.encode())})
        self.assert_safe(out)

    def test_partial_capture_failure_preserves_observed_exit(self):
        failure = W.CaptureFailure('CHILD_PIPE_NOT_CLOSED', W.Captured(11, b'abc', SECRET.encode()))
        out, _ = self.run_fixture({('start', 1): failure})
        row = next(r for r in out['diagnostics']['commands'] if r['command'] == 'start-attach')
        self.assertEqual(row['exit_code'], 11)
        self.assertEqual(row['stdout']['captured_bytes'], 3)
        self.assert_safe(out)

    def test_real_driver_failure_envelope_retained_but_unknown_fields_hidden(self):
        for value, visible in [({'status': 'FAIL_CLOSED', 'code': 'CGROUP_MEMORY'}, True),
                ({'status': 'FAIL_CLOSED', 'code': SECRET}, False),
                ({'status': 'FAIL_CLOSED', 'code': 'CGROUP_MEMORY', 'password': SECRET}, False)]:
            row = W.stream_diagnostic(json.dumps(value).encode(), 262144)
            self.assertEqual('CGROUP_MEMORY' in row['text'], visible)
            self.assertNotIn(SECRET, json.dumps(row))

    def test_dynamic_real_producer_audit_codes_are_closed_and_preserved(self):
        for kind in ('network', 'database', 'write', 'process'):
            audit = D.IOAudit()
            with self.assertRaises(D.Rejected) as got:
                audit.deny(kind)
            code = str(got.exception)
            raw = json.dumps({'status': 'FAIL_CLOSED', 'code': code}).encode()
            self.assertEqual(json.loads(W.stream_diagnostic(raw, 262144)['text'])['code'], code)
        for suffix in ('SECRET_' + SECRET, 'NETWORK_EXTRA', 'PASSWORD'):
            row = W.stream_diagnostic(json.dumps({'status': 'FAIL_CLOSED', 'code': 'FORBIDDEN_' + suffix}).encode(), 262144)
            self.assertEqual(row['text'], '')

    def test_diagnostic_parser_failure_never_masks_primary_failure(self):
        for raw in [b'[' * 2000 + b']' * 2000, b'9' * 10000,
                    json.dumps({'status': 'FAIL_CLOSED', 'code': []}).encode()]:
            out, _ = self.run_fixture({('start', 1): W.Captured(27, raw, b'')})
            self.assertEqual(out['primary_code'], 'DRIVER_EXIT')
            self.assert_safe(out)

    def test_uncertain_create_and_foreign_cleanup_keep_evidence_without_retry(self):
        out, fake = self.run_fixture({('create', 1): W.Captured(-9, b'', SECRET.encode(), timed_out=True),
            ('inspect', 1): emit([dict(Id=CID, Image=IMAGE, Name='/foreign', Config={})])})
        self.assertEqual(out['primary_code'], 'CREATE_OUTCOME_UNCERTAIN')
        self.assertTrue(out['uncertain_outcome'])
        self.assertEqual(sum(a[0] == 'create' for a, _ in fake.calls), 1)
        self.assertFalse(any(a[0] in ('start', 'kill', 'rm') for a, _ in fake.calls))
        self.assert_safe(out)

    def test_cleanup_failures_after_control_success_keep_primary_status(self):
        for captured in [W.Captured(29, b'', SECRET.encode()),
                         W.Captured(-9, b'', SECRET.encode(), timed_out=True),
                         W.Captured(0, b'', SECRET.encode(), overflow=True)]:
            out, _ = self.run_fixture({('start', 1): W.Captured(0, b'{}', b''), ('rm', 1): captured})
            self.assertEqual(out['status'], 'FAIL_CLOSED')
            self.assertIsNone(out['primary_code'])
            self.assertEqual(out['previous_status'], 'PASS_PACKAGED_ASGI_ONLY')
            self.assertTrue(out['uncertain_outcome'])
            self.assert_safe(out)

    def test_preflight_20_second_bound_still_prevents_extra_calls(self):
        out, fake = self.run_fixture(delays={('info', 1): 20})
        self.assertEqual(out['primary_code'], 'TOTAL_DEADLINE_EXHAUSTED')
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(out['cleanup'], 'NOT_CREATED')
        self.assert_safe(out)

    def test_real_stdlib_nonzero_capture_not_a_packaged_reproduction(self):
        # Actual isolated Python child; no Docker/application import or I/O.
        code = ('import sys; print(\'{"status":"FAIL_CLOSED","code":"CGROUP_MEMORY"}\'); '
                'sys.stderr.write("ModuleNotFoundError: ' + SECRET + '\\n"); sys.exit(7)')
        captured = W.capture([sys.executable, '-I', '-B', '-c', code], timeout=5)
        self.assertEqual(captured.returncode, 7)
        out, _ = self.run_fixture({('start', 1): captured})
        self.assertEqual(out['primary_code'], 'DRIVER_EXIT')
        row = next(r for r in out['diagnostics']['commands'] if r['command'] == 'start-attach')
        self.assertIn('CGROUP_MEMORY', row['stdout']['text'])
        self.assert_safe(out)


class CaptureErrorTests(unittest.TestCase):
    class Process:
        def __init__(self, wait_error=False):
            self.stdout, self.stderr = io.BytesIO(b'partial output'), io.BytesIO(SECRET.encode())
            self.returncode, self.wait_error, self.kills = 7, wait_error, 0
        def poll(self):
            return self.returncode
        def kill(self):
            self.kills += 1
        def wait(self, timeout):
            if self.wait_error:
                raise W.subprocess.TimeoutExpired('fixture', timeout)
            return self.returncode

    class Thread:
        alive = False
        fail_start = False
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args
        def start(self):
            if self.fail_start:
                raise RuntimeError(SECRET)
            self.target(*self.args)
        def join(self, timeout):
            pass
        def is_alive(self):
            return self.alive

    def exercise(self, process, thread=None):
        with patch.object(W.subprocess, 'Popen', return_value=process), \
                patch.object(W.threading, 'Thread', thread or self.Thread):
            with self.assertRaises(W.CaptureFailure) as got:
                W.capture(['fixture-only'], timeout=0.1)
        return got.exception

    def test_wait_timeout_keeps_partial_pipes_and_observed_exit(self):
        process = self.Process(wait_error=True)
        error = self.exercise(process)
        self.assertEqual(str(error), 'CHILD_STOP_UNCONFIRMED')
        self.assertEqual(error.captured.returncode, 7)
        self.assertEqual(error.captured.stdout, b'partial output')
        self.assertTrue(error.captured.timed_out)
        self.assertEqual(process.kills, 1)

    def test_unclosed_pipe_keeps_partial_pipes(self):
        class Alive(self.Thread):
            alive = True
        error = self.exercise(self.Process(), Alive)
        self.assertEqual(str(error), 'CHILD_PIPE_NOT_CLOSED')
        self.assertEqual(error.captured.stdout, b'partial output')

    def test_reader_failure_is_captured_without_thread_traceback(self):
        class Broken(io.BytesIO):
            def read1(self, count):
                raise OSError(SECRET)
        process = self.Process()
        process.stderr = Broken()
        error = self.exercise(process)
        self.assertEqual(str(error), 'CHILD_PIPE_READ_FAILED')
        self.assertEqual(error.captured.stdout, b'partial output')

    def test_reader_start_failure_stops_owned_cli_in_existing_budget(self):
        class StartFailure(self.Thread):
            fail_start = True
        process = self.Process()
        error = self.exercise(process, StartFailure)
        self.assertEqual(str(error), 'CHILD_CAPTURE_UNCONFIRMED')
        self.assertEqual(process.kills, 1)
        self.assertEqual(error.captured.returncode, 7)

    def test_start_failure_never_invents_an_exit_code(self):
        with patch.object(W.subprocess, 'Popen', side_effect=OSError(SECRET)):
            with self.assertRaises(W.CaptureFailure) as got:
                W.capture(['fixture-only'], timeout=0.1)
        self.assertEqual(str(got.exception), 'CHILD_START_FAILED')
        self.assertIsNone(got.exception.captured.returncode)


if __name__ == '__main__':
    unittest.main(verbosity=2)
