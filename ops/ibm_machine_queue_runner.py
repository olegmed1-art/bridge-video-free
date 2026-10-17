"""Owner-main runner; local DB/API responders, fixed duplex SSH, no power API."""
import base64,hashlib,os,re,shlex,stat,subprocess,sys,tempfile,time
from pathlib import Path
from ops.ibm_machine_queue_protocol import PipeClient,Supervisor,HardCap,need,accept_channel
from ops.ibm_machine_queue_source import context,main_source,POWER_SHA
from ops.native_maintenance_store_runner import HOST,REPOSITORY
ROOT=Path(__file__).resolve().parents[1]
ROUTE_WORKFLOW=".github/workflows/native-maintenance-owner-host.yml"
ROUTE_SHA="6afa5ef26efd3d3d5151d9bd47c629067fd0f23ae02f30dec33dd518295faf38"
def validate_inputs(main,mode,package):
 need(type(main) is str and re.fullmatch(r"[0-9a-f]{40}",main),"MAIN_SELECTOR")
 need(mode in ("queue-qualify","bounded-relay"),"MODE")
 need(type(package) is str and (bool(re.fullmatch(r"[0-9a-f]{64}",package)) if mode=="bounded-relay" else package==""),"PACKAGE_SELECTOR")
 return main,mode,package
def route():
 raw=(ROOT/ROUTE_WORKFLOW).read_bytes();need(hashlib.sha256(raw).hexdigest()==ROUTE_SHA,"ROUTE_SOURCE_DRIFT")
 lines=[x.strip() for x in raw.decode().splitlines() if x.strip().startswith("bash ops/oracle_known_hosts_from_scan.sh ")]
 need(len(lines)==1,"ROUTE_SOURCE");args=shlex.split(lines[0])
 need(len(args)==5 and args[:2]==["bash","ops/oracle_known_hosts_from_scan.sh"] and HOST=="ubuntu@"+args[2] and re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}",args[3]) and args[4]=="$work/known_hosts","ROUTE_CONTRACT")
 return args[2],args[3]
def child_env(role):
 names={"PATH","LANG","EXPECTED_MAIN","GITHUB_SHA","GITHUB_REPOSITORY","GITHUB_REF","GITHUB_EVENT_NAME","GITHUB_ACTOR","GITHUB_TRIGGERING_ACTOR"}
 names.add("NATIVE_OWNER_DATABASE_URL" if role=="db" else "GH_TOKEN")
 return {k:v for k,v in os.environ.items() if k in names}
def bootstrap(main,package):
 code="import base64,hashlib,sys,types\np=types.ModuleType('ops');p.__path__=[];sys.modules['ops']=p\n"
 for name in ("ibm_machine_queue_protocol","ibm_machine_queue_oracle"):
  raw=(ROOT/("ops/"+name+".py")).read_bytes();need(len(raw)<50000,"BOOTSTRAP_LIMIT")
  encoded=base64.b64encode(raw).decode();digest=hashlib.sha256(raw).hexdigest()
  code+="raw=base64.b64decode("+repr(encoded)+");assert hashlib.sha256(raw).hexdigest()=="+repr(digest)+";m=types.ModuleType('ops."+name+"');sys.modules[m.__name__]=m;exec(compile(raw,'<verified-channel>','exec'),m.__dict__)\n"
 return code+"sys.exit(m.entry("+repr(main)+","+repr(package)+"))\n"
def ssh_argv(key,known,main,package):
 need(re.fullmatch(r"[0-9a-f]{40}",main) and re.fullmatch(r"[0-9a-f]{64}",package),"BOOTSTRAP_PINS")
 return ["/usr/bin/ssh","-F","/dev/null","-T","-i",str(key),"-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","ForwardAgent=no","-o","ClearAllForwardings=yes","-o","StrictHostKeyChecking=yes","-o","UserKnownHostsFile="+str(known),"-o","ConnectTimeout=3","-o","ConnectionAttempts=1","-o","ServerAliveInterval=3","-o","ServerAliveCountMax=1",HOST,shlex.join(["/usr/bin/python3","-I","-B","-u","-c",bootstrap(main,package)])]
