import contextlib,datetime,hashlib,json,os,signal,subprocess,sys,time,unittest,uuid
from pathlib import Path
import types
from unittest.mock import patch
import yaml
from ops import ibm_machine_queue_protocol as p,ibm_machine_queue_source as source,ibm_machine_queue_runner as runner,ibm_machine_queue_db as db,ibm_machine_queue_oracle as oracle
from tests import ibm_machine_queue_fixture as f
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/"tests/ibm_machine_queue_fixture.py"
def fakeenv():
 return {"PATH":"/usr/bin:/bin","EXPECTED_MAIN":f.MAIN,"GITHUB_SHA":f.MAIN,"GITHUB_REPOSITORY":source.REPOSITORY,"GITHUB_REF":"refs/heads/main","GITHUB_EVENT_NAME":"workflow_dispatch","GITHUB_ACTOR":source.OWNER,"GITHUB_TRIGGERING_ACTOR":source.OWNER,"GH_TOKEN":f.SECRET}
class PipeTests(unittest.TestCase):
 def clients(self,dbmode="OK",apimode="OK",sshmode="OK"):
  return [p.PipeClient([sys.executable,"-B",str(FIXTURE),role,mode],fakeenv(),2) for role,mode in (("db",dbmode),("source",apimode),("ssh",sshmode))]
 def execute(self,dbmode="OK",apimode="OK",sshmode="OK",cap=2):
  clients=self.clients(dbmode,apimode,sshmode);q,s,relay=clients;start=time.monotonic()
  try:
   ready=q.recv(start+5);self.assertEqual(ready["kind"],"DB_READY")
   self.assertEqual(s.recv(start+5),{"kind":"SOURCE_READY"})
   self.assertEqual(relay.recv(start+5),{"kind":"CHANNEL_PREPARED_NO_IBM_CONNECTION"})
   frame=relay.recv(start+5);reviewed=runner.binding(frame,f.MAIN,ready)
   bind_nonce=uuid.uuid4().hex
   primary=s.call({"kind":"SOURCE_BIND","run":f.RUN,"nonce":bind_nonce},time.monotonic()+cap)
   self.assertEqual(primary["kind"],"PRIMARY_RESULT")
   p.accept_channel(relay,primary,reviewed,bind_nonce,time.monotonic()+100)
   result=p.Supervisor(q,s,reviewed,cap,used_nonces=(bind_nonce,)).session(relay,frame)
   self.assertNotIn(f.SECRET,json.dumps(result))
   return result,time.monotonic()-start
  finally:
   for c in reversed(clients):c.close()
   self.assertTrue(all(c.p.poll() is not None for c in clients))
 def refuse(self,**kwargs):
  start=time.monotonic()
  with self.assertRaises(p.Refused):self.execute(**kwargs)
  self.assertLess(time.monotonic()-start,10)
 def test_real_pipes_two_hops_both_phases_under_ten_seconds(self):
  result,elapsed=self.execute();self.assertEqual(result["proofs"],2);self.assertTrue(result["parent_stop_required"]);self.assertLess(elapsed,10)
 def test_deadline_blocked_db(self):self.refuse(dbmode="BLOCK",cap=.3)
 def test_deadline_blocked_api(self):self.refuse(apimode="BLOCK",cap=.3)
 def test_malformed(self):self.refuse(dbmode="MALFORMED")
 def test_oversize(self):self.refuse(dbmode="OVERSIZE")
 def test_active_jobs(self):self.refuse(dbmode="NONZERO")
 def test_boolean_count(self):self.refuse(dbmode="BOOL")
 def test_rls(self):self.refuse(dbmode="RLS")
 def test_stale_server_clock(self):self.refuse(dbmode="STALE")
 def test_wrong_nonce(self):self.refuse(dbmode="NONCE")
 def test_replay(self):self.refuse(sshmode="REPLAY")
 def test_bad_phase(self):self.refuse(sshmode="BAD_PHASE")
 def test_disconnect(self):self.refuse(sshmode="EOF")
 def test_source_drift(self):self.refuse(apimode="DRIFT")
 def test_actor(self):self.refuse(apimode="ACTOR")
 def test_run_scope(self):self.refuse(apimode="RUN")
 def test_rerun(self):self.refuse(apimode="RERUN")
 def test_wrong_event(self):self.refuse(apimode="EVENT")
 def test_wrong_power_phase(self):self.refuse(apimode="PHASE")
 def test_wrong_power_step(self):self.refuse(apimode="STEP")
 def test_incomplete_jobs(self):self.refuse(apimode="JOBS")
 def test_duplicate_job_names(self):self.refuse(apimode="DUP_JOB")
 def test_secret_db_failure_is_categorized(self):self.refuse(dbmode="SECRET")
 def test_secret_api_failure_is_categorized(self):self.refuse(apimode="API_SECRET")
 def test_initial_admission_wrong_nonce(self):self.refuse(apimode="BIND_NONCE")
 def test_initial_admission_stale(self):self.refuse(apimode="BIND_STALE")
 def test_initial_admission_future(self):self.refuse(apimode="BIND_FUTURE")
 def test_initial_admission_wrong_mode(self):self.refuse(apimode="BIND_MODE")
 def test_initial_admission_missing_timestamp(self):self.refuse(apimode="BIND_MISSING")
 def test_guest_result_then_nonzero(self):self.refuse(sshmode="GUEST_RESULT_NONZERO")
 def test_guest_result_then_hang(self):self.refuse(sshmode="GUEST_RESULT_HANG")
 def test_result_then_nonzero(self):self.refuse(sshmode="RESULT_THEN_NONZERO")
 def test_result_then_hang(self):self.refuse(sshmode="RESULT_THEN_HANG")
 def test_result_then_cleanup_failure(self):self.refuse(sshmode="RESULT_THEN_CLEANUP_FAILURE")
 def test_parent_death_kills_pipe_child(self):
  code="import sys,time;from ops.ibm_machine_queue_protocol import PipeClient;c=PipeClient([sys.executable,'-c','import time;time.sleep(30)'],{},2);print(c.p.pid,flush=True);time.sleep(30)"
  parent=subprocess.Popen([sys.executable,"-B","-c",code],cwd=ROOT,stdout=subprocess.PIPE)
  child=int(parent.stdout.readline());parent.kill();parent.wait(timeout=2);parent.stdout.close()
  end=time.monotonic()+2;dead=False
  while time.monotonic()<end:
   try:
    raw=Path("/proc/"+str(child)+"/stat").read_text();dead=raw.split(") ",1)[1].split()[0]=="Z"
   except (FileNotFoundError,ProcessLookupError):dead=True
   if dead:break
   time.sleep(.01)
  self.assertTrue(dead)
