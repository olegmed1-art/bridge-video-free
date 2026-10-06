"""Fixed synthetic worker; never imports provider/DB connector or owner dependencies."""
import errno,json,os,signal,socket,sys,time
from stagea_synthetic_core import need,source_guard
from database.pr1994_stage_a import RELATIONS,FUNCTIONS
WIRE=b"STAGEA_SYNTHETIC_NO_CREDENTIAL"
RUNTIME="a"*64
def payload():
 return json.dumps({"stage":"A","catalog_sha256":"b"*64,"runtime_id":RUNTIME,
  "relations":[{"name":n,"definition":"SYNTHETIC_PRIVATE_DO_NOT_LOG"} for n in RELATIONS],
  "functions":[{"name":n,"definition":"SYNTHETIC_PRIVATE_DO_NOT_LOG"} for n in FUNCTIONS],
  "triggers":[],"production_mutations":False,"stage_b_allowed":False,
  "ddl_window_proven":False,"service_hold_unchanged":True,"database_admission_observed":False},
  sort_keys=True,separators=(",",":")).encode()
def assert_network_sandbox():
 # Constructing a socket sends NO network traffic. Kernel AF restriction must
 # reject it; do not substitute a failed TCP connection for this proof.
 try:handle=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
 except OSError as exc:
  need(exc.errno in (errno.EAFNOSUPPORT,errno.EPERM,errno.EACCES));return
 handle.close();need(False)
def run(manifest_pin,mode):
 need(mode in ("payload","hang"))
 source_guard(manifest_pin)
 assert_network_sandbox()
 need(sys.stdin.buffer.read(1024)==WIRE)
 if mode=="payload":
  sys.stdout.buffer.write(payload());sys.stdout.buffer.flush();return 0
 # PID1 service contains both real processes, even if main is killed/stopped.
 child=os.fork()
 if child==0:
  os.setsid();signal.signal(signal.SIGTERM,signal.SIG_IGN)
  while True:time.sleep(1)
 while True:time.sleep(1)
