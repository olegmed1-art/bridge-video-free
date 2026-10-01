"""One separately approved same-ID recovery, never a new ID or an update."""
import os
from urllib.error import HTTPError
from ops import drive_backup_copy_once as c
from ops import drive_backup_folder_readonly as ro
from ops.drive_copy_reconcile_readonly import FILE_ID

BRANCH='test/drive-session-query-compat-20261001'
OPERATION='DRIVE_SAME_ID_QUERY_COMPAT_RECOVERY_V1'


class SameIdDrive(c.Drive):
    def __init__(self,deadline):
        super().__init__(deadline)
        self.file_id=FILE_ID;self.post_count=0;self.put_count=0

    def allocate(self,token):
        raise ro.Refused()

    def call(self,url,**kwargs):
        ro.need(url not in (ro.API+'/files',ro.API+'/files/generateIds'))
        return super().call(url,**kwargs)

    def open(self,url,**kwargs):
        ro.need(self.file_id==FILE_ID and url not in (ro.API+'/files',ro.API+'/files/generateIds'))
        method=kwargs.get('method','GET')
        if method=='POST':
            ro.need(self.post_count==0);self.post_count+=1
        if method=='PUT':
            ro.need(self.put_count==0);self.put_count+=1
        return super().open(url,**kwargs)


def exists(http,token):
    # Only exact files.get 404 allows proceeding; ACL404 or other errors do not.
    try:
        value=http.call(ro.API+'/files/'+FILE_ID,token=token,query={'fields':'id'})
        ro.need(value.get('id')==FILE_ID);return True
    except HTTPError as exc:
        if exc.code==404:return False
        raise


def verify(http,token,owner,row):
    c.verify_file(http,token,owner)
    row['phase']='readback_hash';http.readback(token);row['readback_verified']=True
    row['phase']='privacy_after';c.verify_file(http,token,owner);c.fresh_destination(http,token,owner)
    row.update(status='PASS',phase='complete',second_copy_verified=True)


def transfer(client,http,packed,row):
    row.update(schema='drive-same-id-recovery-v1',drive_file_id=FILE_ID,recovery_outcome='NOT_STARTED',
               create_stage='NOT_STARTED',http_status=None,failure_class='NONE',put_attempted=False)
    try:
        probe={'gates':{}}
        row['phase']='original_oauth_preflight';token,owner=ro.observe(http,packed,probe)
        ro.need(probe['existing_write_scope']=='PRESENT')
        row['phase']='existing_id_before'
        if exists(http,token):
            row['recovery_outcome']='EXISTING_FILE_READONLY';verify(http,token,owner,row);return
        row['phase']='oci_read_hash';cipher=c.source_bytes(client,http.deadline);row['source_verified']=True
        try:
            row['phase']='last_preflight';c.fresh_destination(http,token,owner)
            row['phase']='existing_id_last'
            if exists(http,token):
                row['recovery_outcome']='EXISTING_FILE_READONLY';verify(http,token,owner,row);return
            row.update(phase='create_intent',create_outcome='UNKNOWN',recovery_outcome='RECOVERY_ATTEMPT')
            try:c.record(row)
            except BaseException:
                row['create_outcome']='NOT_ATTEMPTED';raise
            row['phase']='same_id_create';http.create(token,cipher,row)
        finally:
            cipher=None
        row['phase']='metadata_acl';verify(http,token,owner,row)
        row['recovery_outcome']='RECOVERED_VERIFIED'
    except HTTPError as exc:
        row['http_status']=exc.code if type(exc.code) is int and 100<=exc.code<=599 else None
        row['failure_class']='HTTP_ERROR'
        if exc.code==409:row['recovery_outcome']='CONFLICT_STOP'
        raise
    except BaseException as exc:
        row['failure_class']=c.failure_class(exc);raise


def context():return c.context(branch=BRANCH,operation=OPERATION)


def main():
    os.umask(0o077)
    row=c.run(context_fn=context,transfer_fn=transfer,drive_factory=SameIdDrive)
    try:c.record(row)
    except BaseException:return 2
    return 0 if row['status']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