class Guards(unittest.TestCase):
 def test_public_mode_and_package_selectors(self):
  self.assertEqual(runner.validate_inputs(f.MAIN,"queue-qualify",""),(f.MAIN,"queue-qualify",""))
  self.assertEqual(runner.validate_inputs(f.MAIN,"bounded-relay","a"*64),(f.MAIN,"bounded-relay","a"*64))
  for main,mode,package in ((f.MAIN,"arbitrary",""),(f.MAIN,"bounded-relay",""),(f.MAIN,"bounded-relay","A"*64),(f.MAIN,"bounded-relay","a"*64+";x"),(f.MAIN,"queue-qualify","a"*64),("bad","queue-qualify","")):
   with self.subTest(mode=mode,package=package),self.assertRaises(p.Refused):runner.validate_inputs(main,mode,package)
 def test_bad_selectors_never_reach_secret_checkout_or_db(self):
  for mode,package in (("arbitrary",""),("bounded-relay",""),("bounded-relay","A"*64),("queue-qualify","a"*64)):
   accessed=[]
   class Environment(dict):
    def get(self,key,default=None):
     if key in ("NATIVE_OWNER_DATABASE_URL","SSH_PRIVATE_KEY","GH_TOKEN"):accessed.append(key)
     return super().get(key,default)
    def items(self):
     accessed.append("credential-environment-enumeration");return super().items()
   env=Environment(fakeenv());env.update(QUEUE_MODE=mode,EXPECTED_PACKAGE_SHA=package,NATIVE_OWNER_DATABASE_URL="synthetic-private-db",SSH_PRIVATE_KEY="synthetic-private-key")
   import io
   output=io.StringIO()
   with patch.object(runner.os,"environ",env),patch.object(runner,"HardCap",return_value=types.SimpleNamespace(close=lambda:None)),patch.object(runner,"PipeClient") as child,patch.object(runner,"main_source") as api,patch.object(runner.subprocess,"check_output") as checkout,contextlib.redirect_stdout(output):
    self.assertEqual(runner.entry(),2)
   self.assertEqual(accessed,[]);child.assert_not_called();api.assert_not_called();checkout.assert_not_called()
   self.assertNotIn("synthetic-private",output.getvalue())
 def test_fixed_sql_digest(self):self.assertEqual(hashlib.sha256(p.SQL.encode()).hexdigest(),p.SQL_SHA)
 def test_duplicate_json_rejected(self):
  with self.assertRaises(p.Refused):p.parse(b'{"kind":"x","kind":"y"}')
 def test_no_sql_or_url_in_request(self):
  class NoCall:
   def call(self,*a):raise AssertionError("adapter called")
  r=f.request("PRE_STOP");r["sql"]="DELETE anything"
  with self.assertRaises(p.Refused):p.Supervisor(NoCall(),NoCall(),f.reviewed()).prove(r)
 def test_source_context_rejects_pr(self):
  env=fakeenv();env["GITHUB_EVENT_NAME"]="pull_request"
  with patch.dict(os.environ,env,clear=True),self.assertRaises(p.Refused):source.context(f.MAIN)
 def test_source_no_actions_token(self):
  calls=[];fake=f.fake_get("OK")
  def get(path,token=None):calls.append((path,token));return fake(path,token)
  with patch.dict(os.environ,fakeenv(),clear=True),patch.object(source,"get",side_effect=get):source.primary(f.MAIN,f.RUN,"a"*32)
  self.assertTrue(all(token is None for path,token in calls if path.startswith("/actions/")))
 def test_actual_query_readonly_identity_and_global_queue(self):
  conn=f.FakeConnection();row=db.query(conn);self.assertEqual(row["lab_nonterminal"],0)
  self.assertIn("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY",conn.commands)
  self.assertIn("SELECT current_database(),session_user,current_user",conn.commands)
  self.assertTrue(any("FROM pg_catalog.pg_settings" in x for x in conn.commands))
  self.assertEqual(conn.commands[-1],"TRANSACTION_END");self.assertIn(p.SQL,conn.commands)
  lock="LOCK TABLE ONLY assistant_lab.control_command, ONLY assistant_lab.job IN ACCESS SHARE MODE"
  self.assertLess(conn.commands.index(lock),next(i for i,x in enumerate(conn.commands) if "FROM pg_catalog.pg_class" in x))
  self.assertLess(conn.commands.index(lock),conn.commands.index(p.SQL))
  self.assertFalse(any(x.startswith(("GRANT","REVOKE","UPDATE","DELETE","INSERT","CREATE")) for x in conn.commands))
 def test_queue_catalog_view_refused(self):
  with self.assertRaises(p.Refused):db.query(f.FakeConnection("CAT_VIEW"))
 def test_queue_catalog_rls_refused(self):
  with self.assertRaises(p.Refused):db.query(f.FakeConnection("CAT_RLS"))
 def test_login_role_mismatch(self):
  with self.assertRaises(Exception):db.query(f.FakeConnection("ROLE"))
 def test_guc_not_provider_config(self):
  with self.assertRaises(Exception):db.query(f.FakeConnection("GUC_SOURCE"))
 def test_guc_missing(self):
  with self.assertRaises(Exception):db.query(f.FakeConnection("GUC_MISSING"))
 def test_readwrite_connection_refused(self):
  conn=f.FakeConnection();conn.read_only=False
  with self.assertRaises(p.Refused):db.query(conn)
 def test_readwrite_transaction_refused(self):
  with self.assertRaises(p.Refused):db.query(f.FakeConnection("READWRITE"))
 def test_child_environment_has_no_ssh_or_cross_role_secret(self):
  with patch.dict(os.environ,{**fakeenv(),"SSH_PRIVATE_KEY":f.SECRET,"NATIVE_OWNER_DATABASE_URL":f.SECRET},clear=True):
   self.assertNotIn("SSH_PRIVATE_KEY",runner.child_env("db"));self.assertNotIn("GH_TOKEN",runner.child_env("db"));self.assertNotIn("NATIVE_OWNER_DATABASE_URL",runner.child_env("source"))
 def test_fixed_remote_command_no_forwarded_credential(self):
  args=runner.ssh_argv("/synthetic/key","/synthetic/known",f.MAIN,"a"*64)
  self.assertIn("ForwardAgent=no",args);self.assertIn("ClearAllForwardings=yes",args);self.assertIn("StrictHostKeyChecking=yes",args)
  remote=__import__("shlex").split(args[-1]);self.assertEqual(remote[:5],["/usr/bin/python3","-I","-B","-u","-c"]);self.assertNotIn(f.SECRET,args[-1])
 def test_bootstrap_source_hashes_and_fixed_adapter(self):
  code=runner.bootstrap(f.MAIN,"a"*64);compile(code,"bootstrap","exec")
  self.assertIn("assert hashlib.sha256",code);self.assertIn("ibm_machine_queue_oracle",code)
 def test_existing_route_source_anchor(self):
  self.assertEqual(hashlib.sha256((ROOT/runner.ROUTE_WORKFLOW).read_bytes()).hexdigest(),runner.ROUTE_SHA);host,fp=runner.route();self.assertEqual("ubuntu@"+host,runner.HOST);self.assertTrue(fp.startswith("SHA256:"))
 def test_binding_main_drift(self):
  frame={"kind":"CHANNEL_BINDING","reviewed":f.reviewed(),"source_pins":f.PINS};frame["reviewed"]["main_sha"]="2"*40
  with self.assertRaises(p.Refused):runner.binding(frame,f.MAIN,f.reviewed())
 def test_binding_guest_scope(self):
  frame={"kind":"CHANNEL_BINDING","reviewed":f.reviewed(),"source_pins":{"arbitrary.py":"a"*64}}
  with self.assertRaises(p.Refused):runner.binding(frame,f.MAIN,f.reviewed())
 def test_oracle_path_not_caller_input(self):
  import inspect
  text=inspect.getsource(oracle);self.assertNotIn("ssh_argv(",text);self.assertNotIn("SSH_PRIVATE_KEY",text)
  self.assertEqual(oracle.FILES,{"relay.py","bundle.json"})
 def test_no_private_host_address_literals_in_new_source(self):
  import re
  for path in (ROOT/"ops").glob("ibm_machine_queue_*.py"):
   self.assertFalse(re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b",path.read_text()),path.name)
class WorkflowContract(unittest.TestCase):
 def test_secret_only_final_guarded_owner_step(self):
  value=yaml.safe_load((ROOT/".github/workflows/ibm-machine-queue-proof.yml").read_text())
  self.assertEqual(value["permissions"],{"contents":"read"})
  owner=value["jobs"]["owner"];guard=owner["if"]
  for term in ("refs/heads/main","github.repository_owner","github.triggering_actor","workflow_dispatch","inputs.expected_main_sha == github.sha"):self.assertIn(term,guard)
  self.assertEqual(owner["environment"],"database-production")
  steps=owner["steps"]
  for step in steps[:-1]:self.assertNotIn("secrets.",json.dumps(step))
  env=steps[-1]["env"];self.assertEqual(env["NATIVE_OWNER_DATABASE_URL"],"${{ secrets.LIGHT_MAINTENANCE_DATABASE_URL }}");self.assertIn("inputs.mode == 'bounded-relay'",env["SSH_PRIVATE_KEY"])
  before=steps[-2];self.assertIn("validate_inputs",before["run"]);self.assertEqual(set(before["env"]),{"EXPECTED_MAIN","QUEUE_MODE","EXPECTED_PACKAGE_SHA"})
  self.assertNotIn("IBM_CLOUD_API_KEY",json.dumps(value));self.assertNotIn("actions: write",json.dumps(value))
 def test_existing_canon_maintenance_workflow_unchanged(self):
  expected={".github/workflows/native-maintenance-owner-attest.yml":"c6fd46d973bbbf08d7fe63ae40c5bf8cf63af347ef50c3eb466108e4fd1cfc92",".github/workflows/native-maintenance-owner-host.yml":runner.ROUTE_SHA}
  for path,digest in expected.items():self.assertEqual(hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),digest)
 def test_independent_stop_lane_not_replaced(self):
  value=yaml.safe_load((ROOT/".github/workflows/ibm-machine-queue-proof.yml").read_text());text=json.dumps(value)
  self.assertNotIn("ibm-vpc-independent-stop",text);self.assertFalse(value["concurrency"]["cancel-in-progress"])

