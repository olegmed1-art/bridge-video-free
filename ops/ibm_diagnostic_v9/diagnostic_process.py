"""Bounded subprocess bytes; clean EOF and exit0 required. No stderr echo."""
import ctypes,os,selectors,signal,subprocess,time,sys
def need(ok,code):
 if not ok:raise RuntimeError(code)
def run(argv,end,limit=65536,env=None):
 parent=os.getpid()
 bootstrap="import ctypes,os,signal;parent="+repr(parent)+";assert ctypes.CDLL(None).prctl(1,signal.SIGKILL)==0 and os.getppid()==parent;args="+repr(argv)+";os.execv(args[0],args)"
 child=subprocess.Popen([sys.executable,"-I","-B","-c",bootstrap],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
 selector=selectors.DefaultSelector();selector.register(child.stdout,selectors.EVENT_READ);raw=bytearray()
 try:
  while True:
   left=end-time.monotonic();need(left>0 and selector.select(left),"PROCESS_CAP")
   part=os.read(child.stdout.fileno(),min(4096,limit+1-len(raw)));raw.extend(part);need(len(raw)<=limit,"PROCESS_LIMIT")
   if not part:break
  need(child.wait(timeout=max(.001,end-time.monotonic()))==0,"PROCESS_EXIT")
  return bytes(raw)
 finally:
  selector.close()
  if child.poll() is None:child.kill()
  child.wait(timeout=1);child.stdout.close()
