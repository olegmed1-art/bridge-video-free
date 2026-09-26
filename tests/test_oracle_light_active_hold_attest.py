import json
import unittest
import contextlib
import io
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from ops import oracle_light_active_hold_attest as attest


class ActiveHoldContract(unittest.TestCase):
    def run_child(self, tags, host=attest.HOST, count=0,
                  identity=('autopilot_light_worker_login','neondb','on')):
        queries=[]
        settings={}
        class Connection:
            info=SimpleNamespace(host=host,port=5432)
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def execute(self, query):
                queries.append(query)
                if 'pg_settings' in query:
                    return SimpleNamespace(fetchall=lambda: tags)
                return SimpleNamespace(fetchone=lambda:
                    (count,) if 'count(*)' in query else
                    identity)
        def connect(*args,**kwargs):
            settings.update(kwargs)
            return Connection()
        output=io.StringIO()
        with patch.dict(sys.modules,{'psycopg':SimpleNamespace(connect=connect)}), \
             patch.dict('os.environ',{'AUDIT_DATABASE_URL':'postgresql://private-secret'}), \
             contextlib.redirect_stdout(output):
            try:
                exec(compile(attest.CHILD,'attestation-child','exec'),{})
            except SystemExit as exc:
                self.assertEqual(exc.code,2)
        self.assertEqual(settings['sslmode'],'verify-full')
        self.assertEqual(settings['sslrootcert'],'system')
        self.assertEqual(settings['gssencmode'],'disable')
        self.assertIn('default_transaction_read_only=on',settings['options'])
        self.assertNotIn('private-secret',output.getvalue())
        return json.loads(output.getvalue()),queries

    def test_actual_child_rejects_wrong_neon_binding_before_queue_query(self):
        tags=[
            ('neon.project_id','misty-poetry-18012774','postmaster','configuration file','misty-poetry-18012774',False),
            ('neon.branch_id','br-aged-mud-b1i64914','postmaster','configuration file','br-aged-mud-b1i64914',False),
            ('neon.endpoint_id','ep-noisy-pine-b1pe30sf','superuser','configuration file','ep-noisy-pine-b1pe30sf',False)]
        self.assertEqual(self.run_child(tags)[0],{'ok':True})
        self.assertEqual(self.run_child(tags,count=1)[0],{'ok':False})
        for identity in (('wrong','neondb','on'),
                         ('autopilot_light_worker_login','wrong','on'),
                         ('autopilot_light_worker_login','neondb','off')):
            result,queries=self.run_child(tags,identity=identity)
            self.assertEqual(result,{'ok':False})
            self.assertEqual(len(queries),1)
        variants=[tags[:-1],tags+[tags[0]]]
        for row in range(3):
            for column,value in ((1,'wrong'),(2,'user'),(3,'client'),(4,'wrong'),(5,True)):
                changed=[list(t) for t in tags]
                changed[row][column]=value
                variants.append(changed)
        for changed in variants:
            with self.subTest(tags=changed):
                result,queries=self.run_child(changed)
                self.assertEqual(result,{'ok':False})
                self.assertFalse(any('task_status' in q for q in queries))
        result,queries=self.run_child(tags,host='wrong.neon.tech')
        self.assertEqual(result,{'ok':False})
        self.assertFalse(any('task_status' in q for q in queries))

    def test_env_rejects_duplicate_and_only_returns_values_in_memory(self):
        raw=b'AUTOPILOT_DATABASE_URL=postgresql://a:private-secret@host/db\n'
        self.assertEqual(attest.env(raw)['AUTOPILOT_DATABASE_URL'],
                         'postgresql://a:private-secret@host/db')
        with self.assertRaises(attest.Blocked):
            attest.env(raw+raw)

    def test_login_child_uses_read_only_session_and_checks_empty_queue(self):
        self.assertIn('default_transaction_read_only=on',attest.CHILD)
        self.assertIn("SELECT count(*) FROM autopilot.task_status",attest.CHILD)
        self.assertIn("current_setting('transaction_read_only')",attest.CHILD)

    def test_bad_login_does_not_output_dsn(self):
        with patch.object(attest.pwd,'getpwnam') as user, \
             patch.object(attest.subprocess,'run') as run:
            user.return_value.pw_gid=1000
            user.return_value.pw_uid=1000
            run.return_value.returncode=2
            run.return_value.stdout=json.dumps({'ok':False})
            with self.assertRaisesRegex(attest.Blocked,'LOGIN_OR_QUEUE_FAILED'):
                attest.login('postgresql://user:private-secret@host/db')

    def test_workflow_manual_probe_requires_owner_and_exact_main(self):
        text=Path('.github/workflows/oracle-light-active-hold-attest.yml').read_text()
        self.assertIn('needs: contract',text)
        self.assertIn("github.ref == 'refs/heads/main'",text)
        self.assertIn("github.event_name == 'push'",text)
        self.assertIn("github.event_name == 'workflow_dispatch'",text)
        self.assertIn('github.actor == github.repository_owner',text)
        self.assertIn('inputs.expected_main_sha == github.sha',text)
        self.assertNotIn('NEON_API_KEY',text)


if __name__=='__main__':
    unittest.main()
