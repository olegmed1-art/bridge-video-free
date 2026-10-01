"""Approval-gated copy of ONE existing ciphertext. No DB access or decryption."""
import hashlib
import json
import os
import re
import signal
import subprocess
import time
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request
from urllib.error import HTTPError

from ops import drive_backup_folder_readonly as ro
from ops.oci_backup_bucket_metadata import credential_config, make_client, BUCKET, TENANCY

BRANCH='test/drive-backup-copy-review-20261001'
OPERATION='DRIVE_EXISTING_CIPHERTEXT_COPY_V1'
SOURCE_SHA='86ebfda8545e23dc497cd4fa375deadd7f8e368e'
SOURCE_RUN='36908992530'
KEY='neon-backups/v1/'+SOURCE_RUN+'-1-'+SOURCE_SHA+'.dump.enc'
NAME=KEY.rsplit('/',1)[1]
SIZE=8238960
DIGEST='5878d3eef5f63753b39be3b247057c7694e1b381b3b5854832fbf14c085a48af'
NAMESPACE='frzcdnwzijyf'
UPLOAD='https://www.googleapis.com/upload/drive/v3/files'
FIELDS='id,name,mimeType,size,parents,shared,trashed,ownedByMe,driveId,owners(emailAddress,permissionId,me)'
need=ro.need


def check_deadline(deadline):
    need(time.monotonic()<deadline)


def read_cipher(stream,deadline,*,retain=False):
    digest=hashlib.sha256(); count=0; chunks=[]; prefix=b''
    while True:
        check_deadline(deadline)
        block=stream.read(min(65536,SIZE-count+1))
        check_deadline(deadline)
        need(isinstance(block,bytes))
        if not block: break
        prefix=(prefix+block)[:8]
        count+=len(block); need(count<=SIZE)
        digest.update(block)
        if retain: chunks.append(block)
    need(count==SIZE and digest.hexdigest()==DIGEST and prefix==b'Salted__')
    return b''.join(chunks) if retain else None


def source_bytes(client,deadline):
    check_deadline(deadline)
    need(client.get_namespace(compartment_id=TENANCY).data==NAMESPACE)
    bucket=client.get_bucket(namespace_name=NAMESPACE,bucket_name=BUCKET).data
    need(all(getattr(bucket,k,None)==v for k,v in dict(name=BUCKET,namespace=NAMESPACE,
        compartment_id=TENANCY,public_access_type='NoPublicAccess',storage_tier='Standard').items()))
    check_deadline(deadline)
    response=client.get_object(namespace_name=NAMESPACE,bucket_name=BUCKET,object_name=KEY)
    try:
        need(response.status==200 and str(response.headers.get('content-length'))==str(SIZE)
             and response.headers.get('content-encoding','identity')=='identity')
        response.data.raw.decode_content=False
        return read_cipher(response.data.raw,deadline,retain=True)
    finally:
        response.data.close()


def session_url(url):
    need(isinstance(url,str) and len(url)<8192)
    parts=urlsplit(url); query=parse_qs(parts.query,keep_blank_values=True,strict_parsing=True)
    need(parts.scheme=='https' and parts.netloc=='www.googleapis.com'
         and parts.path=='/upload/drive/v3/files' and not parts.fragment
         and set(query)=={'uploadType','upload_id'} and query['uploadType']==['resumable']
         and len(query['upload_id'])==1 and re.fullmatch('[A-Za-z0-9_-]{1,4096}',query['upload_id'][0]))
    return url


