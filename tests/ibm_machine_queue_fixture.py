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
  if sql=="SELECT current_setting('transaction_read_only')":return Result(("off" if self.mode=="READWRITE" else "on",))
  if sql=="SELECT current_database(),session_user,current_user":return Result((TARGET["database"],TARGET["session_owner"],"other" if self.mode=="ROLE" else TARGET["owner"]))
  if "FROM pg_catalog.pg_settings" in sql:
   n=TARGET["neon"];rows=[("neon.project_id",n["project_id"],"postmaster","configuration file",n["project_id"],False),("neon.branch_id",n["branch_id"],"postmaster","configuration file",n["branch_id"],False),("neon.endpoint_id",n["endpoint_id"],"superuser","configuration file",n["endpoint_id"],False)]
   if self.mode=="GUC_SOURCE":rows[2]=(*rows[2][:3],"session",*rows[2][4:])
   if self.mode=="GUC_MISSING":rows.pop()
   return Result(rows=rows)
  if "FROM pg_catalog.pg_class" in sql:
   rows=[("control_command","r",False,False,"heap",False),("job","r",self.mode=="CAT_RLS",False,"heap",False)]
   if self.mode=="CAT_VIEW":rows[1]=("job","v",False,False,None,False)
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
 elif role=="ssh":
  # Mock SSH still introduces a real second-hop process and real duplex pipes.
  send({"kind":"CHANNEL_PREPARED_NO_IBM_CONNECTION"});send({"kind":"CHANNEL_BINDING","reviewed":reviewed(),"source_pins":PINS})
  p.need(recv()=={"kind":"CHANNEL_ACCEPT"},"FIXTURE_ADMISSION")
  child=p.PipeClient([sys.executable,"-B",__file__,"guest",mode],dict(os.environ),2)
  try:
   send(child.recv(time.monotonic()+5))
   for _ in range(2):
    r=child.recv(time.monotonic()+5);send(r);child.send(recv(),time.monotonic()+2)
   send(child.recv(time.monotonic()+5))
  finally:child.close()
 else:raise RuntimeError("FIXTURE_ROLE")
if __name__=="__main__":
 try:main(*sys.argv[1:])
 except BaseException:send({"kind":"ADAPTER_ERROR"});sys.exit(2)
