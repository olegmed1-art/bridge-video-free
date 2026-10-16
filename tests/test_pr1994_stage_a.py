"""Synthetic TLS PostgreSQL tests. No production credential/endpoint or payload."""
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
import psycopg
from database import pr1994_stage_a as stage
from tests.pr1994_stage_a_fixture import SyntheticFixture

ROOT=Path(__file__).resolve().parents[1]
RAW=(ROOT/'database/scripts/reconcile_pr1994_audit.sql').read_bytes()
def fake(state=psycopg.pq.TransactionStatus.IDLE):
    info=SimpleNamespace(status=psycopg.pq.ConnStatus.OK,transaction_status=state,
        pipeline_status=psycopg.pq.PipelineStatus.OFF,host='fixture',port=5432,
        dbname='fixture',user='fixture',
        get_parameters=lambda:{'sslmode':'verify-full','channel_binding':'require'})
    return SimpleNamespace(info=info,closed=False,autocommit=True,
                           pgconn=SimpleNamespace(ssl_in_use=True))
B=stage.Binding('fixture','fixture','fixture','fixture')
class ClientTests(unittest.TestCase):
    def test_nonidle_preserved_without_sql_or_close(self):
        for state in (psycopg.pq.TransactionStatus.ACTIVE,psycopg.pq.TransactionStatus.INTRANS,
                      psycopg.pq.TransactionStatus.INERROR,psycopg.pq.TransactionStatus.UNKNOWN):
            c=fake(state); calls=[]
            c.execute=lambda *a:calls.append(a); c.close=lambda:calls.append('close')
            with self.assertRaises(stage.Refused): stage.collect(c,psycopg.pq,B,RAW)
            self.assertEqual(calls,[])
    def test_bad_source_before_client_or_sql(self):
        c=fake(); calls=[]; c.execute=lambda *a:calls.append(a); c.close=lambda:calls.append('close')
        with self.assertRaises(stage.Refused): stage.collect(c,psycopg.pq,B,RAW+b' ')
        self.assertEqual(calls,[])
    def test_client_contracts(self):
        for attr,value in (('closed',True),('autocommit',False)):
            c=fake(); setattr(c,attr,value)
            with self.assertRaises(stage.Refused): stage.client_guard(c,psycopg.pq,B)
        for attr,value in (('host','wrong'),('port',1),('dbname','wrong'),('user','wrong'),
                           ('pipeline_status',psycopg.pq.PipelineStatus.ON),
                           ('status',psycopg.pq.ConnStatus.BAD)):
            c=fake(); setattr(c.info,attr,value)
            with self.assertRaises(stage.Refused): stage.client_guard(c,psycopg.pq,B)
        c=fake(); c.pgconn.ssl_in_use=False
        with self.assertRaises(stage.Refused): stage.client_guard(c,psycopg.pq,B)
        for policy in ({},{'sslmode':'require','channel_binding':'prefer'}):
            c=fake(); c.info.get_parameters=lambda:policy
            with self.assertRaises(stage.Refused): stage.client_guard(c,psycopg.pq,B)
    def test_only_pinned_catalog(self):
        q=stage.catalog_query(RAW)
        self.assertTrue(q.startswith('SELECT encode(sha256'))
        self.assertNotIn('bridge.pr1994.work_item_id',q)