class Drive(ro.Http):
    def __init__(self,deadline):
        super().__init__(deadline)
        self.file_id=None; self.session=None; self.initiated=False; self.put=False

    def call(self,url,*,query=None,form=None,token=None):
        extra={ro.API+'/files/generateIds',ro.API+'/files'}
        if self.file_id: extra.update({ro.API+'/files/'+self.file_id,ro.API+'/files/'+self.file_id+'/permissions'})
        if url not in extra: return super().call(url,query=query,form=form,token=token)
        need(form is None and isinstance(token,str) and token)
        with self.open(url,token=token,query=query) as response:
            need(response.status==200)
            return self.json(response)

    def open(self,url,*,token,query=None,method='GET',body=None,headers=None):
        allowed_get={ro.API+'/files/generateIds',ro.API+'/files'}
        if self.file_id: allowed_get.update({ro.API+'/files/'+self.file_id,ro.API+'/files/'+self.file_id+'/permissions'})
        need((method=='GET' and url in allowed_get and body is None)
             or (method=='POST' and url==UPLOAD and self.initiated and not self.put)
             or (method=='PUT' and url==self.session and self.put))
        check_deadline(self.deadline)
        values={'Authorization':'Bearer '+token,'Accept-Encoding':'identity',**(headers or {})}
        request=Request(url+('?' + urlencode(query) if query else ''),data=body,method=method,headers=values)
        return self.opener.open(request,timeout=min(30,self.deadline-time.monotonic()))

    def json(self,response):
        body=response.read(ro.LIMIT+1); need(len(body)<=ro.LIMIT)
        check_deadline(self.deadline)
        value=json.loads(body); need(isinstance(value,dict)); return value

    def allocate(self,token):
        need(self.file_id is None)
        result=self.call(ro.API+'/files/generateIds',token=token,query={'count':'1','space':'drive','type':'files'})
        ids=result.get('ids'); need(isinstance(ids,list) and len(ids)==1
                                  and isinstance(ids[0],str) and re.fullmatch('[A-Za-z0-9_-]{1,200}',ids[0]))
        self.file_id=ids[0]; return self.file_id

    def create(self,token,cipher,receipt):
        receipt.update(create_stage='LOCAL_CHECK',http_status=None,failure_class='NONE',
                       initiation_accepted=False,session_validated=False,put_attempted=False,
                       final_response_accepted=False)
        try:
            self._create(token,cipher,receipt)
        except BaseException as exc:
            if isinstance(exc,HTTPError):
                receipt['http_status']=exc.code if type(exc.code) is int and 100<=exc.code<=599 else None
            receipt['failure_class']=failure_class(exc)
            raise

    def _create(self,token,cipher,receipt):
        need(self.file_id and not self.initiated and not self.put)
        need(len(cipher)==SIZE and hashlib.sha256(cipher).hexdigest()==DIGEST)
        body=json.dumps(dict(id=self.file_id,name=NAME,mimeType='application/octet-stream',
            parents=[ro.FOLDER],appProperties={'sourceRun':SOURCE_RUN,'ciphertextSha256':DIGEST})).encode()
        check_deadline(self.deadline)
        # Initiation may mutate remote state; receipt must already be durable.
        self.initiated=True; receipt['create_outcome']='UNKNOWN'
        receipt['create_stage']='INIT_POST'
        with self.open(UPLOAD,token=token,method='POST',query={'uploadType':'resumable'},body=body,
            headers={'Content-Type':'application/json; charset=UTF-8',
                     'X-Upload-Content-Type':'application/octet-stream','X-Upload-Content-Length':str(SIZE)}) as response:
            receipt['http_status']=response.status if type(response.status) is int else None
            need(response.status==200); receipt['initiation_accepted']=True
            receipt['create_stage']='SESSION_VALIDATION'
            self.session=session_url(response.headers.get('Location'))
            receipt['session_validated']=True
        check_deadline(self.deadline)
        self.put=True
        receipt.update(create_stage='CONTENT_PUT',put_attempted=True,http_status=None)
        with self.open(self.session,token=token,method='PUT',body=cipher,
            headers={'Content-Type':'application/octet-stream','Content-Length':str(SIZE)}) as response:
            receipt['http_status']=response.status if type(response.status) is int else None
            need(response.status in (200,201)); receipt['create_stage']='FINAL_REPLY'
            value=self.json(response); need(value.get('id')==self.file_id)
            receipt['final_response_accepted']=True
        receipt['create_outcome']='CONFIRMED'
        receipt['create_stage']='COMPLETE'

    def readback(self,token):
        with self.open(ro.API+'/files/'+self.file_id,token=token,query={'alt':'media'}) as response:
            need(response.status==200 and str(response.headers.get('Content-Length'))==str(SIZE)
                 and response.headers.get('Content-Encoding','identity')=='identity')
            read_cipher(response,self.deadline)


def fresh_destination(http,token,owner):
    need(http.call(ro.MAIN_URL)['object']['sha']==ro.MAIN)
    folder=ro.metadata(http,token,ro.FOLDER,owner,parent=ro.PARENT)
    need(folder.get('capabilities',{}).get('canAddChildren') is True)
    ro.owner_acl(http,token,ro.FOLDER,owner)
    ro.metadata(http,token,ro.PARENT,owner); ro.owner_acl(http,token,ro.PARENT,owner)


def failure_class(exc):
    # Fixed allowlist only: no exception strings, URLs, headers or response bodies.
    if isinstance(exc,HTTPError): return 'HTTP_ERROR'
    if isinstance(exc,ro.Refused): return 'GUARD_REFUSED'
    if isinstance(exc,json.JSONDecodeError): return 'JSON_INVALID'
    if isinstance(exc,TimeoutError): return 'TIMEOUT'
    if isinstance(exc,OSError): return 'TRANSPORT_OR_OS_ERROR'
    return 'OTHER_FAILURE'


def verify_file(http,token,owner):
    value=http.call(ro.API+'/files/'+http.file_id,token=token,query={'fields':FIELDS})
    expected=dict(id=http.file_id,name=NAME,mimeType='application/octet-stream',size=str(SIZE),
                  parents=[ro.FOLDER],shared=False,trashed=False,ownedByMe=True)
    need(all(value.get(k)==v for k,v in expected.items()) and not value.get('driveId'))
    owners=value.get('owners'); need(isinstance(owners,list) and len(owners)==1)
    need(owners[0].get('emailAddress')==ro.OWNER and owners[0].get('permissionId')==owner
         and owners[0].get('me') is True)
    ro.owner_acl(http,token,http.file_id,owner)


