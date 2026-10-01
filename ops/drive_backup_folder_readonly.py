"""Exact-folder metadata probe using existing user OAuth, no writes or new grants."""
import json
import os
import re
import signal
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

REPO='olegmed1-art/bridge-video-free'
BRANCH='test/drive-backup-folder-readonly-20261001'
MAIN='1440920191e1778fb9a9ba24e6701937a1a7459c'
OPERATION='DRIVE_BACKUP_FOLDER_READONLY_V1'
FOLDER='1mS9eoLAIX4yMsKI_Ale_TM6Xl478ULqc'
PARENT='1uzpcT49YcV34gcv_Jo2U86a9qHUuqPpj'
OWNER='olegmed1@gmail.com'
API='https://www.googleapis.com/drive/v3'
TOKEN_URL='https://oauth2.googleapis.com/token'
MAIN_URL='https://api.github.com/repos/'+REPO+'/git/ref/heads/main'
LIMIT=65536


class Refused(Exception):
    pass


def need(value):
    if not value:
        raise Refused()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise Refused()


class Http:
    def __init__(self, deadline):
        self.deadline=deadline
        self.opener=build_opener(ProxyHandler({}),NoRedirect())

    def call(self,url,*,query=None,form=None,token=None):
        # Explicit method/URL allowlist; token refresh is the sole POST.
        reads={MAIN_URL,API+'/about',*(API+'/files/'+x for x in (FOLDER,PARENT)),
               *(API+'/files/'+x+'/permissions' for x in (FOLDER,PARENT))}
        need((url==TOKEN_URL and form is not None and token is None and query is None)
             or (url in reads and form is None and (token is None)==(url==MAIN_URL)))
        remaining=self.deadline-time.monotonic(); need(remaining>0)
        headers={'Accept':'application/json','User-Agent':'bridge-drive-readonly-probe'}
        if token is not None: headers['Authorization']='Bearer '+token
        if form is not None: headers['Content-Type']='application/x-www-form-urlencoded'
        request=Request(url+('?' + urlencode(query) if query else ''),
            data=urlencode(form).encode() if form is not None else None,headers=headers,
            method='POST' if form is not None else 'GET')
        with self.opener.open(request,timeout=min(10,remaining)) as response:
            need(response.status==200)
            body=response.read(LIMIT+1)
        need(len(body)<=LIMIT and time.monotonic()<self.deadline)
        result=json.loads(body); need(isinstance(result,dict))
        return result


def context():
    sha=os.environ.get('EXPECTED_REVIEW','')
    need(re.fullmatch('[0-9a-f]{40}',sha) is not None)
    exact=dict(GITHUB_REPOSITORY=REPO,GITHUB_REF='refs/heads/'+BRANCH,
        GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_ACTOR='olegmed1-art',
        GITHUB_TRIGGERING_ACTOR='olegmed1-art',GITHUB_RUN_ATTEMPT='1',GITHUB_SHA=sha,
        GITHUB_WORKFLOW_SHA=sha,GITHUB_WORKFLOW_REF=REPO+
        '/.github/workflows/native-registry-credential-probe.yml@refs/heads/'+BRANCH,
        EXPECTED_MAIN=MAIN,DRIVE_PROBE_OPERATION=OPERATION)
    need(all(os.environ.get(k)==v for k,v in exact.items()))
    need(re.fullmatch('[0-9]+',os.environ.get('GITHUB_RUN_ID','')) is not None)
    result=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,timeout=5,
                          env={'PATH':os.environ.get('PATH',''),'LC_ALL':'C'})
    need(result.returncode==0 and result.stdout.strip()==sha)
    return sha


def metadata(http,token,identifier,permission_id,*,parent=None):
    row=http.call(API+'/files/'+identifier,token=token,query={'fields':
        'id,mimeType,trashed,parents,shared,driveId,ownedByMe,owners(emailAddress,permissionId,me),capabilities(canAddChildren)'})
    need(row.get('id')==identifier and row.get('mimeType')=='application/vnd.google-apps.folder'
         and row.get('trashed') is False and row.get('shared') is False
         and row.get('ownedByMe') is True and not row.get('driveId'))
    owners=row.get('owners')
    need(isinstance(owners,list) and len(owners)==1 and owners[0].get('emailAddress')==OWNER
         and owners[0].get('permissionId')==permission_id and owners[0].get('me') is True)
    if parent is not None: need(row.get('parents')==[parent])
    return row


