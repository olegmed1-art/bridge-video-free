"""Private immutable candidate backup upload/download within a bounded free budget."""
import hashlib
import json
import os
from pathlib import Path
import sys
from oci_storage_audit import main as audit
from oci_light_access_audit import TENANCY, scalar

BUCKET='bridge-light-autopilot-backups'
TAG='bridge-light-autopilot-backups-v1'
LIMIT=8*1024**3


def validate_bucket(bucket):
    assert bucket.compartment_id==TENANCY
    assert bucket.freeform_tags.get('managed_by')==TAG
    assert bucket.public_access_type=='NoPublicAccess'
    assert bucket.storage_tier=='Standard' and bucket.versioning=='Disabled'
    assert bucket.auto_tiering=='Disabled' and not bucket.is_read_only
    assert not bucket.kms_key_id


def main():
    import oci
    assert len(sys.argv)==3
    source,destination=map(Path,sys.argv[1:])
    assert source.is_file() and not source.is_symlink() and not destination.exists()
    size=source.stat().st_size
    assert 0<size<32*1024**2
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    _,_,inventory=audit()
    assert inventory['compartments']==1 and inventory['allocated_gb']<=100
    assert inventory['object_bytes_approximate']+size<LIMIT
    config={k:scalar(os.environ[e],k) for k,e in (
        ('user','OCI_USER'),('tenancy','OCI_TENANCY'),('fingerprint','OCI_FINGERPRINT'),('region','OCI_REGION'))}
    assert config['tenancy']==TENANCY and config['region']=='eu-frankfurt-1'
    config['key_content']=os.environ['OCI_KEY'].replace('\\r','').replace('\\n','\n')
    client=oci.object_storage.ObjectStorageClient(config,timeout=(10,60),retry_strategy=oci.retry.NoneRetryStrategy())
    namespace=client.get_namespace(compartment_id=TENANCY).data
    buckets=oci.pagination.list_call_get_all_results(client.list_buckets,namespace,compartment_id=TENANCY).data
    total=0
    count=0
    for bucket in buckets:
        detail=client.get_bucket(namespace,bucket.name).data
        assert detail.versioning=='Disabled' and detail.storage_tier=='Standard'
        start=None
        while True:
            objects=client.list_objects(namespace,bucket.name,fields='name,size',limit=1000,**({'start':start} if start else {})).data
            total+=sum(item.size for item in objects.objects)
            count+=len(objects.objects)
            assert count<=10000 and total+size<LIMIT
            start=objects.next_start_with
            if not start:
                break
    matches=[b for b in buckets if b.name==BUCKET]
    assert len(matches)<=1
    if not matches:
        client.create_bucket(namespace,oci.object_storage.models.CreateBucketDetails(
            name=BUCKET,compartment_id=TENANCY,public_access_type='NoPublicAccess',
            storage_tier='Standard',versioning='Disabled',auto_tiering='Disabled',
            freeform_tags={'managed_by':TAG}))
    validate_bucket(client.get_bucket(namespace,BUCKET).data)
    assert not oci.pagination.list_call_get_all_results(client.list_preauthenticated_requests,namespace,BUCKET).data
    assert not oci.pagination.list_call_get_all_results(client.list_replication_policies,namespace,BUCKET).data
    name='candidate/20260922/'+digest+'.tar.gz'
    try:
        head=client.head_object(namespace,BUCKET,name)
        assert int(head.headers['content-length'])==size
        assert head.headers.get('opc-meta-sha256')==digest
    except oci.exceptions.ServiceError as exc:
        if exc.status!=404:
            raise
        with source.open('rb') as stream:
            client.put_object(namespace,BUCKET,name,stream,content_length=size,if_none_match='*',
                              content_type='application/gzip',opc_meta={'sha256':digest,'scope':'fenced-candidate'})
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
                          'http_status':getattr(exc,'status',None)}))
        sys.exit(2)