def record(row):
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as output:
        output.write('```json\n'+json.dumps(row,sort_keys=True)+'\n```\n'); output.flush(); os.fsync(output.fileno())


def transfer(client,http,packed,row):
    probe={'gates':{}}
    row['phase']='drive_preflight'; token,owner=ro.observe(http,packed,probe)
    need(probe['existing_write_scope']=='PRESENT')
    row['phase']='oci_read_hash'; cipher=source_bytes(client,http.deadline); row['source_verified']=True
    try:
        row['phase']='duplicate_guard'
        # Same exact ciphertext name in this folder is a stop, not an overwrite.
        found=http.call(ro.API+'/files',token=token,query={'q':"'"+ro.FOLDER+"' in parents and trashed = false and name = '"+NAME+"'",
                        'pageSize':'100','fields':'nextPageToken,files(id)','spaces':'drive'})
        need(found.get('files')==[] and not found.get('nextPageToken'))
        row['phase']='allocate_id'; row['drive_file_id']=http.allocate(token)
        row['phase']='last_preflight'; fresh_destination(http,token,owner)
        row['phase']='create_intent'; row['create_outcome']='UNKNOWN'
        try: record(row)
        except BaseException:
            row['create_outcome']='NOT_ATTEMPTED'; raise
        row['phase']='create'; http.create(token,cipher,row)
    finally:
        cipher=None
    row['phase']='file_acl'; verify_file(http,token,owner)
    row['phase']='drive_readback_hash'; http.readback(token); row['readback_verified']=True
    row['phase']='privacy_after'; verify_file(http,token,owner); fresh_destination(http,token,owner)
    row.update(status='PASS',phase='complete',second_copy_verified=True)


def context():
    sha=os.environ.get('EXPECTED_REVIEW',''); need(re.fullmatch('[0-9a-f]{40}',sha))
    exact=dict(GITHUB_REPOSITORY=ro.REPO,GITHUB_REF='refs/heads/'+BRANCH,GITHUB_EVENT_NAME='workflow_dispatch',
        GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',GITHUB_RUN_ATTEMPT='1',
        GITHUB_SHA=sha,GITHUB_WORKFLOW_SHA=sha,EXPECTED_MAIN=ro.MAIN,COPY_OPERATION=OPERATION,
        COPY_APPROVAL=OPERATION+':'+sha+':one-create-approved',GITHUB_WORKFLOW_REF=ro.REPO+
        '/.github/workflows/native-registry-credential-probe.yml@refs/heads/'+BRANCH)
    need(all(os.environ.get(k)==v for k,v in exact.items()))
    need(re.fullmatch('[0-9]+',os.environ.get('GITHUB_RUN_ID','')))
    result=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,timeout=5,
                          env={'PATH':os.environ.get('PATH',''),'LC_ALL':'C'})
    need(result.returncode==0 and result.stdout.strip()==sha)
    wall=time.time(); start=int(os.environ['COPY_STARTED']); end=int(os.environ['COPY_DEADLINE'])
    need(0<=wall-start<300 and wall<end<=start+300)
    return sha,min(240,end-wall-15,285-(wall-start))


def run():
    packed=os.environ.pop('GOOGLE_DRIVE_OAUTH_JSON','')
    credentials={key:os.environ.pop(key,'') for key in ('OCI_CLI_'+x for x in ('USER','TENANCY','FINGERPRINT','KEY_CONTENT','REGION'))}
    row=dict(schema='drive-existing-ciphertext-copy-v1',status='FAIL',phase='context',source_sha=None,
        source_run=SOURCE_RUN,namespace=NAMESPACE,bucket=BUCKET,object_key=KEY,ciphertext_bytes=SIZE,
        ciphertext_sha256=DIGEST,folder_id=ro.FOLDER,drive_file_id=None,create_outcome='NOT_ATTEMPTED',
        source_verified=False,readback_verified=False,second_copy_verified=False,
        production_access=False,new_grants=False,permission_changes=False,remote_delete=False,
        plaintext=False,local_files=False)
    try:
        row['source_sha'],seconds=context(); need(seconds>0)
        def expired(*args): raise ro.Refused()
        signal.signal(signal.SIGALRM,expired); signal.signal(signal.SIGTERM,expired)
        signal.setitimer(signal.ITIMER_REAL,seconds)
        config,key=credential_config(credentials); credentials.clear()
        client=make_client(config,key)
        transfer(client,Drive(time.monotonic()+seconds),packed,row)
    except BaseException:
        row['status']='FAIL'
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        credentials.clear(); packed=''
    return row


def main():
    os.umask(0o077)
    row=run()
    try: record(row)
    except BaseException: return 2
    return 0 if row['status']=='PASS' else 2


if __name__=='__main__': raise SystemExit(main())