def owner_acl(http,token,identifier,permission_id):
    row=http.call(API+'/files/'+identifier+'/permissions',token=token,query={
        'pageSize':'100','fields':'nextPageToken,permissions(id,type,role,emailAddress,deleted,pendingOwner,permissionDetails)'})
    # One owner requires one complete page. Any pagination/omission is not PASS.
    need(not row.get('nextPageToken'))
    permissions=row.get('permissions'); need(isinstance(permissions,list) and len(permissions)==1)
    p=permissions[0]
    need(p.get('id')==permission_id and p.get('type')=='user' and p.get('role')=='owner'
         and p.get('emailAddress')==OWNER and p.get('deleted',False) is False
         and p.get('pendingOwner',False) is False)


def observe(http,packed,receipt):
    receipt['phase']='main_before'; need(http.call(MAIN_URL)['object']['sha']==MAIN)
    receipt['phase']='credential_format'
    need(isinstance(packed,str) and 0<len(packed)<=32768)
    data=json.loads(packed); need(isinstance(data,dict))
    values={k:data.get(k) for k in ('client_id','client_secret','refresh_token')}
    need(all(isinstance(v,str) and v.strip() and not any(ord(c)<32 for c in v) for v in values.values()))
    receipt['phase']='existing_oauth_refresh'
    # No scope parameter: reuse grants already held by this refresh token.
    auth=http.call(TOKEN_URL,form={'grant_type':'refresh_token',**values})
    token=auth.get('access_token')
    need(isinstance(token,str) and 0<len(token)<16384 and not any(ord(c)<32 for c in token)
         and str(auth.get('token_type','')).lower()=='bearer')
    scope=auth.get('scope',''); need(isinstance(scope,str))
    receipt['existing_write_scope']='PRESENT' if set(scope.split()) & {
        'https://www.googleapis.com/auth/drive','https://www.googleapis.com/auth/drive.file'} else 'NOT_PROVEN'
    receipt['phase']='oauth_identity'
    user=http.call(API+'/about',token=token,query={'fields':'user(emailAddress,permissionId)'})['user']
    need(user.get('emailAddress')==OWNER and isinstance(user.get('permissionId'),str) and user['permissionId'])
    receipt['gates']['identity']='PASS'
    receipt['phase']='folder_identity'
    folder=metadata(http,token,FOLDER,user['permissionId'],parent=PARENT)
    receipt['gates']['folder']='PASS'
    receipt['phase']='can_add_children'
    need(folder.get('capabilities',{}).get('canAddChildren') is True)
    receipt['gates']['canAddChildren']='PASS'
    receipt['phase']='folder_acl'; owner_acl(http,token,FOLDER,user['permissionId'])
    receipt['gates']['folder_owner_only']='PASS'
    receipt['phase']='parent_identity'; metadata(http,token,PARENT,user['permissionId'])
    receipt['phase']='parent_acl'; owner_acl(http,token,PARENT,user['permissionId'])
    receipt['gates']['parent_owner_only']='PASS'
    receipt['phase']='main_after'; need(http.call(MAIN_URL)['object']['sha']==MAIN)
    receipt.update(status='PASS',phase='complete',failure_code='NONE')


def run():
    packed=os.environ.pop('GOOGLE_DRIVE_OAUTH_JSON','')
    receipt=dict(schema='drive-backup-folder-readonly-v1',status='FAIL',phase='context',
        failure_code='READONLY_PROBE_REFUSED',source_sha=None,folder_id=FOLDER,parent_id=PARENT,
        uploads=False,new_grants=False,permission_changes=False,existing_write_scope='NOT_PROVEN',
        gates={k:'NOT_PROVEN' for k in ('identity','folder','canAddChildren','folder_owner_only','parent_owner_only')})
    try:
        def expired(*args): raise Refused()
        signal.signal(signal.SIGALRM,expired); signal.signal(signal.SIGTERM,expired)
        signal.alarm(90)
        receipt['source_sha']=context()
        observe(Http(time.monotonic()+80),packed,receipt)
    except BaseException:
        receipt['status']='FAIL'
    finally:
        signal.alarm(0)
    return receipt


def main():
    receipt=run()
    try:
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as stream:
            stream.write('```json\n'+json.dumps(receipt,sort_keys=True)+'\n```\n')
    except BaseException:
        return 2
    return 0 if receipt['status']=='PASS' else 2


if __name__=='__main__': raise SystemExit(main())