class BeforeConnection(unittest.TestCase):
 def primary(self):
  return {"kind":"PRIMARY_RESULT","nonce":"a"*32,"main_sha":f.MAIN,"power_sha":source.POWER_SHA,"run":f.RUN,"run_status":"in_progress","mode":"manual_console_trial","trial_start":"skipped","manual_console_trial":"in_progress","verified_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}
 def refuse_without_accept(self,change,seconds=100):
  value=self.primary();change(value);sent=[]
  relay=types.SimpleNamespace(send=lambda v,end:sent.append(v))
  with self.assertRaises(p.Refused):p.accept_channel(relay,value,f.reviewed(),"a"*32,time.monotonic()+seconds)
  self.assertEqual(sent,[])
 def test_wrong_nonce_no_accept(self):self.refuse_without_accept(lambda v:v.update(nonce="b"*32))
 def test_missing_field_no_accept(self):self.refuse_without_accept(lambda v:v.pop("mode"))
 def test_stale_no_accept(self):self.refuse_without_accept(lambda v:v.update(verified_at="2000-01-01T00:00:00Z"))
 def test_future_no_accept(self):self.refuse_without_accept(lambda v:v.update(verified_at="2100-01-01T00:00:00Z"))
 def test_wrong_mode_no_accept(self):self.refuse_without_accept(lambda v:v.update(mode="trial_start"))
 def test_insufficient_reserve_no_accept(self):self.refuse_without_accept(lambda v:None,69)
 def test_boundary_reserve_is_not_expanded(self):
  with self.assertRaises(p.Refused):p.remaining_budget(100,31)
  p.remaining_budget(100,30)
 def test_full_private_validator_called_before_connection(self):
  import inspect
  text=inspect.getsource(oracle.entry)
  self.assertLess(text.index("validate_guest_review("),text.index("child=PipeClient("))
  self.assertLess(text.index("remaining_budget(deadline_total)"),text.index("child=PipeClient("))
 def test_cached_guest_sources_hash_before_compile(self):
  import inspect
  text=inspect.getsource(oracle.validate_guest_review)
  self.assertLess(text.index("hashlib.sha256(raw)"),text.index("exec(compile(raw"))
  self.assertIn("validate_review(policy,authorization",text)
  self.assertIn("APPROVAL_OPERATION_SCOPE",text)


@unittest.skipUnless(os.environ.get("MACHINE_QUEUE_SYNTHETIC_POSTGRES")=="1","disposable CI PostgreSQL only")
class ConcurrentRelationTests(unittest.TestCase):
 def connect(self):
  import psycopg
  # Fixed disposable loopback service; never use an owner credential/DSN.
  return psycopg.connect(host="127.0.0.1",port=5432,dbname="neondb",user="postgres",password="postgres",connect_timeout=2,sslmode="disable",gssencmode="disable",autocommit=True)
 def setUp(self):
  self.assertEqual(os.environ.get("GITHUB_ACTIONS"),"true")
  with self.connect() as c:
   c.execute("CREATE SCHEMA assistant_lab")
   c.execute("CREATE TABLE assistant_lab.job(status text)")
   c.execute("CREATE TABLE assistant_lab.control_command(status text)")
 def tearDown(self):
  with self.connect() as c:c.execute("DROP SCHEMA assistant_lab CASCADE")
 def race(self,ddl,writer=False):
  import threading
  from database import native_cli_permission_engine as engine
  start=threading.Event();done=threading.Event();codes=[]
  def concurrent():
   with self.connect() as c:
    c.execute("SET lock_timeout='150ms'")
    if not start.wait(2):codes.append("NO_CHALLENGE");done.set();return
    try:c.execute(ddl);codes.append("OK")
    except Exception as e:codes.append(getattr(e,"sqlstate",None))
    finally:done.set()
  thread=threading.Thread(target=concurrent);thread.start()
  with self.connect() as c:
   c.read_only=True
   class LockedObserver:
    read_only=True
    def transaction(self):return c.transaction()
    def execute(self,sql):
     if sql==p.SQL:
      start.set()
      if not done.wait(2):raise AssertionError("DDL_ATTEMPT_TIMEOUT")
     return c.execute(sql)
   try:
    with patch.object(engine,"identity"):
     if writer:
      with self.assertRaises(p.Refused):db.query(LockedObserver())
     else:self.assertEqual(db.query(LockedObserver())["lab_nonterminal"],0)
   finally:start.set();thread.join(timeout=3)
  self.assertFalse(thread.is_alive())
  self.assertEqual(codes,["OK" if writer else "55P03"])
 def test_drop_then_view_replacement_is_blocked_until_counts(self):
  self.race("DROP TABLE assistant_lab.job; CREATE VIEW assistant_lab.job AS SELECT 'COMPLETED'::text AS status")
 def test_enable_rls_is_blocked_until_counts(self):
  self.race("ALTER TABLE assistant_lab.job ENABLE ROW LEVEL SECURITY")
 def test_access_share_allows_enqueue_and_count_refuses(self):
  self.race("INSERT INTO assistant_lab.job(status) VALUES ('RUNNING')",writer=True)

if __name__=="__main__":unittest.main()
