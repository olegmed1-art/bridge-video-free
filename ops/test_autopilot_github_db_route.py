import hashlib
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ops import github_autopilot_db_route as target


class ConsumerRouting(unittest.TestCase):
    def test_dsn_constructed_from_pinned_identity(self):
        raw='postgresql://autopilot_callback_login:synthetic-password@'+target.SOURCE+'/neondb?sslmode=require'
        raw=raw.replace('synthetic-password',target.quote('synthetic-password@:',safe=''))
        value=target.target_dsn(raw,'autopilot_callback_login','/tmp/ca.crt')
        self.assertIn('synthetic-password%40%3A@127.0.0.1:55432/autopilot?',value)
        self.assertIn('sslmode=verify-full',value)
        self.assertIn('channel_binding=require',value)
        for invalid in [raw.replace(target.SOURCE,'attacker.invalid'),raw+'&host=elsewhere',
                        raw.replace('/neondb','/other'),raw.replace('callback_login','runtime_login'),
                        raw+'\n',raw+'&sslmode=disable']:
            with self.subTest(invalid=invalid),self.assertRaises(AssertionError):
                target.target_dsn(invalid,'autopilot_callback_login','/tmp/ca.crt')

    def test_worker_legacy_quoted_input_has_fixed_oracle_destination(self):
        raw="'postgresql://bridge_school_worker_principal:synthetic%40password@legacy.neon.tech/neondb?sslmode=require&channel_binding=require'"
        value=target.target_dsn(raw,'bridge_school_worker_principal','/tmp/ca.crt')
        parsed=target.urlsplit(value)
        self.assertEqual((parsed.hostname,parsed.port,parsed.path),('127.0.0.1',55432,'/autopilot'))
        self.assertEqual(target.unquote(parsed.password),'synthetic@password')
        for invalid in (raw.replace('legacy.neon.tech','attacker.invalid'),raw.replace('/neondb','/other'),
                        raw.replace('legacy.neon.tech','legacy.neon.tech:9999'),raw+'\\n'):
            with self.assertRaises(AssertionError):
                target.target_dsn(invalid,'bridge_school_worker_principal','/tmp/ca.crt')

    def test_route_fails_closed_and_epoch_never_decreases(self):
        route={'version':1,'backend':'neon','database':'autopilot','epoch':4}
        with patch.object(target,'CA_SHA256',hashlib.sha256(b'certificate').hexdigest()):
            record={'route':route,'ca_pem':'certificate'}
            self.assertEqual(target.parse_record(json.dumps(record),4),record)
            for invalid in [{'route':route},{**record,'extra':1},{**record,'ca_pem':'other'},
                            {**record,'route':{**route,'backend':'invalid'}},
                            {**record,'route':{**route,'epoch':3}}]:
                with self.subTest(invalid=invalid),self.assertRaises(AssertionError):
                    target.parse_record(json.dumps(invalid),4)
        self.assertIsNone(target.parse_record('{"busy":true}',0))

    def test_completed_paused_process_header_is_read(self):
        p=subprocess.Popen([sys.executable,'-c','print(\'{"busy":true}\')'],stdout=subprocess.PIPE)
        p.wait(timeout=5)
        self.assertEqual(target.header(p),b'{"busy":true}')
        p.stdout.close()

    def test_lease_loss_kills_consumer_group(self):
        p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
        target.stop(p,group=True)
        self.assertIsNotNone(p.poll())

    def test_only_approved_entrypoints_exist(self):
        self.assertEqual(set(target.TARGETS),{'role-callback','codex-ack','codex-terminal','codex-publication','diagnostics','reconcile','next-step','mailbox','health'})
        self.assertEqual(target.TARGETS['codex-ack'][2:],('oracle_autopilot.github_codex_callback',('ack',)))
        self.assertEqual(target.TARGETS['codex-terminal'][2:],('oracle_autopilot.github_codex_callback',('terminal',)))
        self.assertEqual(target.TARGETS['codex-publication'][2],'oracle_autopilot.github_codex_publication')


