"""Synthetic-only subprocess fixture. No real DB, API, SSH or repair calls."""
import datetime,json,os,sys,time,types,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ops import ibm_machine_queue_protocol as p,ibm_machine_queue_source as source,ibm_machine_queue_db as db
from ops.native_permission_hold_guard import EXPECTED_TARGET as TARGET
SECRET="SYNTHETIC_CONTEXT_SECRET_NEVER_EXPORT"
MAIN="1"*40;RUN="999999";HOST="synthetic-compute";BOOT="00000000-0000-0000-0000-000000000001"
PINS={k:"a"*64 for k in ("repair_gate.py","fs_transaction.py","host_adapter.py","executor.py","inspector.py")}
def reviewed():
 return {"host":HOST,"boot":BOOT,"run":RUN,"main_sha":MAIN,"power_sha":source.POWER_SHA,"project":TARGET["neon"]["project_id"],"branch":TARGET["neon"]["branch_id"],"database":TARGET["database"]}
def send(v):sys.stdout.buffer.write(p.canonical(v));sys.stdout.buffer.flush()
def recv():
 raw=sys.stdin.buffer.readline(65537);p.need(raw.endswith(b"\n") and len(raw)<=65536,"FIXTURE_FRAME");return p.parse(raw)
def request(phase,nonce=None):
 r=reviewed();return {"kind":"QUEUE_REQUEST","phase":phase,"nonce":nonce or uuid.uuid4().hex,**{k:r[k] for k in ("host","boot","run","project","branch","database")},"requested_mono":time.monotonic(),"sql_sha256":p.SQL_SHA}
class Result:
 def __init__(self,row=None,rows=None,description=None):self.row=row;self.rows=rows;self.description=description
 def fetchone(self):return self.row
 def fetchall(self):return self.rows
class Transaction:
 def __init__(self,conn):self.conn=conn
 def __enter__(self):self.conn.commands.append("TRANSACTION");return self
 def __exit__(self,*args):self.conn.commands.append("TRANSACTION_END")
class FakeConnection:
 def __init__(self,mode="OK"):
  self.mode=mode;self.commands=[];self.read_only=True
  self.info=types.SimpleNamespace(host=TARGET["neon"]["host"],port=5432,hostaddr="synthetic-address")
  vals={"host":TARGET["neon"]["host"],"hostaddr":"synthetic-address","options":"","sslmode":"verify-full","gssencmode":"disable"}
  self.pgconn=types.SimpleNamespace(info=[types.SimpleNamespace(keyword=k.encode(),val=v.encode()) for k,v in vals.items()])
 def transaction(self):return Transaction(self)
 def execute(self,sql,args=None):
  self.commands.append(sql)
  if sql==db.FENCE_SQL:
   lsn="0/"+format(0x1000000*(1+self.commands.count(p.SQL) if self.mode=="WAL_CHANGED" else 1),"X")
   return Result((False,lsn,1001,1002,1000))
  if sql=="SELECT current_setting('transaction_read_only')":return Result(("off" if self.mode=="READWRITE" else "on",))
  if sql=="SELECT current_database(),session_user,current_user":return Result((TARGET["database"],TARGET["session_owner"],"other" if self.mode=="ROLE" else TARGET["owner"]))
  if "FROM pg_catalog.pg_settings" in sql:
   n=TARGET["neon"];rows=[("neon.project_id",n["project_id"],"postmaster","configuration file",n["project_id"],False),("neon.branch_id",n["branch_id"],"postmaster","configuration file",n["branch_id"],False),("neon.endpoint_id",n["endpoint_id"],"superuser","configuration file",n["endpoint_id"],False)]
   if self.mode=="GUC_SOURCE":rows[2]=(*rows[2][:3],"session",*rows[2][4:])
   if self.mode=="GUC_MISSING":rows.pop()
   return Result(rows=rows)
  if sql=="SELECT 'assistant_lab.control_command'::regclass::oid,'assistant_lab.job'::regclass::oid":return Result((1001,1002))
  if "FROM pg_catalog.pg_class" in sql:
   rows=[(1001,"control_command","r",False,False,"heap",False),(1002,"job","r",self.mode=="CAT_RLS",False,"heap",False)]
   if self.mode=="CAT_VIEW":rows[1]=(1002,"job","v",False,False,None,False)
   return Result(rows=rows)
  if sql==p.SQL:
   names=["observed_at","database_name","lab_nonterminal","control_nonterminal","null_status_count","job_rls_off","control_rls_off"]
   count=1 if self.mode=="NONZERO" else False if self.mode=="BOOL" else 0
   stamp=datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=20 if self.mode=="STALE" else 0)
   return Result((stamp,TARGET["database"],count,0,0,self.mode!="RLS",True),description=[types.SimpleNamespace(name=k) for k in names])
  return Result()