class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=SyntheticFixture()
        try:
            cls.kwargs=cls.fixture.start()
            cls.fixture.wait_ready(psycopg.connect,psycopg.OperationalError,cls.kwargs)
            spec=importlib.util.spec_from_file_location('closure_fixture',
                ROOT/'database/tests/pr1994-audit-closure/run.py')
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            receipt=(ROOT/'database/migrations/0351_autopilot_paused_evidence_reconcile.sql').read_text()
            receipt=re.search(r'CREATE TABLE autopilot\.paused_work_reconcile_receipt[\s\S]*?\n\);',receipt).group()
            functions='\n'.join(module.public_function(p,n) for p,n in (
                ('database/migrations/0323_autopilot_failure_continuation.sql','role_blocker_requires_owner'),
                ('database/migrations/0324a_autopilot_dynamic_role_registry.sql','role_is_enabled'),
                ('database/migrations/0324a_autopilot_dynamic_role_registry.sql','enforce_enabled_role'),
                ('database/migrations/0325_autopilot_durable_dependency_wakeup.sql','set_project_work_dependency_state'),
                ('database/migrations/0325_autopilot_durable_dependency_wakeup.sql','release_project_work_dependents')))
            cls.schema=module.SCHEMA+receipt+functions+"""
CREATE TRIGGER project_work_item_enabled_role BEFORE INSERT OR UPDATE OF role
ON autopilot.project_work_item FOR EACH ROW EXECUTE FUNCTION autopilot.enforce_enabled_role();
CREATE TRIGGER autopilot_project_work_dependency_state BEFORE INSERT OR UPDATE OF depends_on_work_item_id
ON autopilot.project_work_item FOR EACH ROW EXECUTE FUNCTION autopilot.set_project_work_dependency_state();
CREATE TRIGGER autopilot_project_work_dependency_release AFTER UPDATE OF state
ON autopilot.project_work_item FOR EACH ROW EXECUTE FUNCTION autopilot.release_project_work_dependents();
INSERT INTO autopilot.project_work_item(work_item_id,work_key,state,objective)
VALUES('00000000-0000-4000-8000-000000000099','fixture-row','BLOCKED','ROW_PAYLOAD_DO_NOT_EXPORT');
"""
            cls.binding=stage.Binding('localhost','postgres','fixture-branch','postgres')
        except BaseException:
            cls.fixture.diagnostics()
            try:
                cls.fixture.cleanup()
            except Exception:
                print('{"fixture_cleanup_failed":true}')
            raise
    @classmethod
    def tearDownClass(cls): cls.fixture.cleanup()
    def setUp(self):
        with psycopg.connect(**self.kwargs,autocommit=True) as c: c.execute(self.schema)
    def connection(self): return psycopg.connect(**self.kwargs,autocommit=True)
    def sql(self,q,params=None):
        with self.connection() as c:
            cursor=c.execute(q,params)
            return cursor.fetchall() if cursor.description else []
    def snapshot(self):
        return self.sql("SELECT objective,state,updated_at FROM autopilot.project_work_item ORDER BY work_item_id")
    def test_real_tls_same_connection_catalog_and_rollback(self):
        before=self.snapshot(); c=self.connection()
        result=stage.collect(c,psycopg.pq,self.binding,RAW)
        self.assertTrue(c.closed); self.assertEqual(before,self.snapshot())
        self.assertEqual(len(result['relations']),9)
        self.assertEqual(len(result['functions']),5)
        self.assertNotIn('ROW_PAYLOAD_DO_NOT_EXPORT',json.dumps(result))
        self.assertEqual(result['catalog_sha256'],self.sql(stage.catalog_query(RAW))[0][0])
        self.assertTrue(all(f['definition'] is None for f in result['functions'][3:]))
    def test_missing_dependency_bodies_once(self):
        b=stage.Binding('localhost','postgres','fixture-branch','postgres',True,True)
        result=stage.collect(self.connection(),psycopg.pq,b,RAW)
        self.assertTrue(all(type(f['definition']) is str for f in result['functions']))
        self.assertEqual(len({f['name'] for f in result['functions']}),5)
    def test_wrong_branch_parameterized_refusal_and_cleanup(self):
        c=self.connection()
        b=stage.Binding('localhost','postgres',"fixture-branch'; DROP SCHEMA autopilot CASCADE;--",'postgres')
        before=self.snapshot()
        with self.assertRaises(stage.Refused): stage.collect(c,psycopg.pq,b,RAW)
        self.assertTrue(c.closed); self.assertEqual(before,self.snapshot())
    def test_replaces_rr_default_before_any_snapshot(self):
        c=self.connection()
        c.execute("SET default_transaction_isolation='repeatable read'")
        result=stage.collect(c,psycopg.pq,self.binding,RAW)
        self.assertEqual(result['stage'],'A')
    def test_existing_transaction_unchanged(self):
        c=self.connection(); c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
        c.execute("SELECT 1")
        with self.assertRaises(stage.Refused): stage.collect(c,psycopg.pq,self.binding,RAW)
        self.assertFalse(c.closed)
        self.assertEqual(c.info.transaction_status,psycopg.pq.TransactionStatus.INTRANS)
        c.execute("ROLLBACK"); c.close()
    def test_missing_relation_rolls_back(self):
        self.sql("DROP TABLE autopilot.project_planner_state")
        c=self.connection()
        with self.assertRaises(stage.Refused): stage.collect(c,psycopg.pq,self.binding,RAW)
        self.assertTrue(c.closed)
    def test_application_classifier_is_not_invoked(self):
        self.sql("""CREATE OR REPLACE FUNCTION autopilot.role_blocker_requires_owner(p_result_code text)
RETURNS boolean LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'CLASSIFIER_MUST_NOT_RUN'; END $$""")
        result=stage.collect(self.connection(),psycopg.pq,self.binding,RAW)
        self.assertIn('CLASSIFIER_MUST_NOT_RUN',result['functions'][0]['definition'])
    def test_ddl_between_definition_and_digest_refuses(self):
        real=self.connection()
        outer=self
        class Proxy:
            def __getattr__(self,name): return getattr(real,name)
            def execute(self,q,params=None):
                cursor=real.execute(q,params)
                if q==stage.FUNCTION_QUERY and params[1]==stage.FUNCTIONS[0]:
                    outer.sql("""CREATE OR REPLACE FUNCTION autopilot.role_blocker_requires_owner(p_result_code text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT true $$""")
                return cursor
        with self.assertRaises(stage.Refused):
            stage.collect(Proxy(),psycopg.pq,self.binding,RAW)
        self.assertTrue(real.closed)
    def test_close_failure_does_not_egress_exception(self):
        real=self.connection()
        class Proxy:
            def __getattr__(self,name): return getattr(real,name)
            def close(self):
                real.close()
                raise RuntimeError('PRIVATE_CLOSE_ERROR_DO_NOT_EXPORT')
        with self.assertRaisesRegex(stage.Refused,'^STAGE_A_REFUSED$'):
            stage.collect(Proxy(),psycopg.pq,self.binding,RAW)
        self.assertTrue(real.closed)
    def test_cleanup_deadline_expiry_refuses(self):
        from unittest.mock import patch
        real=self.connection(); clock=[0]
        class Proxy:
            def __getattr__(self,name): return getattr(real,name)
            def close(self): real.close(); clock[0]=46
        with patch.object(stage.time,'monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(stage.Refused):
                stage.collect(Proxy(),psycopg.pq,self.binding,RAW)
        self.assertTrue(real.closed)
    def test_output_cap_rolls_back_and_closes(self):
        self.sql("CREATE OR REPLACE FUNCTION autopilot.role_blocker_requires_owner(p_result_code text) "
                 "RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT false /*" + "X"*262144 + "*/ $$")
        c=self.connection()
        with self.assertRaises(stage.Refused): stage.collect(c,psycopg.pq,self.binding,RAW)
        self.assertTrue(c.closed)
    def test_overall_deadline_refusal(self):
        c=self.connection()
        with self.assertRaises(stage.Refused): stage.collect(c,psycopg.pq,self.binding,RAW,seconds=0)
        self.assertFalse(c.closed); c.close()

if __name__=='__main__': unittest.main(verbosity=2)
