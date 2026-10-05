"""Existing owner SSH secret in sealed memfd; private reviewed packet -> short entry."""
import argparse,base64,fcntl,hashlib,io,json,os,re,stat,sys,tarfile,tempfile,types,shlex
from pathlib import Path,PurePosixPath
from diagnostic_process import run,need
FP="SHA256:XBR1x74uJ41BxmDF7Y9P20GjIjNbrYXqieV4c2MC0Go"
def memory(raw,name):
 fd=os.memfd_create(name,os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
 try:
  os.fchmod(fd,0o600);need(stat.S_IMODE(os.fstat(fd).st_mode)==0o600,"MEMFD_MODE")
  offset=0
  while offset<len(raw):offset+=os.write(fd,raw[offset:])
  fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SEAL);return fd
 except BaseException:os.close(fd);raise
def known_hosts():
 import time
 raw=run(["/usr/bin/ssh-keyscan","-T","3","-t","ed25519","92.5.47.149"],time.monotonic()+4)
 rows=[r.split() for r in raw.decode().splitlines() if r and not r.startswith("#")]
 need(len(rows)==1 and rows[0][:2]==["92.5.47.149","ssh-ed25519"],"ORACLE_PUBLIC_TRUST")
 need("SHA256:"+base64.b64encode(hashlib.sha256(base64.b64decode(rows[0][2],validate=True)).digest()).decode().rstrip("=")==FP,"ORACLE_FINGERPRINT")
 return raw
def private_packet(digest,keyfd,knownfd):
 import time
 root="/home/ubuntu/.local/share/bridge-school/ibm-diagnostic-v9/"+digest
 code="import os,stat,hashlib,base64;assert os.getuid()==1001;root="+repr(root)+";fds=[];fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(fd)\nfor part in root.split('/')[1:]:\n fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);fds.append(fd);s=os.fstat(fd);assert s.st_uid in (0,1001) and not(s.st_mode&0o022)\ns=os.fstat(fd);assert s.st_uid==1001 and stat.S_IMODE(s.st_mode)==0o700;rootfd=fd;f=os.open('DIAGNOSTIC-PACKAGE.tar',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=rootfd);s=os.fstat(f);assert stat.S_ISREG(s.st_mode) and s.st_uid==1001 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=8388608;raw=os.read(f,s.st_size+1);after=os.fstat(f);assert len(raw)==s.st_size and (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) and hashlib.sha256(raw).hexdigest()=="+repr(digest)+";print(base64.b64encode(raw).decode());os.close(f)\nfor fd in reversed(fds):os.close(fd)"
 cmd=["/usr/bin/ssh","-F","/dev/null","-T","-i","/proc/"+str(os.getpid())+"/fd/"+str(keyfd),"-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","StrictHostKeyChecking=yes","-o","HostKeyAlgorithms=ssh-ed25519","-o","GlobalKnownHostsFile=/dev/null","-o","UserKnownHostsFile=/proc/"+str(os.getpid())+"/fd/"+str(knownfd),"-o","UpdateHostKeys=no","-o","VerifyHostKeyDNS=no","-o","ForwardAgent=no","-o","ClearAllForwardings=yes","-o","ConnectTimeout=3","-o","ConnectionAttempts=1","ubuntu@92.5.47.149","/usr/bin/python3 -I -B -c "+shlex.quote(code)]
 raw=base64.b64decode(run(cmd,time.monotonic()+10,limit=16*1024*1024).strip(),validate=True)
 need(len(raw)<=8388608 and hashlib.sha256(raw).hexdigest()==digest,"PRIVATE_ARCHIVE_PIN")
 return raw
def unpack(root,raw):
 total=0;names=set()
 with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as tar:
  for m in tar.getmembers():
   p=PurePosixPath(m.name);total+=m.size
   need(len(names)<64 and m.isfile() and m.name==str(p) and not p.is_absolute() and ".." not in p.parts and m.name not in names and 0<m.size<=131072 and total<=8388608,"PRIVATE_ARCHIVE_SCOPE");names.add(m.name)
   target=root/m.name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   try:
    data=tar.extractfile(m).read();offset=0
    while offset<len(data):offset+=os.write(fd,data[offset:])
    os.fsync(fd)
   finally:os.close(fd)
def main(argv=None):
 p=argparse.ArgumentParser()
 for name in ("archive-sha","manifest-sha","expected-main"):p.add_argument("--"+name,required=True)
 p.add_argument("--run-reviewed",action="store_true");a=p.parse_args(argv)
 need(re.fullmatch("[0-9a-f]{64}",a.archive_sha) and re.fullmatch("[0-9a-f]{64}",a.manifest_sha) and re.fullmatch("[0-9a-f]{40}",a.expected_main),"INPUT_PINS")
 expected={"GITHUB_REPOSITORY":"olegmed1-art/bridge-video-free","GITHUB_ACTOR":"olegmed1-art","GITHUB_TRIGGERING_ACTOR":"olegmed1-art","GITHUB_EVENT_NAME":"workflow_dispatch","GITHUB_REF":"refs/heads/main","GITHUB_SHA":a.expected_main,"GITHUB_RUN_ATTEMPT":"1","GITHUB_JOB":"ibm-diagnostic-trial"}
 need(all(os.environ.get(k)==v for k,v in expected.items()) and re.fullmatch("[1-9][0-9]{5,14}",os.environ.get("GITHUB_RUN_ID","")),"OWNER_SETUP_CONTEXT")
 key=bytearray(os.environ.pop("ORACLE_SSH_PRIVATE_KEY","").encode());need(32<len(key)<32768,"EXISTING_OWNER_SECRET")
 keyfd=knownfd=None
 try:
  keyfd=memory(key,"existing-oracle-owner-key")
  for i in range(len(key)):key[i]=0
  knownfd=memory(known_hosts(),"oracle-public-trust")
  raw=private_packet(a.archive_sha,keyfd,knownfd)
  root=Path(tempfile.mkdtemp(prefix="ibm-diagnostic-",dir=os.environ.get("RUNNER_TEMP")))
  unpack(root,raw)
  manifest_raw=(root/"SUCCESSOR-MANIFEST.json").read_bytes();need(hashlib.sha256(manifest_raw).hexdigest()==a.manifest_sha,"MANIFEST_PIN")
  manifest=json.loads(manifest_raw);need(manifest["main_sha"]==a.expected_main,"SOURCE_MAIN")
  for name,pin in manifest["files"].items():need(hashlib.sha256((root/name).read_bytes()).hexdigest()==pin,"PRIVATE_CLOSURE")
  raw=(root/"diagnostic_entry.py").read_bytes()
  module=types.ModuleType("reviewed_diagnostic_entry");module.__file__=str(root/"diagnostic_entry.py");sys.modules[module.__name__]=module
  exec(compile(raw,module.__file__,"exec"),module.__dict__)
  # Same process holds memory fds; target c.Channel opens parent's /proc fd paths.
  os.environ.update(ORACLE_KEY_PATH="/proc/"+str(os.getpid())+"/fd/"+str(keyfd),ORACLE_KNOWN_HOSTS="/proc/"+str(os.getpid())+"/fd/"+str(knownfd))
  args=["--manifest-sha",a.manifest_sha]+(["--run-reviewed"] if a.run_reviewed else [])
  return module.entry(args)
 finally:
  for i in range(len(key)):key[i]=0
  for fd in (knownfd,keyfd):
   if fd is not None:os.close(fd)
if __name__=="__main__":
 try:raise SystemExit(main())
 except Exception:print('{"kind":"OWNER_SETUP_REFUSED","live_admission":false}');raise SystemExit(78)