def api_values(mode="OK"):
 main={"ref":"refs/heads/main","object":{"type":"commit","sha":"2"*40 if mode=="DRIFT" else MAIN}}
 power={"ref":"refs/heads/"+source.POWER_BRANCH,"object":{"type":"commit","sha":source.POWER_SHA}}
 run={"id":int(RUN),"workflow_id":source.POWER_WORKFLOW,"head_sha":source.POWER_SHA,"head_branch":source.POWER_BRANCH,"status":"in_progress","event":"workflow_dispatch","run_attempt":1,"repository":{"full_name":source.REPOSITORY},"actor":{"login":source.OWNER},"triggering_actor":{"login":source.OWNER}}
 if mode=="ACTOR":run["triggering_actor"]["login"]="other"
 if mode=="RUN":run["workflow_id"]=0
 if mode=="RERUN":run["run_attempt"]=2
 if mode=="EVENT":run["event"]="push"
 jobs={"total_count":2,"jobs":[{"name":"trial-start","status":"completed","conclusion":"skipped"},{"name":"manual-console-trial","status":"in_progress","conclusion":None,"steps":[{"name":"Owner manual serial console window with bounded ordinary Stop","status":"in_progress"}]}]}
 if mode=="PHASE":jobs["jobs"][0]["conclusion"]="success"
 if mode=="STEP":jobs["jobs"][1]["steps"][0]["status"]="completed"
 if mode=="JOBS":jobs["total_count"]=3
 if mode=="DUP_JOB":jobs["jobs"][1]["name"]="trial-start"
 return main,power,run,jobs
def fake_get(mode):
 values=api_values(mode)
 def get(path,token=None):
  if mode=="API_SECRET":raise RuntimeError(SECRET)
  if mode=="BLOCK":time.sleep(20)
  if path=="/git/ref/heads/main":return values[0]
  if path=="/git/ref/heads/"+source.POWER_BRANCH:return values[1]
  p.need(token is None,"NO_ACTIONS_TOKEN_SCOPE")
  return values[3] if "/jobs?" in path else values[2]
 return get
def main(role,mode):
 if role=="db":
  conn=FakeConnection();db.query(conn);send({"kind":"DB_READY",**{k:reviewed()[k] for k in ("project","branch","database")},"role":TARGET["session_owner"]})
  while True:
   r=recv()
   if mode=="BLOCK":time.sleep(20)
   if mode=="MALFORMED":sys.stdout.write("{\n");sys.stdout.flush();continue
   if mode=="OVERSIZE":sys.stdout.write("x"*65537+"\n");sys.stdout.flush();continue
   if mode=="SECRET":print(SECRET,file=sys.stderr);raise RuntimeError(SECRET)
   conn.mode=mode;row=db.query(conn);row.update(kind="FIXED_QUERY_RESULT",nonce="0"*32 if mode=="NONCE" else r["nonce"]);send(row)
 elif role=="source":
  source.get=fake_get(mode);send({"kind":"SOURCE_READY"})
  while True:
   r=recv();result=source.primary(MAIN,r["run"],r["nonce"])
   if r["kind"]=="SOURCE_BIND":
    if mode=="BIND_NONCE":result["nonce"]="0"*32
    if mode=="BIND_STALE":result["verified_at"]=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=20)).isoformat()
    if mode=="BIND_FUTURE":result["verified_at"]=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=20)).isoformat()
    if mode=="BIND_MODE":result["mode"]="trial_start"
    if mode=="BIND_MISSING":result.pop("verified_at")
   send(result)
 elif role=="guest":
  send({"kind":"EXECUTOR_READY","host":HOST,"boot":BOOT,"source_pins":PINS,"wall":time.time(),"mono":time.monotonic(),"deadline_seconds":55,"no_mutation_yet":True})
  first=None
  for phase in ("PRE_STOP","POST_STOP"):
   if mode=="EOF" and phase=="POST_STOP":return
   r=request("POST_STOP" if mode=="BAD_PHASE" else phase,first if mode=="REPLAY" else None);first=r["nonce"]
   send(r);proof=recv();p.need(proof.get("kind")=="QUEUE_PROOF" and proof.get("nonce")==r["nonce"],"FIXTURE_PROOF")
  send({"kind":"REPAIR_RESULT","state":"APPLIED_QUIESCENT_NOT_STARTED","automatic_starts":0,"stop_now_required":True})
  if mode=="GUEST_RESULT_NONZERO":os._exit(7)
  if mode=="GUEST_RESULT_HANG":time.sleep(20)
 elif role=="ssh":
  # Mock SSH still introduces a real second-hop process and real duplex pipes.
  send({"kind":"CHANNEL_PREPARED_NO_IBM_CONNECTION"});send({"kind":"CHANNEL_BINDING","reviewed":reviewed(),"source_pins":PINS})
  p.need(recv()=={"kind":"CHANNEL_ACCEPT"},"FIXTURE_ADMISSION")
  child=p.PipeClient([sys.executable,"-B",__file__,"guest",mode],dict(os.environ),2)
  try:
   send(child.recv(time.monotonic()+5))
   for _ in range(2):
    r=child.recv(time.monotonic()+5);send(r);child.send(recv(),time.monotonic()+2)
   result=child.recv(time.monotonic()+5);child.finish(time.monotonic()+.5);child.close();send(result)
   if mode=="RESULT_THEN_NONZERO":os._exit(7)
   if mode=="RESULT_THEN_HANG":time.sleep(20)
   if mode=="RESULT_THEN_CLEANUP_FAILURE":raise RuntimeError("FIXTURE_CLEANUP_FAILURE")
  finally:child.close()
 else:raise RuntimeError("FIXTURE_ROLE")
