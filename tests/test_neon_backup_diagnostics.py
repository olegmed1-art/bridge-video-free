"""Synthetic libpq/psql/Docker fixtures; no database or secret access."""
import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from ops import neon_backup_source as source
from ops import neon_backup_preflight_once as once
from test_neon_backup_source import evidence

PRIVATE = 'synthetic-password-and-private-dsn-do-not-emit'
FIXTURES = [
    (2, 'psql: error: connection to server at "host" failed: FATAL: password authentication failed for user "owner"', 'AUTHENTICATION_FAILED'),
    (2, 'psql: error: connection failed: fe_sendauth: no password supplied', 'AUTHENTICATION_FAILED'),
    (2, 'psql: error: connection failed: SSL error: certificate verify failed', 'TLS_FAILED'),
    (2, 'psql: error: root certificate file "/backup-ca.crt" does not exist', 'TLS_FAILED'),
    (2, 'psql: error: channel binding required, but server authenticated client without channel binding', 'TLS_FAILED'),
    (2, 'psql: error: could not translate host name "host" to address: Name or service not known', 'NETWORK_FAILED'),
    (2, 'psql: error: connection to server at "host" failed: Connection refused', 'NETWORK_FAILED'),
    (2, 'psql: error: connection to server at "host" failed: timeout expired', 'CONNECT_TIMEOUT'),
    (3, 'ERROR:  57014: canceling statement due to statement timeout\nLOCATION: ProcessInterrupts, postgres.c:3400', 'STATEMENT_TIMEOUT'),
    (3, 'ERROR:  55P03: canceling statement due to lock timeout', 'LOCK_TIMEOUT'),
    (3, 'ERROR:  57014: canceling statement due to user request', 'SQL_FAILED'),
    (3, 'ERROR:  55P03: could not obtain lock on relation fixture', 'SQL_FAILED'),
    (3, 'ERROR:  42601: syntax error at or near "password authentication failed"\nLINE 1: connection refused', 'SQL_FAILED'),
    (3, 'ERROR:  42501: permission denied for table "connection refused row-level security"', 'ACL_DENIED'),
    (3, 'ERROR:  42601: syntax error at or near "SELECT"\nLINE 1: SELECT private_data', 'SQL_FAILED'),
    (3, 'ERROR:  42883: function has_largeobject_privilege(oid, unknown) does not exist', 'SQL_FAILED'),
    (3, 'ERROR:  42809: "sample" is not a sequence', 'SQL_FAILED'),
    (3, 'ERROR:  42501: permission denied for table sensitive_table', 'ACL_DENIED'),
    (3, 'ERROR:  42501: query would be affected by row-level security policy for table "sensitive_table"', 'RLS_ACTIVE'),
    (3, 'ERROR:  25006: cannot execute INSERT in a read-only transaction', 'READ_ONLY_REFUSED'),
    (125, 'docker: Error response from daemon: invalid mount config for type "bind": bind source path does not exist', 'TOOL_FAILED'),
    (125, 'docker: Cannot connect to the Docker daemon', 'TOOL_FAILED'),
    (127, 'docker: executable file not found in PATH', 'TOOL_FAILED'),
    (1, 'unexpected untranslated diagnostic', 'TOOL_FAILED'),
]


