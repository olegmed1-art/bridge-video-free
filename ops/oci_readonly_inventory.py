"""Bounded metadata-only OCI probe. No writes, default credential discovery or retries."""
import hashlib
import json
import os
import re
import signal
import time
from datetime import datetime, timezone

REGION = 'eu-frankfurt-1'
TENANCY = 'ocid1.tenancy.oc1..aaaaaaaa52xuylcexzuwuqj4un36qchvmcxqtggmthadvoiho75r6vbkv24q'
MAX_PAGES = 3
MAX_BUCKETS = 20
BUDGET_SECONDS = 180


class StopProbe(Exception):
    def __init__(self, code):
        self.code = code


def safe_text(value):
    # Never emit control characters, arbitrary exceptions, tags or free-form descriptions.
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,256}', value) else 'UNKNOWN'


def number(value):
    return value if type(value) is int and value >= 0 else 'UNKNOWN'


def scalar(value, key):
    # Existing repository credentials can contain a single key=value scalar.
    if not isinstance(value, str):
        raise StopProbe('INVALID_CREDENTIAL_INPUT')
    value = value.strip()
    if '\n' in value or '\r' in value:
        raise StopProbe('INVALID_CREDENTIAL_INPUT')
    if value.startswith(key + '='):
        value = value[len(key) + 1:].strip()
    if not value or '=' in value or not re.fullmatch(r'[A-Za-z0-9_.:-]+', value):
        raise StopProbe('INVALID_CREDENTIAL_INPUT')
    return value


def credential_config(env):
    config = {key: scalar(env.get('OCI_READONLY_CLI_' + suffix), key) for key, suffix in (
        ('tenancy', 'TENANCY'), ('user', 'USER'), ('fingerprint', 'FINGERPRINT'), ('region', 'REGION'))}
    if config['tenancy'] != TENANCY or config['region'] != REGION:
        raise StopProbe('TARGET_MISMATCH')
    if not config['user'].startswith('ocid1.user.') or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){15}[0-9a-fA-F]{2}', config['fingerprint']):
        raise StopProbe('INVALID_CREDENTIAL_INPUT')
    key = env.get('OCI_READONLY_CLI_KEY_CONTENT', '')
    if not isinstance(key, str) or len(key) > 32768 or not key.startswith('-----BEGIN '):
        raise StopProbe('INVALID_CREDENTIAL_INPUT')
    key = key.replace('\\r', '').replace('\\n', '\n').replace('\r', '')
    return config, key


def make_clients(config, key):
    import oci  # Pinned by workflow; absent during offline unit tests.
    signer = oci.signer.Signer(config['tenancy'], config['user'], config['fingerprint'],
                               None, private_key_content=key)
    kwargs = dict(signer=signer, timeout=(3, 8), retry_strategy=oci.retry.NoneRetryStrategy())
    return (oci.identity.IdentityClient(config, **kwargs),
            oci.object_storage.ObjectStorageClient(config, **kwargs))


def empty_summary():
    return dict(schema='oci-readonly-inventory-v1', observed_at=datetime.now(timezone.utc).isoformat(),
                status='UNKNOWN', region=REGION, inventory_scope='TENANCY_ROOT_ONLY', other_compartments='NOT_CHECKED',
                home_region_key='UNKNOWN', namespace='UNKNOWN', buckets=[],
                tenancy_match=False,
                bucket_inventory_complete=False, billing_free_remaining='UNKNOWN',
                iam_grants='NOT_QUERIED', failed_stage=None)