if __name__=="__main__":
 try:main(*sys.argv[1:])
 except BaseException:send({"kind":"ADAPTER_ERROR"});sys.exit(2)

# Exact query function from prior reviewed head51fdca29; test-only historical reproducer.
# SHA256 e84608d6f6a7cf80ae696ec798c5c4e48f3ff31fbf2746eaaaafef4ddf0638cd.
SCHEMA_RENAME_BASELINE_QUERY="def query(conn):\n from database.native_cli_permission_engine import identity\n from ops.native_permission_hold_guard import EXPECTED_TARGET\n from database.native_cli_permission_engine import Target,NeonBinding\n target=Target(**{**EXPECTED_TARGET,\"neon\":NeonBinding(**EXPECTED_TARGET[\"neon\"])})\n need(conn.read_only is True,\"READONLY_CONFIGURATION\")\n with conn.transaction():\n  conn.execute(\"SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY\")\n  conn.execute(\"SET LOCAL statement_timeout='2s'\")\n  conn.execute(\"SET LOCAL lock_timeout='1s'\")\n  conn.execute(\"SET LOCAL search_path='pg_catalog'\")\n  need(conn.execute(\"SELECT current_setting('transaction_read_only')\").fetchone()==(\"on\",),\"READONLY_TRANSACTION\")\n  identity(conn,target)\n  # ACCESS SHARE permits writers, but pins both named relations through counts.\n  conn.execute(\"LOCK TABLE ONLY assistant_lab.control_command, ONLY assistant_lab.job IN ACCESS SHARE MODE\")\n  locked=conn.execute(\"SELECT 'assistant_lab.control_command'::regclass::oid,'assistant_lab.job'::regclass::oid\").fetchone()\n  need(type(locked) is tuple and len(locked)==2 and all(type(x) is int and x>0 for x in locked) and len(set(locked))==2,\"LOCKED_RELATION_OIDS\")\n  # Validate these exact locked OIDs; no views, partitions, inheritance or RLS.\n  catalog=conn.execute(\"\"\"SELECT c.oid,c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,a.amname,\n    EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)\n    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace\n    LEFT JOIN pg_catalog.pg_am a ON a.oid=c.relam\n    WHERE c.oid IN ('assistant_lab.control_command'::regclass,'assistant_lab.job'::regclass) AND n.nspname='assistant_lab' ORDER BY c.relname\"\"\").fetchall()\n  need(catalog==[(locked[0],\"control_command\",\"r\",False,False,\"heap\",False),(locked[1],\"job\",\"r\",False,False,\"heap\",False)],\"QUEUE_CATALOG\")\n  cur=conn.execute(SQL);values=cur.fetchone()\n  row=dict(zip([c.name for c in cur.description],values))\n  need(row[\"database_name\"]==target.database and all(type(row[k]) is int and row[k]==0 for k in (\"lab_nonterminal\",\"control_nonterminal\",\"null_status_count\")) and row[\"job_rls_off\"] is True and row[\"control_rls_off\"] is True,\"QUEUE_NOT_ZERO_OR_VISIBLE\")\n  observed=row[\"observed_at\"];need(isinstance(observed,datetime.datetime) and observed.tzinfo is not None,\"SERVER_TIMESTAMP\")\n  row[\"observed_at\"]=observed.isoformat()\n  return row\n"
def schema_rename_baseline_query(conn):
 import hashlib
 p.need(hashlib.sha256(SCHEMA_RENAME_BASELINE_QUERY.encode()).hexdigest()=="e84608d6f6a7cf80ae696ec798c5c4e48f3ff31fbf2746eaaaafef4ddf0638cd","HISTORICAL_QUERY_PIN")
 scope={"datetime":datetime,"SQL":p.SQL,"need":p.need}
 exec(compile(SCHEMA_RENAME_BASELINE_QUERY,"<pinned-test-only-query>","exec"),scope)
 return scope["query"](conn)