class DiagnosticTests(unittest.TestCase):
    def test_realistic_failure_fixtures_return_only_allowlisted_codes(self):
        for rc, text, expected in FIXTURES:
            with self.subTest(expected=expected, text=text):
                self.assertEqual(source.classify_client_failure(rc, text + '\n' + PRIVATE), expected)
                with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], rc, PRIVATE, text)), \
                     self.assertRaises(source.BackupFailure) as failure:
                    source.client({}, ['psql'])
                self.assertEqual(str(failure.exception), expected)
                self.assertNotIn(PRIVATE, str(failure.exception))
                self.assertIn(failure.exception.code, source.FAILURE_CODES)

    def test_sanitization_through_actual_observer_and_entrypoint(self):
        for rc, text, expected in FIXTURES:
            with self.subTest(expected=expected), patch.object(once, 'context', return_value='a'*40), \
                 patch.dict(os.environ, {'GITHUB_RUN_ID':'123', 'DATABASE_URL':PRIVATE}, clear=True), \
                 patch.object(once, 'check_main'), patch.object(source, 'parameters', return_value={}), \
                 patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], rc, PRIVATE, text+'\n'+PRIVATE)):
                row = once.observe()
            encoded = json.dumps(row)
            self.assertEqual(row['failure_code'], expected)
            self.assertEqual(row['status'], 'FAIL')
            self.assertNotIn(PRIVATE, encoded)
            self.assertEqual(set(row['gates'].values()), {'NOT_PROVEN'})
            stderr = io.StringIO()
            with patch.object(source.sys, 'argv', ['backup', 'preflight']), \
                 patch.object(source, 'main', side_effect=source.BackupFailure(expected)), \
                 contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                source.entrypoint()
            self.assertEqual(stderr.getvalue(), 'BACKUP_SOURCE_REFUSED:'+expected+'\n')

    def test_catalog_acl_rls_identity_and_invalid_output_are_distinct(self):
        self.assertIn("CASE WHEN c.relkind='S' THEN NOT has_sequence_privilege", source.PREFLIGHT)
        cases = []
        for key in ('denied_tables','denied_schemas','denied_sequences','denied_largeobjects'):
            row=evidence(); row[key]=1; cases.append((row,'ACL_DENIED'))
        row=evidence(); row['denied_rls']=1; cases.append((row,'RLS_ACTIVE'))
        row=evidence(); row['identity'][0][1]='wrong'; cases.append((row,'IDENTITY_MISMATCH'))
        row=evidence(); row['session'][3]='off'; cases.append((row,'READ_ONLY_REFUSED'))
        row=evidence(); row['denied_tables']=None; cases.append((row,'OUTPUT_INVALID'))
        for row, code in cases:
            gates={key:'NOT_PROVEN' for key in ('auth','readonly','identity','acl','rls')}
            with patch.object(source, 'client', return_value=json.dumps(row)), self.assertRaises(source.BackupFailure) as failure:
                source.preflight({}, gates=gates)
            self.assertEqual(failure.exception.code, code)
            if code in ('ACL_DENIED','RLS_ACTIVE'):
                self.assertEqual(gates['auth'], 'PASS')
                self.assertEqual(gates['identity'], 'PASS')
            if code=='RLS_ACTIVE': self.assertEqual(gates['acl'], 'PASS')
        for payload in ('not-json '+PRIVATE, '[]', 'null', '{}'):
            with patch.object(source, 'client', return_value=payload), self.assertRaises(source.BackupFailure) as failure:
                source.preflight({})
            self.assertEqual(failure.exception.code, 'OUTPUT_INVALID')
            self.assertNotIn(PRIVATE, str(failure.exception))

    def test_client_timeout_cleanup_and_missing_tool_do_not_leak(self):
        for cleanup in (subprocess.CompletedProcess([],0), subprocess.CompletedProcess([],1,PRIVATE,PRIVATE),
                        subprocess.TimeoutExpired(['private'],15), OSError(PRIVATE)):
            expected='CLIENT_TIMEOUT' if isinstance(cleanup,subprocess.CompletedProcess) and cleanup.returncode==0 else 'CLIENT_TIMEOUT_CLEANUP_FAILED'
            with patch.object(subprocess,'run',side_effect=[subprocess.TimeoutExpired(['private'],45),cleanup]) as run, \
                 self.assertRaises(source.BackupFailure) as failure:
                source.client({},['psql'])
            self.assertEqual(failure.exception.code,expected)
            self.assertEqual(run.call_count,2)
            self.assertNotIn(PRIVATE,str(failure.exception))
        with patch.object(subprocess,'run',side_effect=FileNotFoundError(PRIVATE)), self.assertRaises(source.BackupFailure) as failure:
            source.client({},['psql'])
        self.assertEqual(failure.exception.code,'TOOL_FAILED')

    def test_untrusted_exception_code_cannot_escape_allowlist(self):
        exc=source.BackupFailure(PRIVATE)
        self.assertEqual(str(exc),'TOOL_FAILED')
        exc.code=PRIVATE
        with patch.object(once,'context',return_value='a'*40), \
             patch.dict(os.environ,{'GITHUB_RUN_ID':'123','DATABASE_URL':PRIVATE},clear=True), \
             patch.object(once,'check_main'), patch.object(source,'parameters',return_value={}), \
             patch.object(source,'preflight',side_effect=exc):
            row=once.observe()
        self.assertEqual(row['failure_code'],'TOOL_FAILED')
        self.assertNotIn(PRIVATE,json.dumps(row))


if __name__ == '__main__': unittest.main()
