"""Owner-authorized existing API-key route: three GETs, one fixed bucket only."""
import json
import os
import re
import signal
import time

from ops.oci_readonly_inventory import (
    TENANCY, REGION, StopProbe, scalar, number, safe_text,
)

BUCKET = 'bridge-light-autopilot-backups'
BUDGET_SECONDS = 120


def credential_config(env):
    config = {key: scalar(env.get('OCI_CLI_' + suffix), key) for key, suffix in (
        ('tenancy', 'TENANCY'), ('user', 'USER'), ('fingerprint', 'FINGERPRINT'), ('region', 'REGION'))}
    if config['tenancy'] != TENANCY or config['region'] != REGION:
        raise StopProbe('TARGET_MISMATCH', 'credential_input')
    if not config['user'].startswith('ocid1.user.') or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){15}[0-9a-fA-F]{2}', config['fingerprint']):
        raise StopProbe('INVALID_CREDENTIAL_INPUT', 'credential_input')
    key = env.get('OCI_CLI_KEY_CONTENT', '')
    if not isinstance(key, str) or len(key) > 32768 or not key.startswith('-----BEGIN '):
        raise StopProbe('INVALID_CREDENTIAL_INPUT', 'credential_input')
    return config, key.replace('\\r', '').replace('\\n', '\n').replace('\r', '')


def make_client(config, key):
    stage = 'sdk_import'
    try:
        import oci
        stage = 'signer_setup'
        signer = oci.signer.Signer(config['tenancy'], config['user'], config['fingerprint'],
                                   None, private_key_content=key)
        stage = 'storage_client_setup'
        return oci.object_storage.ObjectStorageClient(
            dict(config, key_content=key), signer=signer, timeout=(3, 8),
            retry_strategy=oci.retry.NoneRetryStrategy())
    except StopProbe:
        raise
    except Exception:
        raise StopProbe({'sdk_import': 'SDK_IMPORT_FAILED', 'signer_setup': 'SIGNER_SETUP_FAILED',
                         'storage_client_setup': 'STORAGE_CLIENT_SETUP_FAILED'}[stage], stage) from None


def summary():
    from datetime import datetime, timezone
    return dict(schema='oci-backup-bucket-metadata-v1',
                observed_at=datetime.now(timezone.utc).isoformat(),
                status='UNKNOWN', failed_stage=None, region=REGION, bucket=BUCKET,
                scope='EXACT_BUCKET_METADATA_ONLY', namespace='UNKNOWN',
                tenancy_config_match=False, bucket_identity_match=False,
                privacy='UNKNOWN', storage_tier='UNKNOWN', encryption='UNKNOWN',
                versioning='UNKNOWN', auto_tiering='UNKNOWN', events_enabled='UNKNOWN',
                approximate_size_bytes='UNKNOWN', approximate_object_count='UNKNOWN',
                lifecycle='UNKNOWN', lifecycle_rule_count='UNKNOWN',
                billing_free_remaining='UNKNOWN', object_contents_read=False)


def read_metadata(client, clock=time.monotonic):
    result = summary()
    result['tenancy_config_match'] = True
    deadline = clock() + BUDGET_SECONDS
    stage = 'namespace'

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
        return response.data

    try:
        namespace = read(client.get_namespace, compartment_id=TENANCY)
        if safe_text(namespace) == 'UNKNOWN':
            raise StopProbe('INVALID_METADATA')
        result['namespace'] = namespace
        stage = 'bucket_metadata'
        bucket = read(client.get_bucket, namespace_name=namespace, bucket_name=BUCKET,
                      fields=['approximateSize', 'approximateCount', 'autoTiering'])
        if (getattr(bucket, 'name', None) != BUCKET or getattr(bucket, 'namespace', None) != namespace
                or getattr(bucket, 'compartment_id', None) != TENANCY):
            raise StopProbe('TARGET_MISMATCH')
        result['bucket_identity_match'] = True
        for key, choices in [('public_access_type', ('NoPublicAccess', 'ObjectRead', 'ObjectReadWithoutList')),
                             ('storage_tier', ('Standard', 'Archive')),
                             ('versioning', ('Enabled', 'Suspended', 'Disabled')),
                             ('auto_tiering', ('Enabled', 'Disabled'))]:
            value = getattr(bucket, key, None)
            result['privacy' if key == 'public_access_type' else key] = value if value in choices else 'UNKNOWN'
        absent = object()
        kms = getattr(bucket, 'kms_key_id', absent)
        result['encryption'] = ('ORACLE_MANAGED' if kms is None else 'CUSTOMER_MANAGED'
                                if isinstance(kms, str) and kms.startswith('ocid1.key.') else 'UNKNOWN')
        events = getattr(bucket, 'object_events_enabled', None)
        result['events_enabled'] = events if type(events) is bool else 'UNKNOWN'
        result['approximate_size_bytes'] = number(getattr(bucket, 'approximate_size', None))
        result['approximate_object_count'] = number(getattr(bucket, 'approximate_count', None))
        stage = 'lifecycle'
        lifecycle = read(client.get_object_lifecycle_policy, namespace_name=namespace, bucket_name=BUCKET)
        rules = getattr(lifecycle, 'items', None)
        if not isinstance(rules, list):
            raise StopProbe('INVALID_METADATA')
        result.update(lifecycle='RULES_PRESENT' if rules else 'EMPTY_RULES',
                      lifecycle_rule_count=len(rules), status='COMPLETE')
    except StopProbe as exc:
        result.update(status=exc.code, failed_stage=stage)
    except Exception:
        result.update(status='INVALID_METADATA', failed_stage=stage)
    return result


def main():
    result = summary()
    stage = 'credential_input'
    if not hasattr(signal, 'SIGALRM'):
        result.update(status='UNSUPPORTED_PLATFORM', failed_stage='platform')
    else:
        def alarm(_signal, _frame):
            raise StopProbe('TIME_BUDGET_EXCEEDED')
        signal.signal(signal.SIGALRM, alarm)
        signal.alarm(BUDGET_SECONDS)
        try:
            config, key = credential_config(os.environ)
            result['tenancy_config_match'] = True
            stage = 'client_setup'
            client = make_client(config, key)
            stage = 'inventory'
            result = read_metadata(client)
        except StopProbe as exc:
            result.update(status=exc.code, failed_stage=exc.stage or stage)
        except Exception:
            result.update(status='UNEXPECTED_LOCAL_FAILURE', failed_stage=stage)
        finally:
            signal.alarm(0)
    payload = json.dumps(result, sort_keys=True, ensure_ascii=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        try:
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
                output.write('```json\n' + payload + '\n```\n')
        except OSError:
            result.update(status='SUMMARY_WRITE_FAILED', failed_stage='summary')
            payload = json.dumps(result, sort_keys=True, ensure_ascii=True)
    print(payload)
    return 0 if result['status'] == 'COMPLETE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
