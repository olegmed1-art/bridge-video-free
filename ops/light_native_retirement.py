"""Declarative failed-prepare retirement; never a completion, ACK or admission.

This module is stdlib-only. Authenticated controller entrypoints provide the live
reader. The exact proposal acquires meaning only through a new finite policy's
explicit hash reference and fresh verification by its consumer.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import stat

NAME = 'retire-prepare-v1.json'
RECORD_KEYS = {'version','kind','requested_disposition','policy_sha256','index','plan_sha256',
    'repository','failed_work_key','failed_target_pr','predecessor','incident_sha256',
    'historical_controller_source','historical_controller_sha256','retained_runtime_source',
    'retained_runtime_sha256','original_failure','interpretation','old_policy_may_issue',
    'failed_plan_may_replay','execution_acknowledged','new_task_authorized','admission_changed','incident_closed'}
FALSE_FLAGS = ('old_policy_may_issue','failed_plan_may_replay','execution_acknowledged',
               'new_task_authorized','admission_changed','incident_closed')
REFERENCE_KEYS = {'policy_sha256','index','record_sha256'}
JOURNAL_KEYS = {'baseline.json','before.json','contained.json','cycle-contain-intent',
    'cycle-intent','driver-contain-intent','incident.json','issuer-intent',
    'prepare.json','runtime-package.json','wheels.tar'}


def journal_pins(value):
    need(type(value) is dict and set(value)==JOURNAL_KEYS and
         all(digest(pin) for pin in value.values()), 'LANE_RETIREMENT_PINS')
    return value


class Refusal(RuntimeError):
    """Internal fixed-code refusal; public diagnostics never export its text."""
    def __init__(self,code):
        super().__init__(code)
        self.code=code


def need(ok, code='LANE_RETIREMENT_REFUSED'):
    if not ok:
        raise Refusal(code)


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw, *, limit=262144):
    def pairs(items):
        value={}
        for key,item in items:
            need(key not in value,'LANE_RETIREMENT_SCHEMA')
            value[key]=item
        return value
    need(type(raw) is bytes and 0<len(raw)<=limit,'LANE_RETIREMENT_SCHEMA')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False,'LANE_RETIREMENT_SCHEMA'))


def digest(value,n=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{'+str(n)+'}',value) is not None


def references(value):
    need(type(value) is list and len(value)<=8,'LANE_RETIREMENT_REFERENCE')
    policies=set()
    for item in value:
        need(type(item) is dict and set(item)==REFERENCE_KEYS
             and digest(item['policy_sha256']) and digest(item['record_sha256'])
             and type(item['index']) is int and 0<=item['index']<8
             and item['policy_sha256'] not in policies,'LANE_RETIREMENT_REFERENCE')
        policies.add(item['policy_sha256'])
    return value


def record(raw,accepted=None):
    need(accepted is None or digest(accepted) and sha(raw)==accepted,'LANE_RETIREMENT_NOT_ACCEPTED')
    value=parse(raw)
    need(type(value) is dict and set(value)==RECORD_KEYS and encoded(value)==raw,
         'LANE_RETIREMENT_SCHEMA')
    need(type(value['version']) is int and value['version']==1
         and value['kind']=='FAILED_PREPARE_RETIREMENT_PROPOSAL'
         and value['requested_disposition']=='RETIRE_FAILED_PREPARE_AND_CANCEL_OLD_POLICY_REMAINDER'
         and value['original_failure']=='UNKNOWN'
         and value['interpretation']=='EXPLICIT_NEW_POLICY_HASH_ALLOWLIST_AND_FRESH_GUARDS_REQUIRED'
         and all(value[k] is False for k in FALSE_FLAGS),'LANE_RETIREMENT_SCHEMA')
    need(all(digest(value[k]) for k in ('policy_sha256','plan_sha256','incident_sha256',
         'historical_controller_sha256','retained_runtime_sha256'))
         and all(digest(value[k],40) for k in ('historical_controller_source','retained_runtime_source'))
         and type(value['index']) is int and 0<=value['index']<8
         and type(value['failed_target_pr']) is int and 0<value['failed_target_pr']<=1000000
         and type(value['repository']) is str and re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',value['repository'])
         and type(value['failed_work_key']) is str and 1<=len(value['failed_work_key'])<=200,
         'LANE_RETIREMENT_SCHEMA')
    p=value['predecessor']
    need(type(p) is dict and set(p)=={'sequence','plan_sha256','terminal_sha256'}
         and type(p['sequence']) is int and 0<=p['sequence']<9999
         and digest(p['plan_sha256']) and digest(p['terminal_sha256'])
         and p['plan_sha256']!=value['plan_sha256'],'LANE_RETIREMENT_SCHEMA')
    return value


def entry(value):
    return 'issuers/'+value['policy_sha256']+'/'+format(value['index'],'04d')


def parts(path):
    need(type(path) is str and 0<len(path)<=512,'LANE_RETIREMENT_PATH')
    result=path.split('/')
    need(all(re.fullmatch('[A-Za-z0-9_.-]+',p) and p not in ('.','..') for p in result),
         'LANE_RETIREMENT_PATH')
    return result


def identity(row):
    return (row.st_dev,row.st_ino)


def metadata(row):
    return (row.st_dev,row.st_ino,row.st_mode,row.st_uid,row.st_gid,row.st_nlink,
            row.st_size,row.st_mtime_ns,row.st_ctime_ns)


class Snapshot:
    """No-follow/no-atime private root reader; capture identity now, never invent approval."""
    def __init__(self,root,output_parent):
        self.root=str(root);self.output_parent=output_parent;self.rows={};self.fd=None
    def __enter__(self):
        need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0,'LANE_RETIREMENT_ROOT')
        need(self.root.startswith('/') and self.root!='/','LANE_RETIREMENT_PATH')
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOATIME)
        try:
            for name in parts(self.root[1:]):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NOATIME,dir_fd=fd)
                os.close(fd);fd=child;row=os.fstat(fd)
                need(row.st_uid==0 and (not row.st_mode & 0o022 or row.st_mode & stat.S_ISVTX),
                     'LANE_RETIREMENT_METADATA')
            self.directory_meta(os.fstat(fd));self.fd=fd
            self.rows['.']=metadata(os.fstat(fd))
            return self
        except BaseException:
            os.close(fd);raise
    def __exit__(self,*args):
        os.close(self.fd);self.fd=None
    @staticmethod
    def directory_meta(row):
        need(stat.S_ISDIR(row.st_mode) and row.st_uid==row.st_gid==0
             and stat.S_IMODE(row.st_mode)==0o700,'LANE_RETIREMENT_METADATA')
    @staticmethod
    def file_meta(row):
        need(stat.S_ISREG(row.st_mode) and row.st_uid==row.st_gid==0
             and stat.S_IMODE(row.st_mode)==0o600 and row.st_nlink==1,'LANE_RETIREMENT_METADATA')
    @contextmanager
    def directory(self,path):
        fd=os.dup(self.fd);seen=[]
        try:
            for name in parts(path):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NOATIME,dir_fd=fd)
                os.close(fd);fd=child;seen.append(name);row=os.fstat(fd);self.directory_meta(row)
                key='/'.join(seen)
                # Only the intentional output parent may change timestamps/size.
                value=metadata(row)[:6] if key==self.output_parent else metadata(row)
                previous=self.rows.setdefault(key+'/',value)
                need(previous==value,'LANE_RETIREMENT_DRIFT')
            yield fd
        finally:
            os.close(fd)
    def read(self,path,pin=None,*,optional=False,limit=262144,output=False):
        names=parts(path)
        need(len(names)>1,'LANE_RETIREMENT_PATH')
        with self.directory('/'.join(names[:-1])) as parent:
            try:
                fd=os.open(names[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_NOATIME,dir_fd=parent)
            except FileNotFoundError:
                need(optional,'LANE_RETIREMENT_MISSING')
                if not output:need(self.rows.setdefault(path,None) is None,'LANE_RETIREMENT_DRIFT')
                return None
            try:
                before=os.fstat(fd);self.file_meta(before)
                need(before.st_size<=limit,'LANE_RETIREMENT_SIZE')
                chunks=[];left=before.st_size+1
                while left:
                    chunk=os.read(fd,min(left,65536))
                    if not chunk:break
                    chunks.append(chunk);left-=len(chunk)
                raw=b''.join(chunks);after=os.fstat(fd)
                need(metadata(before)==metadata(after) and len(raw)==before.st_size
                     and identity(os.stat(names[-1],dir_fd=parent,follow_symlinks=False))==identity(before),
                     'LANE_RETIREMENT_DRIFT')
                need(pin is None or sha(raw)==pin,'LANE_RETIREMENT_NOT_ACCEPTED')
                if not output:
                    value=(metadata(before),sha(raw))
                    need(self.rows.setdefault(path,value)==value,'LANE_RETIREMENT_DRIFT')
                return raw
            finally:
                os.close(fd)
    def names(self,path,*,allow_output=False):
        with self.directory(path) as fd:
            names=set(os.listdir(fd))
            if allow_output:
                need(path==self.output_parent,'LANE_RETIREMENT_PATH')
                names.discard(NAME)
            value=tuple(sorted(names))
            need(self.rows.setdefault(path+'/*',value)==value,'LANE_RETIREMENT_DRIFT')
            return names
    def check(self):
        """Re-open from /, recheck original bytes/inodes and every recorded inventory."""
        with Snapshot(self.root,self.output_parent) as current:
            for key,value in list(self.rows.items()):
                if key=='.':continue
                if key.endswith('/*'):
                    current.names(key[:-2],allow_output=key[:-2]==self.output_parent)
                elif key.endswith('/'):
                    with current.directory(key[:-1]):pass
                else:
                    current.read(key,None if value is None else value[1],optional=value is None,
                                 limit=16*1024*1024)
            need(current.rows==self.rows,'LANE_RETIREMENT_DRIFT')


def write_proposal(root,raw,accepted,observe):
    """Exact create-only data; observe(snapshot,record) is reviewed live code.

    The authenticated entrypoint owns issuer/cycle locks and a hard deadline.
    A failure after O_EXCL is UNKNOWN, and no cleanup or automatic retry occurs.
    """
    value=record(raw,accepted);parent_path=entry(value);path=parent_path+'/'+NAME
    with Snapshot(root,parent_path) as view:
        existing=view.read(path,optional=True,output=True)
        if existing is not None:
            view.read(path)  # retain exact inode/metadata as an original, not an output
        need(existing is None or existing==raw,'LANE_RETIREMENT_PARTIAL_OR_CONFLICT')
        before=observe(view,value)
        view.check()
        need(observe(view,value)==before,'LANE_RETIREMENT_DRIFT')
        view.check()
        if existing is not None:
            # Read-only reconciliation, not completion/repair of a previous write.
            need(view.read(path)==raw,'LANE_RETIREMENT_DRIFT')
            return {'state':'PROPOSAL_PRESENT_UNACCEPTED','record_sha256':accepted}
        try:
            with view.directory(parent_path) as parent:
                fd=os.open(NAME,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
                try:
                    row=os.fstat(fd);view.file_meta(row);written=identity(row)
                    left=memoryview(raw)
                    while left:
                        count=os.write(fd,left);need(count>0,'LANE_RETIREMENT_UNKNOWN');left=left[count:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(parent)
                need(view.read(path,output=True)==raw,'LANE_RETIREMENT_DRIFT')
                need(observe(view,value)==before,'LANE_RETIREMENT_DRIFT');view.check()
                need(view.read(path,output=True)==raw and
                     identity(os.stat(NAME,dir_fd=parent,follow_symlinks=False))==written,'LANE_RETIREMENT_DRIFT')
        except BaseException:
            raise RuntimeError('LANE_RETIREMENT_UNKNOWN') from None
    return {'state':'PROPOSAL_RETAINED_UNACCEPTED','record_sha256':accepted}
