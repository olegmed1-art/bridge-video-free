#!/usr/bin/env python3
"""Manual Light production installation; preserves existing shadow services."""
import ast, base64, fcntl, hashlib, json, os, pwd, shlex, shutil, socket, subprocess, tempfile, time
from pathlib import Path
import urllib.request

REV = "962903f3f2ee7ca9b63147b28cd889bf03838314"
MODULES = {"autopilot_phase3b/__init__.py":"15b86e2fa79e853813f6b3593a996f755a25693d","autopilot_phase3b/policy.py":"b67389c0b9dc9eea0e0513f58b409cd34cabf4c8","oracle_autopilot/__init__.py":"1c22c517227c70574add22ad8d8edfb17bcd3bda","oracle_autopilot/contract.py":"47f6a0a4bbc26447a4118cf19bd39255303e3ac9","oracle_autopilot/github_codex_callback.py":"e82d2e751b1ea112ca539592e42929c04d1da2a6","oracle_autopilot/github_role_callback.py":"31d871b313084cbb671eea876c63f75f6471c896","oracle_autopilot/ibf_board_structured.py":"03a019d54ccca3de2d3a62fa034f198632fb4568","oracle_autopilot/ibf_read_only.py":"cc7f9303eff9deea7776c270bf3b30c2febf7d45","oracle_autopilot/online_observer.py":"8b26019ffe1d7e3010b36e50ba0c4360bd9371ab","oracle_autopilot/worker.py":"5e4410fa7a92a4aca40079563b56d6592ff0bc91","oracle_autopilot/worker_v16.py":"5969302153ab71716c0d7f673301a7221047ecff","oracle_autopilot/worker_v17.py":"110aff5cbf8c4df01f05f54fe672e632e51c796d"}
CIPHER = "gz1vpn+75ZR+bkxy1XUW6c6azcpzbGAFy1oURZ3KLjEB43pnaY7GNsxcdYm1649PBNAQZ5cIZeoQ9QJUB5inV86Z5EEXjnhQXZzuxJmEfL6tRFmU5nmf9F/TRzDDyfFZ01EzAByptMc1jjSW6N6flagKKIuf4XBTxVoCz7YVebocqttiOrPzfkvxjGRnWHqxhnQOXnDdBPB0nW69DHPvcBsqQlX6wQlwu9tcRMa0Ju3ZsEY2DDzGfBeGDXrW9/MzyYHUZINjLaG5f6ivBbslPVhVByXcrVvzMe6adMQZIYgh5gTaX0YS6GLZZaoI0CthLzehwrd3QUlKB2ghoeD0Vm4XF+LgCAhkMoZrOfgzIL/dWKtOzU92cST4eHHoIo9/sCDwPrvFNHcEMxDi802KFngWT/pMc3waHGbUmlKdPZH9cHv+Uob6bhBgY+n46Mamge3ZYs5KsGV8ahkahtdYSVLoeLVGZLGDCM9Y6wKY6cAu5xEhJBWBkLCZuvb0UDCc"
BROKER = {
  "schema_version": 1,
  "broker_url": "https://bridge-school-autopilot-77iri20rz-olegmed1-4368s-projects.vercel.app/v1/github/draft-repair",
  "broker_source_sha": "e632334f2d5dc28f8e8ea4892e43d341850c3f11",
  "broker_artifact_sha256": "b6ff9bfc2617ff152994d363e76b69cf7ea4426244d5de75b5844e4030724b7b",
  "broker_policy_sha256": "418d2d1fe7873f2ed3f602ed990a64fa99adc4a2d01afaf4930feac059fa5958",
  "broker_policy_version": "physical-no-merge-v2",
  "broker_provenance_sha256": "a1514d3797023ccdf0db170b6f70c4a9dcfe3a1630b8e4abca728c4dedd6477b"
}

ROOT = Path("/opt/bridge-school/school-autopilot-production-light")
OLD = Path("/opt/bridge-school/school-autopilot")
PYTHON = OLD / ".venv/bin/python"
UNIT = "school-autopilot-production-light.service"
ENV = Path("/etc/school-autopilot-production-light.env")
RELEASE = ROOT / "releases" / REV

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()

def parse(path):
    values = {}
    for line in path.read_text().splitlines():
        for item in shlex.split(line, comments=True):
            if "=" in item:
                k, v = item.split("=", 1)
                values[k] = v
    return values

