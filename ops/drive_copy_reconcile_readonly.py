"""Read-only reconciliation of one allocated ID with the original OAuth context."""
import json
import os
import re
import signal
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request
from ops import drive_backup_folder_readonly as ro
from ops import drive_backup_copy_once as copy

FILE_ID='1hrg64wNgiRuxF4tCRj52YDLVWHSrhQKF'
BRANCH='test/drive-copy-reconcile-20261001'
OPERATION='DRIVE_EXACT_COPY_RECONCILE_READONLY_V1'
FILE_URL=ro.API+'/files/'+FILE_ID


class Readonly(ro.Http):
    file_id=FILE_ID

    def call(self,url,*,query=None,form=None,token=None):
        # No folders/list/generateIds/session endpoints; refresh is sole POST.
        if url in (ro.MAIN_URL,ro.TOKEN_URL,ro.API+'/about'):
            return super().call(url,query=query,form=form,token=token)
        ro.need(url in (FILE_URL,FILE_URL+'/permissions') and form is None and token)
        with self.read(url,token,query) as response:
            ro.need(response.status==200)
            body=response.read(ro.LIMIT+1);ro.need(len(body)<=ro.LIMIT)
            copy.check_deadline(self.deadline)
            value=json.loads(body);ro.need(isinstance(value,dict));return value

    def read(self,url,token,query):
        ro.need(url in (FILE_URL,FILE_URL+'/permissions') and isinstance(token,str) and token)
        copy.check_deadline(self.deadline)
        request=Request(url+('?' + urlencode(query) if query else ''),method='GET',
            headers={'Authorization':'Bearer '+token,'Accept-Encoding':'identity'})
        return self.opener.open(request,timeout=min(15,self.deadline-time.monotonic()))

    def readback(self,token):
        with self.read(FILE_URL,token,{'alt':'media'}) as response:
            ro.need(response.status==200 and str(response.headers.get('Content-Length'))==str(copy.SIZE)
                    and response.headers.get('Content-Encoding','identity')=='identity')
            copy.read_cipher(response,self.deadline)


def authenticate(http,packed):
    ro.need(isinstance(packed,str) and 0<len(packed)<=32768)
    data=json.loads(packed);ro.need(isinstance(data,dict))
    values={k:data.get(k) for k in ('client_id','client_secret','refresh_token')}
    ro.need(all(isinstance(v,str) and v.strip() and not any(ord(c)<32 for c in v) for v in values.values()))
    auth=http.call(ro.TOKEN_URL,form={'grant_type':'refresh_token',**values})
    token=auth.get('access_token')
    ro.need(isinstance(token,str) and 0<len(token)<16384 and not any(ord(c)<32 for c in token)
            and str(auth.get('token_type','')).lower()=='bearer')
    user=http.call(ro.API+'/about',token=token,query={'fields':'user(emailAddress,permissionId)'})['user']
    ro.need(user.get('emailAddress')==ro.OWNER and isinstance(user.get('permissionId'),str) and user['permissionId'])
    return token,user['permissionId']


def reconcile(http,packed,row):
    row['phase']='main_before';ro.need(http.call(ro.MAIN_URL)['object']['sha']==ro.MAIN)
    row['phase']='original_oauth_identity';token,owner=authenticate(http,packed);row['identity_verified']=True
    row['phase']='exact_file_metadata_acl'
    try:
        copy.verify_file(http,token,owner)
    except HTTPError as exc:
        if exc.code==404:
            row.update(observation='NOT_FOUND_OR_NOT_VISIBLE',http_status=404)
            row['phase']='main_after';ro.need(http.call(ro.MAIN_URL)['object']['sha']==ro.MAIN)
            row.update(status='OBSERVED',phase='complete')
            return
        raise
    row.update(observation='FILE_PRESENT',metadata_acl_verified=True)
    row['phase']='exact_file_readback_hash';http.readback(token);row['readback_verified']=True
    row['phase']='metadata_acl_after';copy.verify_file(http,token,owner)
    row['phase']='main_after';ro.need(http.call(ro.MAIN_URL)['object']['sha']==ro.MAIN)
    row.update(status='PASS',phase='complete',second_copy_verified=True)


def context():
    sha=os.environ.get('EXPECTED_REVIEW','');ro.need(re.fullmatch('[0-9a-f]{40}',sha))
    exact=dict(GITHUB_REPOSITORY=ro.REPO,GITHUB_REF='refs/heads/'+BRANCH,
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',
        GITHUB_RUN_ATTEMPT='1',GITHUB_SHA=sha,GITHUB_WORKFLOW_SHA=sha,EXPECTED_MAIN=ro.MAIN,
        RECONCILE_OPERATION=OPERATION,GITHUB_WORKFLOW_REF=ro.REPO+
        '/.github/workflows/native-registry-credential-probe.yml@refs/heads/'+BRANCH)
    ro.need(all(os.environ.get(k)==v for k,v in exact.items()))
    ro.need(re.fullmatch('[0-9]+',os.environ.get('GITHUB_RUN_ID','')))
    result=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,timeout=5,
                          env={'PATH':os.environ.get('PATH',''),'LC_ALL':'C'})
    ro.need(result.returncode==0 and result.stdout.strip()==sha);return sha


def run():
    packed=os.environ.pop('GOOGLE_DRIVE_OAUTH_JSON','')
    row=dict(schema='drive-exact-copy-reconcile-v1',status='FAIL',phase='context',source_sha=None,
        file_id=FILE_ID,folder_id=ro.FOLDER,ciphertext_bytes=copy.SIZE,ciphertext_sha256=copy.DIGEST,
        observation='UNKNOWN',identity_verified=False,metadata_acl_verified=False,readback_verified=False,
        second_copy_verified=False,http_status=None,failure_class='NONE',uploads=False,new_grants=False,
        permission_changes=False,production_access=False,local_files=False)
    try:
        def expired(*args):raise ro.Refused()
        signal.signal(signal.SIGALRM,expired);signal.signal(signal.SIGTERM,expired);signal.alarm(90)
        row['source_sha']=context()
        reconcile(Readonly(time.monotonic()+80),packed,row)
    except BaseException as exc:
        row['status']='FAIL';row['failure_class']=copy.failure_class(exc)
        if isinstance(exc,HTTPError) and type(exc.code) is int and 100<=exc.code<=599:row['http_status']=exc.code
    finally:
        signal.alarm(0);packed=''
    return row


def main():
    os.umask(0o077);row=run()
    try:copy.record(row)
    except BaseException:return 2
    # OBSERVED 404 is successful diagnosis, never a successful backup or retry permission.
    return 0 if row['status'] in ('PASS','OBSERVED') else 2


if __name__=='__main__':raise SystemExit(main())