def prepare_key(root):
 host,fingerprint=route();key=root/"key";known=root/"known_hosts"
 value=os.environ.pop("SSH_PRIVATE_KEY","");need(0<len(value)<=32768,"EXISTING_SSH_REFERENCE")
 fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"w") as f:f.write(value.replace("\r","")+"\n")
 del value
 env={"PATH":"/usr/bin:/bin"}
 p=subprocess.run(["/usr/bin/ssh-keygen","-y","-P","","-f",str(key)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,timeout=3)
 need(p.returncode==0,"SSH_KEY_METADATA")
 p=subprocess.run(["/bin/bash",str(ROOT/"ops/oracle_known_hosts_from_scan.sh"),host,fingerprint,str(known)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,timeout=8)
 need(p.returncode==0,"HOST_KEY_VERIFICATION");return key,known
def binding(frame,main,dbready):
 need(set(frame)=={"kind","reviewed","source_pins"} and frame["kind"]=="CHANNEL_BINDING","CHANNEL_BIND_SCHEMA")
 r=frame["reviewed"];need(type(r) is dict and set(r)=={"host","boot","run","main_sha","power_sha","project","branch","database"},"CHANNEL_REVIEW_SCHEMA")
 need(r["main_sha"]==main and r["power_sha"]==POWER_SHA and (r["project"],r["branch"],r["database"])==(dbready["project"],dbready["branch"],dbready["database"]),"CHANNEL_SOURCE_SCOPE")
 need(type(r["host"]) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,100}",r["host"]) and re.fullmatch(r"[0-9a-f-]{36}",r["boot"]) and re.fullmatch(r"[1-9][0-9]{5,14}",r["run"]),"CHANNEL_IDENTITY")
 pins=frame["source_pins"];need(type(pins) is dict and set(pins)=={"repair_gate.py","fs_transaction.py","host_adapter.py","executor.py","inspector.py"} and all(type(x) is str and re.fullmatch(r"[0-9a-f]{64}",x) for x in pins.values()),"GUEST_PINS")
 return r
def close_clients(clients):
 errors=[]
 while clients:
  child=clients.pop()
  try:child.close()
  except BaseException:errors.append(True)
 need(not errors,"CLEANUP_FAILED")
def entry():
 deadline=time.monotonic()+160;cap=HardCap(160);clients=[]
 try:
  main,mode,package=validate_inputs(os.environ.get("EXPECTED_MAIN"),os.environ.get("QUEUE_MODE"),os.environ.get("EXPECTED_PACKAGE_SHA",""));context(main)
  need(subprocess.check_output(["/usr/bin/git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()==main and subprocess.run(["/usr/bin/git","diff","--quiet","HEAD","--"],cwd=ROOT).returncode==0,"CHECKOUT_DRIFT")
  main_source(main)
  db=PipeClient([sys.executable,"-B","-u","-m","ops.ibm_machine_queue_db"],child_env("db"),5);clients.append(db)
  os.environ.pop("NATIVE_OWNER_DATABASE_URL",None)
  dbready=db.recv(time.monotonic()+15)
  need(set(dbready)=={"kind","project","branch","database","role"} and dbready["kind"]=="DB_READY" and dbready["role"]=="neondb_owner","DB_PREPARE")
  if mode=="queue-qualify":
   main_source(main);close_clients(clients);cap.close();cap=None;print('{"audit":"ASSISTANT_LAB_QUEUE_READ_ONLY_PASS","production_mutations":false,"live_actions":0}',flush=True);return 0
  source=PipeClient([sys.executable,"-B","-u","-m","ops.ibm_machine_queue_source"],child_env("source"),5);clients.append(source)
  need(source.recv(time.monotonic()+5)=={"kind":"SOURCE_READY"},"SOURCE_PREPARE")
  with tempfile.TemporaryDirectory(prefix="machine-queue-",dir=os.environ["RUNNER_TEMP"]) as temp:
   key,known=prepare_key(Path(temp))
   main_source(main)
   relay=PipeClient(ssh_argv(key,known,main,package),{"PATH":"/usr/bin:/bin"},65);clients.append(relay)
   need(relay.recv(time.monotonic()+10)=={"kind":"CHANNEL_PREPARED_NO_IBM_CONNECTION"},"CHANNEL_PREPARE")
   print('{"audit":"MACHINE_QUEUE_PREPARED_NO_IBM_CONNECTION"}',flush=True)
   frame=relay.recv(time.monotonic()+65);reviewed=binding(frame,main,dbready)
   # Primary proof BEFORE allowing Oracle to make any IBM SSH connection.
   import uuid
   bind_nonce=uuid.uuid4().hex
   primary=source.call({"kind":"SOURCE_BIND","run":reviewed["run"],"nonce":bind_nonce},time.monotonic()+5)
   accept_channel(relay,primary,reviewed,bind_nonce,deadline)
   result=Supervisor(db,source,reviewed,5,used_nonces=(bind_nonce,)).session(relay,frame)
   need(result["proofs"]==2,"PROOF_COUNT")
  close_clients(clients);cap.close();cap=None
  print('{"audit":"MACHINE_QUEUE_RELAY_COMPLETE","proofs":2,"parent_stop_required":true,"automatic_starts":0}',flush=True);return 0
 except BaseException:
  print('{"audit":"MACHINE_QUEUE_REFUSED","parent_stop_required":true,"automatic_starts":0}',flush=True);return 2
 finally:
  try:close_clients(clients)
  finally:
   if cap:cap.close()
if __name__=="__main__":sys.exit(entry())