def main():
    assert os.geteuid() == 0, "Administrator required"
    assert socket.gethostname() == "autopilot-lite-vnic", "Wrong host"
    os.umask(0o022)
    with open("/run/lock/autopilot-production-light.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        unit_path = Path("/etc/systemd/system") / UNIT
        assert not ROOT.exists() and not ENV.exists() and not unit_path.exists(), "Already staged; review before retry"
        service_user = pwd.getpwnam("school-autopilot")
        watched = ["school-autopilot-shadow.service", "school-autopilot-online-observer.service"]
        pids = {u:run("systemctl","show",u,"-p","MainPID","--value") for u in watched}
        assert all(int(p)>0 for p in pids.values()), "Existing shadow services must be active"
        env = parse(OLD / "autopilot-shadow.env")
        env.update(parse(OLD / "autopilot-broker.env"))
        with tempfile.TemporaryDirectory(prefix="light-prod-", dir="/run") as temp:
            temp = Path(temp)
            host = temp / "host.key"
            host.write_bytes(Path("/etc/ssh/ssh_host_rsa_key").read_bytes())
            host.chmod(0o600)
            subprocess.run(["ssh-keygen","-p","-m","PEM","-P","","-N","","-f",str(host)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            decrypted = subprocess.run(
                ["openssl","pkeyutl","-decrypt","-inkey",str(host),
                 "-pkeyopt","rsa_padding_mode:oaep","-pkeyopt","rsa_oaep_md:sha256"],
                input=base64.b64decode(CIPHER), capture_output=True, check=True).stdout.decode()
        env.update({
            "AUTOPILOT_DATABASE_URL": decrypted,
            "AUTOPILOT_EXPECTED_DB_USER": "autopilot_light_worker_login",
            "AUTOPILOT_WORKER_ID": "oracle-autopilot-light-1",
            "AUTOPILOT_RUNTIME_MODE": "SHADOW",
            "AUTOPILOT_TOKEN_BROKER_URL": BROKER["broker_url"],
            "AUTOPILOT_TOKEN_BROKER_EXPECTED_SOURCE_SHA": BROKER["broker_source_sha"],
            "AUTOPILOT_TOKEN_BROKER_EXPECTED_ARTIFACT_SHA256": BROKER["broker_artifact_sha256"],
            "AUTOPILOT_TOKEN_BROKER_EXPECTED_POLICY_SHA256": BROKER["broker_policy_sha256"],
            "AUTOPILOT_TOKEN_BROKER_EXPECTED_PROVENANCE_SHA256": BROKER["broker_provenance_sha256"],
        })
        try:
            ROOT.mkdir(mode=0o755)
            RELEASE.mkdir(parents=True, mode=0o755)
            opener = urllib.request.build_opener(NoRedirect)
            for path, expected in MODULES.items():
                url = "https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/" + REV + "/" + path
                with opener.open(url, timeout=20) as response:
                    data = response.read(1000000)
                assert hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest() == expected, "Source hash mismatch"
                ast.parse(data)
                target = RELEASE / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o644)
            (RELEASE / "SOURCE_REVISION").write_text(REV+"\n")
            runtime = ROOT / "runtime"
            runtime.mkdir(mode=0o700)
            os.chown(runtime, service_user.pw_uid, service_user.pw_gid)
            env["PYTHONPATH"] = str(RELEASE)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
            preflight = '''
import os, sys, psycopg
from datetime import datetime, timezone
from urllib.parse import urlsplit
from oracle_autopilot import worker
try:
    cfg=worker.load_config()
    assert cfg.worker_id == "oracle-autopilot-light-1"
    u=urlsplit(cfg.dsn)
    assert u.hostname=="ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech"
    with psycopg.connect(cfg.dsn,connect_timeout=10,options="-c default_transaction_read_only=on") as c:
        row=c.execute("SELECT current_user,current_database(),current_setting('neon.project_id'),current_setting('neon.branch_id'),autopilot.verify_broker_schema_v0321()").fetchone()
        assert row==("autopilot_light_worker_login","neondb","misty-poetry-18012774","br-wispy-lab-b1rq54of",True)
        role=c.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil::text FROM pg_roles WHERE rolname=current_user").fetchone()
        assert role==(False,False,False,False,False,4,"infinity")
        memberships=c.execute("SELECT p.rolname FROM pg_auth_members m JOIN pg_roles p ON p.oid=m.roleid JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=current_user ORDER BY p.rolname").fetchall()
        assert memberships==[("autopilot_runtime_principal",)]
        privileges=c.execute("""SELECT n.nspname,c.relname,v.priv
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('TRIGGER'),('REFERENCES')) v(priv)
            WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname NOT LIKE 'pg_%'
            AND n.nspname <> 'information_schema'
            AND has_table_privilege(current_user,c.oid,v.priv)""").fetchall()
        allowed={("autopilot","task_status","SELECT"),("public","pg_stat_statements","SELECT"),("public","pg_stat_statements_info","SELECT")}
        assert set(privileges)<=allowed
        elevated=c.execute("""SELECT p.rolname FROM pg_roles p
            WHERE pg_has_role(current_user,p.oid,'USAGE')
            AND (p.rolsuper OR p.rolcreatedb OR p.rolcreaterole OR p.rolreplication OR p.rolbypassrls)""").fetchall()
        assert not elevated
    print("PRODUCTION_DB_IDENTITY_AND_AUTH=PASS",flush=True)
    result=worker.fetch_github_project_head("olegmed1-art/bridge-video-free",1609)
    assert result["head_sha"]=="c3492ec64bfee7d82cd59762d6883521d5fae719"
    print("BROKER_AUTH_AND_PROVENANCE=PASS",flush=True)
except Exception as e:
    print("PREFLIGHT_FAILED="+type(e).__name__,flush=True)
    sys.exit(1)
'''
            gate = subprocess.run([str(PYTHON),"-c",preflight],env=env,cwd=RELEASE,
                                  user=service_user.pw_uid,group=service_user.pw_gid,
                                  extra_groups=[service_user.pw_gid],capture_output=True,text=True,timeout=100)
            print(gate.stdout, end="")
            assert gate.returncode==0, "Preflight failed; no service installed or started"
        except BaseException:
            # Only this newly created, never-started staging tree is removed.
            if ROOT.exists():
                shutil.rmtree(ROOT)
            raise
        # Preserve original configuration. The new service receives only its own env.
        keep = {k:v for k,v in env.items() if k.startswith("AUTOPILOT_")}
        assert all("\n" not in v and "\r" not in v for v in keep.values())
        fd=os.open(ENV,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as out:
            for k,v in sorted(keep.items()):
                out.write(k+"="+json.dumps(v,ensure_ascii=False)+"\n")
        unit = f"""[Unit]
Description=Bridge School Light production worker
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User=school-autopilot
Group=school-autopilot
WorkingDirectory={RELEASE}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
EnvironmentFile={ENV}
ExecStart={PYTHON} -m oracle_autopilot.worker_v17
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
Nice=10
CPUQuota=100%
MemoryHigh=512M
MemoryMax=768M
TasksMax=64
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
ReadWritePaths={runtime}

[Install]
WantedBy=multi-user.target
"""
        unit_path.write_text(unit)
        unit_path.chmod(0o644)
        started=time.time()
        try:
            subprocess.run(["systemctl","daemon-reload"],check=True)
            subprocess.run(["systemd-analyze","verify",str(unit_path)],check=True,capture_output=True)
            subprocess.run(["systemctl","enable","--now",UNIT],check=True,capture_output=True,timeout=40)
            time.sleep(15)
            assert run("systemctl","is-active",UNIT)=="active"
            assert run("systemctl","show",UNIT,"-p","NRestarts","--value")=="0"
            journal=run("journalctl","-u",UNIT,"--since","@"+str(int(started)),"--no-pager","-o","cat")
            assert "worker_started worker_id=oracle-autopilot-light-1" in journal
            assert not any(x in journal for x in ["Traceback"," WARNING "," ERROR "," CRITICAL ","listener_reconnect","role_dispatch_failure","project_work_probe_failed"]), "Worker reported errors"
            assert all(run("systemctl","show",u,"-p","MainPID","--value")==p for u,p in pids.items())
        except BaseException:
            subprocess.run(["systemctl","disable","--now",UNIT],capture_output=True,timeout=40)
            print("NEW_SERVICE_STOPPED; existing services retained")
            raise
        print("LIGHT_PRODUCTION_WORKER=ACTIVE")
        print("WORKER_ID=oracle-autopilot-light-1")
        print("EXISTING_SHADOW_SERVICES=UNCHANGED")
        print("ROLLBACK: systemctl disable --now "+UNIT)

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        print("INSTALL_STOPPED="+type(e).__name__)
        raise SystemExit(1)
