"""Read-only GitHub primary-source adapter. Fixed endpoints; no dispatch or writes."""
import concurrent.futures,datetime,json,os,re,sys,urllib.request
from ops.ibm_machine_queue_protocol import need,parse,canonical,HardCap
from ops.native_maintenance_store_runner import REPOSITORY,NoRedirect
OWNER=REPOSITORY.split("/")[0]
POWER_BRANCH="review/ibm-trial-control-20261001"
POWER_SHA="ef02dc1699e3112c5ace139cfc048417baedfbb2"
POWER_WORKFLOW=364280792
def context(main):
 need(re.fullmatch(r"[0-9a-f]{40}",main or "") and os.environ.get("GITHUB_SHA")==main and os.environ.get("GITHUB_REPOSITORY")==REPOSITORY and os.environ.get("GITHUB_REF")=="refs/heads/main" and os.environ.get("GITHUB_EVENT_NAME")=="workflow_dispatch" and os.environ.get("GITHUB_ACTOR")==OWNER and os.environ.get("GITHUB_TRIGGERING_ACTOR")==OWNER,"OWNER_MAIN_CONTEXT")
def get(path,token=None):
 need(path.startswith(("/git/ref/heads/","/actions/runs/")),"API_SCOPE")
 url="https://api.github.com/repos/"+REPOSITORY+path
 headers={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","Cache-Control":"no-cache","User-Agent":"fixed-machine-queue-proof"}
 if token:headers["Authorization"]="Bearer "+token
 opener=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}))
 with opener.open(urllib.request.Request(url,headers=headers),timeout=2) as r:
  need(r.status==200 and r.url==url,"API_RESPONSE");raw=r.read(65537)
 need(len(raw)<=65536,"API_LIMIT");return parse(raw)
def main_source(main):
 context(main);v=get("/git/ref/heads/main",os.environ.get("GH_TOKEN"))
 need(v.get("ref")=="refs/heads/main" and v.get("object",{}).get("type")=="commit" and v["object"]["sha"]==main,"MAIN_DRIFT")
def primary(main,run,nonce):
 context(main);need(re.fullmatch(r"[1-9][0-9]{5,14}",run or "") and re.fullmatch(r"[0-9a-f]{32}",nonce or ""),"SOURCE_BINDING")
 token=os.environ.get("GH_TOKEN")
 requests=[("/git/ref/heads/main",token),("/git/ref/heads/"+POWER_BRANCH,token),("/actions/runs/"+run,None),("/actions/runs/"+run+"/attempts/1/jobs?per_page=100",None)]
 # Run/jobs are explicitly public API reads. No token scope fallback or escalation.
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  values=list(pool.map(lambda q:get(*q),requests))
 m,p,active,jobs=values
 for value,branch,pin in ((m,"main",main),(p,POWER_BRANCH,POWER_SHA)):
  need(value.get("ref")=="refs/heads/"+branch and value.get("object",{}).get("type")=="commit" and value["object"]["sha"]==pin,"SOURCE_DRIFT")
 need(type(active.get("id")) is int and str(active["id"])==run and active.get("workflow_id")==POWER_WORKFLOW and active.get("head_sha")==POWER_SHA and active.get("head_branch")==POWER_BRANCH and active.get("status")=="in_progress" and active.get("event")=="workflow_dispatch" and active.get("run_attempt")==1 and active.get("repository",{}).get("full_name")==REPOSITORY and active.get("actor",{}).get("login")==OWNER and active.get("triggering_actor",{}).get("login")==OWNER,"POWER_RUN_SCOPE")
 rows=jobs.get("jobs");need(type(rows) is list and type(jobs.get("total_count")) is int and len(rows)==jobs["total_count"]<=100,"JOBS_COMPLETE")
 names=[x.get("name") for x in rows];need(len(set(names))==len(names),"JOB_DUPLICATE")
 indexed={x["name"]:x for x in rows}
 trial=indexed.get("trial-start",{});manual=indexed.get("manual-console-trial",{})
 need(trial.get("status")=="completed" and trial.get("conclusion")=="skipped" and manual.get("status")=="in_progress" and manual.get("conclusion") is None,"POWER_PHASE")
 steps=manual.get("steps",[])
 need(sum(x.get("name")=="Owner manual serial console window with bounded ordinary Stop" and x.get("status")=="in_progress" for x in steps)==1,"POWER_STEP")
 return {"kind":"PRIMARY_RESULT","nonce":nonce,"main_sha":main,"power_sha":POWER_SHA,"run":run,"run_status":"in_progress","mode":"manual_console_trial","trial_start":"skipped","manual_console_trial":"in_progress","verified_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}
def entry():
 cap=HardCap(180);main=os.environ.get("EXPECTED_MAIN");run=None
 try:
  main_source(main)
  print(canonical({"kind":"SOURCE_READY"}).decode(),end="",flush=True)
  for raw in iter(lambda:sys.stdin.buffer.readline(65537),b""):
   need(len(raw)<=65536 and raw.endswith(b"\n"),"FRAME_LIMIT");v=parse(raw)
   if v.get("kind")=="SOURCE_BIND":
    need(set(v)=={"kind","run","nonce"} and run is None,"SOURCE_BIND_SCHEMA");run=v["run"]
   else:
    need(set(v)=={"kind","phase","nonce","host","boot","run","requested_mono","project","branch","database","sql_sha256"} and v["kind"]=="QUEUE_REQUEST" and v["run"]==run,"SOURCE_REQUEST_SCHEMA")
   result=primary(main,run,v["nonce"]);print(canonical(result).decode(),end="",flush=True)
 except BaseException:
  print(canonical({"kind":"ADAPTER_ERROR"}).decode(),end="",flush=True);return 2
 finally:cap.close()
 return 0
if __name__=="__main__":sys.exit(entry())
