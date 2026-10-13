"""Offline protocol fixture: never imports or calls a network/client API."""
import sys
def deny(event,args):
    if event.startswith(('socket.','subprocess.','os.exec','os.spawn','os.posix_spawn','ctypes.')) or event in ('os.fork','os.system'):
        raise RuntimeError('offline_only')
sys.addaudithook(deny)
import struct,time
if len(sys.argv)!=2 or sys.argv[1] not in ('success','hang','malformed','late_exit'):
    raise SystemExit(2)
mode=sys.argv[1]
if mode=='hang':
    time.sleep(30)
elif mode=='malformed':
    sys.stdout.buffer.write(b'\xff'*15);sys.stdout.buffer.flush()
    time.sleep(30)
else:
    frame=struct.Struct('!BBdBBBBB')
    for code,counts in ((1,(0,0,0,0,0)),(2,(1,0,0,0,0)),(3,(1,1,0,0,0)),(4,(1,1,1,0,8))):
        sys.stdout.buffer.write(frame.pack(1,code,time.monotonic(),*counts))
    sys.stdout.buffer.write(frame.pack(2,1,time.monotonic(),1,1,1,1,8))
    sys.stdout.buffer.flush()
    if mode=='late_exit':time.sleep(30)