def inventory(iam, storage, clock=time.monotonic):
    result = empty_summary()
    deadline = clock() + BUDGET_SECONDS
    stage = 'tenancy'

    def read(fn, **kwargs):
        if clock() >= deadline:
            raise StopProbe('TIME_BUDGET_EXCEEDED')
        try:
            response = fn(**kwargs)
        except StopProbe:
            raise
        except Exception as exc:
            status = getattr(exc, 'status', None)
            raise StopProbe('ACCESS_DENIED' if status in (401, 403) else
                            'NOT_FOUND_OR_NOT_VISIBLE' if status == 404 else 'API_READ_FAILED') from None
        if clock() >= deadline:
            raise StopProbe('TIME_BUDGET_EXCEEDED')
        return response

    try:
        tenancy = read(iam.get_tenancy, tenancy_id=TENANCY).data
        if getattr(tenancy, 'id', None) != TENANCY:
            raise StopProbe('TARGET_MISMATCH')
        result['tenancy_match'] = True
        result['home_region_key'] = safe_text(getattr(tenancy, 'home_region_key', None))
        compartment = TENANCY
        stage = 'namespace'
        namespace = read(storage.get_namespace, compartment_id=TENANCY).data
        if safe_text(namespace) == 'UNKNOWN':
            raise StopProbe('INVALID_METADATA')
        result['namespace'] = namespace
        page = None
        seen_pages = set()
        seen_buckets = set()
        for _ in range(MAX_PAGES):
            stage = 'bucket_list'
            kwargs = dict(namespace_name=namespace, compartment_id=compartment, limit=MAX_BUCKETS)
            if page is not None:
                kwargs['page'] = page
            response = read(storage.list_buckets, **kwargs)
            entries = response.data
            if not isinstance(entries, list):
                raise StopProbe('INVALID_METADATA')
            for entry in entries:
                if len(result['buckets']) >= MAX_BUCKETS:
                    raise StopProbe('INCOMPLETE_LIMIT')
                name = getattr(entry, 'name', None)
                if not isinstance(name, str) or not name or len(name) > 256 or name in seen_buckets:
                    raise StopProbe('INVALID_METADATA')
                seen_buckets.add(name)
                stage = 'bucket_metadata'
                bucket = read(storage.get_bucket, namespace_name=namespace, bucket_name=name,
                              fields=['approximateSize', 'approximateCount']).data
                if getattr(bucket, 'name', None) != name or getattr(bucket, 'compartment_id', None) != compartment:
                    raise StopProbe('TARGET_MISMATCH')
                privacy = getattr(bucket, 'public_access_type', None)
                tier = getattr(bucket, 'storage_tier', None)
                result['buckets'].append(dict(
                    name=safe_text(name), name_sha256=hashlib.sha256(name.encode()).hexdigest(),
                    privacy=privacy if privacy in ('NoPublicAccess', 'ObjectRead', 'ObjectReadWithoutList') else 'UNKNOWN',
                    storage_tier=tier if tier in ('Standard', 'Archive') else 'UNKNOWN',
                    approximate_size_bytes=number(getattr(bucket, 'approximate_size', None)),
                    approximate_object_count=number(getattr(bucket, 'approximate_count', None))))
            page = response.headers.get('opc-next-page')
            if not page:
                result['bucket_inventory_complete'] = True
                result['status'] = 'COMPLETE'
                return result
            if not isinstance(page, str) or len(page) > 4096 or page in seen_pages:
                raise StopProbe('INVALID_PAGINATION')
            seen_pages.add(page)
        raise StopProbe('INCOMPLETE_LIMIT')
    except StopProbe as exc:
        result.update(status=exc.code, failed_stage=stage)
    except Exception:
        result.update(status='INVALID_METADATA', failed_stage=stage)
    return result


def main():
    result = empty_summary()
    # POSIX runner wall-clock cap covers credential parsing and SDK construction too.
    if not hasattr(signal, 'SIGALRM'):
        result['status'] = 'UNSUPPORTED_PLATFORM'
    else:
        def alarm(_signal, _frame):
            raise StopProbe('TIME_BUDGET_EXCEEDED')
        signal.signal(signal.SIGALRM, alarm)
        signal.alarm(BUDGET_SECONDS)
        try:
            config, key = credential_config(os.environ)
            result = inventory(*make_clients(config, key))
        except StopProbe as exc:
            result['status'] = exc.code
        except Exception:
            result['status'] = 'CLIENT_SETUP_FAILED'
        finally:
            signal.alarm(0)
    payload = json.dumps(result, sort_keys=True, ensure_ascii=True)
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_path:
        try:
            with open(summary_path, 'a', encoding='utf-8') as output:
                output.write('```json\n' + payload + '\n```\n')
        except OSError:
            result['status'] = 'SUMMARY_WRITE_FAILED'
            payload = json.dumps(result, sort_keys=True, ensure_ascii=True)
    print(payload)
    return 0 if result['status'] == 'COMPLETE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
