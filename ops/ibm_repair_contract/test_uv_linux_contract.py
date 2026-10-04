"""Isolated ephemeral CI fixture only; never loads the production UV unit."""
import hashlib,json,os,stat,subprocess,sys,tempfile,time,unittest,uuid
if sys.platform=='linux':import pwd
from pathlib import Path

def command(argv):
 r=subprocess.run(argv,capture_output=True,text=True,timeout=12)
 if r.returncode:raise AssertionError({'command':argv[0:2],'returncode':r.returncode,'stderr':r.stderr[:1000]})
 return r.stdout

@unittest.skipUnless(sys.platform=='linux','real isolated Linux namespace contract')
class RuntimeDirectoryContract(unittest.TestCase):
 def setUp(self):
  self.assertEqual(os.geteuid(),0,'Run only in isolated CI as root')
  self.account=pwd.getpwnam('nobody');self.units=[]
 def tearDown(self):
  # Stop only our recorded random transient fixture units. Preserve directories/data.
  for unit in self.units:command(['/usr/bin/systemctl','stop','--',unit])
 def fixture(self):
  name='ibm-repair-contract-'+uuid.uuid4().hex
  return name,Path('/run')/name,name+'.service'
 def show(self,unit):
  raw=command(['/usr/bin/systemctl','show','--no-pager','--property=ActiveState,SubState,ExecMainCode,ExecMainStatus,Result','--',unit])
  return dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
 def wait(self,unit):
  deadline=time.monotonic()+8
  while time.monotonic()<deadline:
   row=self.show(unit)
   if row.get('ActiveState') in ('active','failed'):return row
   time.sleep(.02)
  self.fail('Transient fixture did not reach bounded terminal state')
 def start(self,unit,path,argv,runtime=None):
  assert unit.startswith('ibm-repair-contract-') and path.parent==Path('/run')
  args=['/usr/bin/systemd-run','--quiet','--unit='+unit,'--property=Type=oneshot','--property=RemainAfterExit=yes',
        '--property=User=root','--property=Group=root','--property=ProtectSystem=strict',
        '--property=NoNewPrivileges=yes','--property=PrivateTmp=yes','--property=ReadWritePaths='+str(path)]
  if runtime:args+=['--property=RuntimeDirectory='+runtime,'--property=RuntimeDirectoryMode=0750','--property=RuntimeDirectoryPreserve=yes']
  command(args+['--',*argv]);self.units.append(unit);return self.wait(unit)
 def test_missing_required_path_fails_namespace_before_command(self):
  name,path,unit=self.fixture();self.assertFalse(path.exists())
  row=self.start(unit,path,['/usr/bin/test','-d',str(path)])
  self.assertEqual((row['ActiveState'],row['Result'],row['ExecMainStatus']),('failed','exit-code','226'))
  self.assertFalse(path.exists())
 def test_runtime_directory_root_can_reown_preserved_payload(self):
  name,path,unit=self.fixture()
  argv=['/usr/bin/install','-d','-o',str(self.account.pw_uid),'-g',str(self.account.pw_gid),'-m','0750',str(path)]
  row=self.start(unit,path,argv,runtime=name);self.assertEqual(row['Result'],'success')
  payload=path/'fixture-payload';payload.write_bytes(b'unchanged fixture bytes');os.chown(payload,self.account.pw_uid,self.account.pw_gid)
  self.assertEqual(path.stat().st_uid,self.account.pw_uid)
  before=hashlib.sha256(payload.read_bytes()).hexdigest()
  command(['/usr/bin/systemctl','stop','--',unit]);self.assertTrue(payload.exists())
  command(['/usr/bin/systemctl','start','--',unit]);self.assertEqual(self.wait(unit)['Result'],'success')
  self.assertEqual(path.stat().st_uid,self.account.pw_uid) # install restores only directory owner
  self.assertEqual(payload.stat().st_uid,0) # RuntimeDirectory changed preserved child owner
  self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(),before)
 def test_scoped_tmpfiles_prepares_exact_owner_without_reowning_payload(self):
  name,path,unit=self.fixture()
  with tempfile.TemporaryDirectory(prefix='ibm-repair-contract-') as temp:
   config=Path(temp)/'fixture.conf'
   config.write_text('d '+str(path)+' 0750 '+str(self.account.pw_uid)+' '+str(self.account.pw_gid)+' - -\n')
   args=['/usr/bin/systemd-tmpfiles','--create','--prefix='+str(path),str(config)]
   command(args);st=path.stat();self.assertEqual((st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode)),(self.account.pw_uid,self.account.pw_gid,0o750))
   payload=path/'fixture-payload';payload.write_bytes(b'preserved fixture bytes');os.chown(payload,self.account.pw_uid,self.account.pw_gid)
   identity=(payload.stat().st_ino,payload.stat().st_uid,payload.stat().st_gid,hashlib.sha256(payload.read_bytes()).hexdigest())
   self.assertEqual(self.start(unit,path,['/usr/bin/test','-d',str(path)])['Result'],'success')
   command(['/usr/bin/systemctl','stop','--',unit]);command(args);command(['/usr/bin/systemctl','start','--',unit]);self.assertEqual(self.wait(unit)['Result'],'success')
   after=(payload.stat().st_ino,payload.stat().st_uid,payload.stat().st_gid,hashlib.sha256(payload.read_bytes()).hexdigest())
   self.assertEqual(after,identity);self.assertTrue(payload.exists())

if __name__=='__main__':unittest.main(verbosity=2)
