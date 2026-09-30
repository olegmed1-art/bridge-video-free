import hashlib, json, os, subprocess, types, urllib.request, uuid
SHA = 'e12c74d58f8564a9fad2032abfadbb1ecbf1f874'
ROOT = '/opt/bridge-school/school-autopilot-production-light'
def current_main():
    with urllib.request.urlopen('https://api.github.com/repos/olegmed1-art/bridge-video-free/branches/main', timeout=20) as r:
        data = r.read(65537)
    if len(data)>65536 or json.loads(data)['commit']['sha'] != SHA:
        raise RuntimeError('MAIN_CHANGED')
if os.geteuid()!=0 or os.uname().nodename!='autopilot-lite-vnic':
    raise RuntimeError('HOST_IDENTITY')
current_main()
url=f'https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/{SHA}/ops/oracle_light_active_hold_attest.py'
with urllib.request.urlopen(url, timeout=20) as r:
    code=r.read(65537)
if len(code)>65536 or hashlib.sha256(code).hexdigest()!='d4a6b43ed6d8207b8b1ee8d63b4acd41a2aea806a33cebd041746bafd8186f94':
    raise RuntimeError('SOURCE_HASH')
hold=types.ModuleType('verified_hold')
exec(compile(code,'<verified_hold>','exec'),hold.__dict__)
hold.main()
before=hold.service()
child='import json, re, subprocess\nroot=\'/opt/bridge-school/school-autopilot-production-light\'\nenv={\'HOME\':\'/tmp\',\'CODEX_HOME\':root+\'/runtime/codex-home\',\'PATH\':\'/usr/bin:/bin\',\'LANG\':\'C.UTF-8\',\'LC_ALL\':\'C.UTF-8\'}\nbinary=root+\'/runtime-bin/codex\'\nresult={\'audit\':\'SERVICE_CLOUD_READINESS\',\'tasks_started\':False,\'repository_binding_verified\':False}\ntry:\n version=subprocess.run([binary,\'--version\'],env=env,capture_output=True,text=True,timeout=10)\n if version.returncode!=0 or version.stdout.strip()!=\'codex-cli 0.157.0\':\n  raise ValueError(\'CLI_VERSION_UNCONFIRMED\')\n auth=subprocess.run([binary,\'-c\',\'forced_login_method="chatgpt"\',\'login\',\'status\'],env=env,capture_output=True,text=True,timeout=15)\n if auth.returncode!=0 or \'Logged in using ChatGPT\' not in (auth.stdout+\'\\n\'+auth.stderr).splitlines():\n  raise ValueError(\'CLI_AUTH_UNCONFIRMED\')\n result[\'auth\']=\'CLI_AUTH_READY\'\n response=subprocess.run([binary,\'-c\',\'forced_login_method="chatgpt"\',\'cloud\',\'list\',\'--env\',\'bridge-video-free\',\'--limit\',\'1\',\'--json\'],env=env,capture_output=True,text=True,timeout=30)\n if response.returncode!=0 or len(response.stdout)>65536:\n  raise ValueError(\'CLOUD_LIST_UNCONFIRMED\')\n data=json.loads(response.stdout)\n rows=data[\'tasks\']\n if not isinstance(rows,list) or len(rows)>1:\n  raise ValueError(\'CLOUD_LIST_SHAPE_INVALID\')\n ids=[row.get(\'environment_id\') for row in rows]\n result[\'cloud_access\']=\'READ_PASS\'\n result[\'environment_id_candidates\']=[v for v in ids if isinstance(v,str) and re.fullmatch(r\'[A-Za-z0-9_-]{1,128}\',v)]\n result[\'environment_binding\']=\'UNVERIFIED\'\nexcept BaseException:\n result[\'result\']=\'UNCONFIRMED\'\nprint(json.dumps(result,sort_keys=True))\n'
unit='light-cli-probe-'+uuid.uuid4().hex
args=['/usr/bin/systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit,
 '--property=User=school-autopilot','--property=Group=school-autopilot',
 '--property=ProtectSystem=strict','--property=ProtectHome=yes',
 '--property=NoNewPrivileges=yes','--property=PrivateTmp=yes','--property=PrivateDevices=yes',
 '--property=ProtectKernelTunables=yes','--property=ProtectKernelModules=yes',
 '--property=ProtectControlGroups=yes','--property=RestrictSUIDSGID=yes',
 '--property=Nice=10','--property=CPUQuota=100%','--property=MemoryHigh=512M',
 '--property=MemoryMax=768M','--property=TasksMax=64',
 '--property=WorkingDirectory='+before['WorkingDirectory'],
 '--property=RuntimeMaxSec=65','--property=TimeoutStopSec=5','--property=UMask=0077',
 '/usr/bin/python3','-I','-B','-c',child]
current_main()
result=None
try:
 result=subprocess.run(args,capture_output=True,text=True,timeout=80)
finally:
 hold.main()
 if hold.service()!=before:
  raise RuntimeError('SERVICE_CHANGED')
if result is None or result.returncode!=0:
 raise RuntimeError('SANDBOX_PROBE_FAILED')
try:
 status=json.loads(result.stdout)
except ValueError:
 raise RuntimeError('SANDBOX_OUTPUT_INVALID') from None
if status.get('audit')!='SERVICE_CLOUD_READINESS' or status.get('tasks_started') is not False:
 raise RuntimeError('PROBE_OUTPUT_INVALID')
current_main()
print(json.dumps(status,sort_keys=True))
if status.get('cloud_access')!='READ_PASS' or status.get('result')=='UNCONFIRMED':
 raise SystemExit(2)
