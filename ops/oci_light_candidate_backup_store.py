"""Private immutable candidate backup upload/download within a bounded free budget."""
import hashlib
import json
import os
from pathlib import Path
import sys
from oci_storage_audit import main as audit
from oracle_light_candidate_backup import validate_archive
from oci_light_access_audit import TENANCY, scalar

BUCKET='bridge-light-autopilot-backups'
TAG='bridge-light-autopilot-backups-v1'
LIMIT=8*1024**3
PHASE="startup"


def validate_bucket(bucket):
    assert bucket.compartment_id==TENANCY, 'bucket_compartment'
    assert bucket.freeform_tags.get('managed_by')==TAG, 'bucket_tag'
    assert bucket.public_access_type=='NoPublicAccess', 'bucket_public_access'
    assert bucket.storage_tier=='Standard' and bucket.versioning=='Disabled', 'bucket_tier_versioning'
    assert bucket.auto_tiering=='Disabled' and not bucket.is_read_only, 'bucket_tiering_replica'
    assert not bucket.kms_key_id, 'bucket_kms_key'


def main():
    global PHASE
    import oci
    assert len(sys.argv)==3
    source,destination=map(Path,sys.argv[1:])
    assert source.is_file() and not source.is_symlink() and not destination.exists()
    PHASE='source_archive'
    validate_archive(source)
    size=source.stat().st_size
    assert 0<size<32*1024**2
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    PHASE='inventory'
    _,_,inventory=audit()
    assert inventory['compartments']==1 and inventory['allocated_gb']<=100
    assert inventory['object_bytes_approximate']+size<LIMIT
    PHASE='credentials_scope'
    config={k:scalar(os.environ[e],k) for k,e in (
        ('user','OCI_USER'),('tenancy','OCI_TENANCY'),('fingerprint','OCI_FINGERPRINT'),('region','OCI_REGION'))}
    assert config['tenancy']==TENANCY and config['region']=='eu-frankfurt-1'
    config['key_content']=os.environ['OCI_KEY'].replace('\\r','').replace('\\n','\n')
    client=oci.object_storage.ObjectStorageClient(config,timeout=(10,60),retry_strategy=oci.retry.NoneRetryStrategy())
    PHASE='namespace'
    namespace=client.get_namespace(compartment_id=TENANCY).data
    PHASE='bucket_inventory'
    buckets=oci.pagination.list_call_get_all_results(client.list_buckets,namespace,compartment_id=TENANCY).data
    total=0
    count=0
    for bucket in buckets:
        PHASE='existing_bucket_settings'
        detail=client.get_bucket(namespace,bucket.name).data
        assert detail.versioning=='Disabled' and detail.storage_tier=='Standard'
        start=None
        while True:
            PHASE='exact_object_budget'
            objects=client.list_objects(namespace,bucket.name,fields='name,size',limit=1000,**({'start':start} if start else {})).data
            total+=sum(item.size for item in objects.objects)
            count+=len(objects.objects)
            assert count<=10000 and total+size<LIMIT
            start=objects.next_start_with
            if not start:
                break
    PHASE='backup_bucket_identity'
    matches=[b for b in buckets if b.name==BUCKET]
    assert len(matches)<=1
    if not matches:
        PHASE='create_private_bucket'
        client.create_bucket(namespace,oci.object_storage.models.CreateBucketDetails(
            name=BUCKET,compartment_id=TENANCY,public_access_type='NoPublicAccess',
            storage_tier='Standard',versioning='Disabled',auto_tiering='Disabled',
            freeform_tags={'managed_by':TAG}))
    PHASE='validate_private_bucket'
    # autoTiering is opt-in response metadata; omitted fields are not proof of Disabled.
    validate_bucket(client.get_bucket(namespace,BUCKET,fields=['autoTiering']).data)
    PHASE='no_public_links'
    assert not oci.pagination.list_call_get_all_results(client.list_preauthenticated_requests,namespace,BUCKET).data
    PHASE='no_replication'
    assert not oci.pagination.list_call_get_all_results(client.list_replication_policies,namespace,BUCKET).data
    name='candidate/20260922/'+digest+'.tar.gz'
    try:
        PHASE='existing_object_identity'
        head=client.head_object(namespace,BUCKET,name)
        assert int(head.headers['content-length'])==size
        assert head.headers.get('opc-meta-sha256')==digest
    except oci.exceptions.ServiceError as exc:
        if exc.status!=404:
            raise
        PHASE='immutable_upload'
        with source.open('rb') as stream:
            client.put_object(namespace,BUCKET,name,stream,content_length=size,if_none_match='*',
                              content_type='application/gzip',opc_meta={'sha256':digest,'scope':'fenced-candidate'})
    PHASE='download_verify'
    response=client.get_object(namespace,BUCKET,name)
    assert int(response.headers['content-length'])==size
    received=0
    hasher=hashlib.sha256()
    with destination.open('xb') as output:
        os.chmod(destination,0o600)
        for chunk in response.data.raw.stream(1024**2,decode_content=False):
            received+=len(chunk)
            assert received<=size
            hasher.update(chunk)
            output.write(chunk)
    assert received==size and hasher.hexdigest()==digest
    print(json.dumps({'candidate_off_vm_backup':'UPLOADED_AND_DOWNLOADED_VERIFIED','bytes':size,
                      'sha256':digest,'private_bucket':True,'total_object_bytes_upper_bound':total+size,
                      'deleted_objects':0,'production_backup':False}))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'candidate_off_vm_backup':'NOT_CONFIRMED','error_type':type(exc).__name__,
                          'http_status':getattr(exc,'status',None),'phase':PHASE,
                          'assertion':str(exc) if isinstance(exc,AssertionError) else None}))
        sys.exit(2)