class HostKeyScanDiagnostic(unittest.TestCase):
    def scan(self, *, result=None, error=None):
        values = {'side_effect': error} if error is not None else {
            'return_value': result if result is not None else subprocess.CompletedProcess(['synthetic'], 0)}
        with patch.object(target.subprocess, 'run', **values) as run:
            if error is None and result is None:
                self.assertIsNone(target.run_known_hosts_scanner('scanner', 'synthetic-host', 'synthetic-fingerprint', 'output'))
                caught = None
            else:
                with self.assertRaises(target.HostKeyScanError) as raised:
                    target.run_known_hosts_scanner('scanner', 'synthetic-host', 'synthetic-fingerprint', 'output')
                caught = raised.exception
        run.assert_called_once_with(['bash', 'scanner', 'synthetic-host', 'synthetic-fingerprint', 'output'],
                                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return caught

    def test_success_preserves_fixed_scanner_invocation(self):
        self.scan()

    def test_nonzero_exit_is_bounded_and_never_invents_fingerprint_mismatch(self):
        secret = 'SYNTHETIC_SECRET_NEVER_PRINT'
        error = subprocess.CalledProcessError(4, [secret], output=secret.encode(), stderr=secret.encode())
        caught = self.scan(error=error)
        self.assertEqual((caught.reason, caught.exit_code), (target.HostKeyScanReason.NONZERO_EXIT, 4))
        self.assertEqual(caught.args, ())
        with patch.object(target, '_failure_stage', target.RoutingStage.SSH_HOST_KEY), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            target.report_routing_failure(caught)
        self.assertEqual(output.getvalue(),
                         'AUTOPILOT_DATABASE_ROUTING_FAILED stage=SSH_HOST_KEY\n'
                         'AUTOPILOT_SSH_HOST_KEY_DIAGNOSTIC reason=SCANNER_NONZERO_EXIT exit_code=4\n')
        self.assertNotIn(secret, output.getvalue())

        legacy = self.scan(error=subprocess.CalledProcessError(255, [secret], stderr=secret.encode()))
        self.assertEqual((legacy.reason, legacy.exit_code), (target.HostKeyScanReason.NONZERO_EXIT, 255))

    def test_candidate_stage_exit_is_fixed_and_does_not_identify_pipeline_command(self):
        secret = 'SYNTHETIC_SECRET_NEVER_PRINT'
        cases = (
            {'error': subprocess.CalledProcessError(6, [secret], output=secret.encode(), stderr=secret.encode())},
            {'result': subprocess.CompletedProcess([secret], 6, stdout=secret.encode(), stderr=secret.encode())},
        )
        for case in cases:
            with self.subTest(source=next(iter(case))):
                caught = self.scan(**case)
                self.assertEqual((caught.reason, caught.exit_code),
                                 (target.HostKeyScanReason.CANDIDATE_STAGE, 6))
                self.assertEqual(caught.args, ())
                with patch.object(target, '_failure_stage', target.RoutingStage.SSH_HOST_KEY), \
                        contextlib.redirect_stderr(io.StringIO()) as output:
                    target.report_routing_failure(caught)
                self.assertEqual(output.getvalue(),
                                 'AUTOPILOT_DATABASE_ROUTING_FAILED stage=SSH_HOST_KEY\n'
                                 'AUTOPILOT_SSH_HOST_KEY_DIAGNOSTIC '
                                 'reason=SCANNER_CANDIDATE_STAGE_FAILED exit_code=6\n')
                self.assertNotIn(secret, output.getvalue())

    def test_timeout_spawn_failure_signal_and_invalid_result_fail_closed(self):
        secret = 'SYNTHETIC_SECRET_NEVER_PRINT'
        cases = (
            (subprocess.TimeoutExpired([secret], 30, output=secret.encode(), stderr=secret.encode()),
             target.HostKeyScanReason.TIMEOUT, None),
            (FileNotFoundError(2, secret, secret), target.HostKeyScanReason.EXECUTABLE_MISSING, None),
            (PermissionError(13, secret, secret), target.HostKeyScanReason.EXECUTION_DENIED, None),
            (subprocess.CalledProcessError(-9, [secret]), target.HostKeyScanReason.SIGNALLED, -9),
            (subprocess.CalledProcessError('4', [secret]), target.HostKeyScanReason.RESULT_INVALID, None),
            (subprocess.CalledProcessError(999, [secret]), target.HostKeyScanReason.RESULT_INVALID, None),
        )
        for error, reason, exit_code in cases:
            with self.subTest(reason=reason):
                caught = self.scan(error=error)
                self.assertEqual((caught.reason, caught.exit_code), (reason, exit_code))
                with patch.object(target, '_failure_stage', target.RoutingStage.SSH_HOST_KEY), \
                        contextlib.redirect_stderr(io.StringIO()) as output:
                    target.report_routing_failure(caught)
                self.assertNotIn(secret, output.getvalue())
        caught = self.scan(result=subprocess.CompletedProcess([secret], True))
        self.assertEqual((caught.reason, caught.exit_code), (target.HostKeyScanReason.RESULT_INVALID, None))


if __name__=='__main__':
    unittest.main()
