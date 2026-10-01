"""Preparation only: a future owner-gated single synthetic create/readback probe."""
import hashlib
import json
import os
import re
import signal
import time
import uuid

from ops.oci_backup_bucket_metadata import BUCKET, REGION, TENANCY, credential_config, make_client
from ops.oci_readonly_inventory import StopProbe, safe_text

BUDGET_SECONDS = 120
REPOSITORY = 'olegmed1-art/bridge-video-free'
BRANCH = 'refs/heads/review/oci-readonly-inventory-20261001'
WORKFLOW = REPOSITORY + '/.github/workflows/oracle-epoch-readonly-probe.yml@' + BRANCH
PAYLOAD = b'bridge-school synthetic object-storage probe v1\nNo personal or production data.\n'
PREFIX = 'neon-backups/probe-v1/'


def require_gate(env):
    sha = env.get('GITHUB_SHA', '')
    expected = {
        'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_REF': BRANCH,
        'GITHUB_ACTOR': 'olegmed1-art', 'GITHUB_TRIGGERING_ACTOR': 'olegmed1-art',
        'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_WORKFLOW_SHA': sha,
        'GITHUB_WORKFLOW_REF': WORKFLOW,
        'WRITE_GATE': 'oci-backup-write-probe-v1:' + sha + ':policies-reviewed:one-write-approved',
    }
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or any(env.get(k) != v for k, v in expected.items()):
        raise StopProbe('OWNER_GATE_REJECTED', 'owner_gate')
    for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
        if not re.fullmatch(r'[1-9][0-9]{0,19}', env.get(key, '')):
            raise StopProbe('OWNER_GATE_REJECTED', 'owner_gate')
    # Re-running the same authorization must never create an additional object.
    if env['GITHUB_RUN_ATTEMPT'] != '1':
        raise StopProbe('RERUN_REJECTED', 'owner_gate')


def summary():
    return dict(schema='oci-backup-write-probe-v1', status='UNKNOWN', failed_stage=None,
                bucket=BUCKET, region=REGION, object_key=None,
                payload_sha256=hashlib.sha256(PAYLOAD).hexdigest(), payload_bytes=len(PAYLOAD),
                put_attempted=False, created_confirmed=False, readback_verified=False,
                object_preserved=True, policy_attestation='OWNER_GATE_REQUIRED',
                billing_free_remaining='UNKNOWN')


def run_probe(client, env, clock=time.monotonic):
    result = summary()
    stage = 'owner_gate'
    deadline = clock() + BUDGET_SECONDS

    def check_time():
        if clock() >= deadline:
            raise StopProbe('TIME_BUDGET_EXCEEDED')

    def read(fn, check_after=True, **kwargs):
        check_time()
        try:
            value = fn(**kwargs)
        except StopProbe:
            raise
        except Exception as exc:
            code = getattr(exc, 'status', None)
            raise StopProbe('ACCESS_DENIED' if code in (401, 403) else
                            'NOT_FOUND_OR_NOT_VISIBLE' if code == 404 else 'READ_FAILED') from None
        if check_after:
            check_time()
        return value

    try:
        require_gate(env)
        result['policy_attestation'] = 'OWNER_GATE_PRESENT_NOT_API_VERIFIED'
        stage = 'namespace'
        namespace = read(client.get_namespace, compartment_id=TENANCY).data
        if safe_text(namespace) == 'UNKNOWN':
            raise StopProbe('INVALID_METADATA')
        stage = 'bucket_metadata'
        bucket = read(client.get_bucket, namespace_name=namespace, bucket_name=BUCKET,
                      fields=['autoTiering']).data
        expected = dict(name=BUCKET, namespace=namespace, compartment_id=TENANCY,
                        public_access_type='NoPublicAccess', storage_tier='Standard',
                        versioning='Disabled', auto_tiering='Disabled', kms_key_id=None)
        absent = object()
        if any(getattr(bucket, field, absent) != value for field, value in expected.items()):
            raise StopProbe('BUCKET_PREFLIGHT_REJECTED')
        if not 0 < len(PAYLOAD) <= 1024:
            raise StopProbe('PAYLOAD_REJECTED')
        result['object_key'] = PREFIX + env['GITHUB_RUN_ID'] + '-' + env['GITHUB_RUN_ATTEMPT'] + '-' + uuid.uuid4().hex
        stage = 'put'
        check_time()
        result['put_attempted'] = True
        try:
            # make_client disables SDK retries; no upload manager or multipart path.
            response = client.put_object(namespace_name=namespace, bucket_name=BUCKET,
                object_name=result['object_key'], put_object_body=PAYLOAD,
                content_length=len(PAYLOAD), content_type='application/octet-stream', if_none_match='*')
        except Exception as exc:
            code = getattr(exc, 'status', None)
            raise StopProbe('COLLISION_STOP' if code == 412 else 'ACCESS_DENIED' if code in (401, 403)
                            else 'WRITE_OUTCOME_UNKNOWN') from None
        if getattr(response, 'status', None) != 200:
            raise StopProbe('WRITE_OUTCOME_UNKNOWN')
        result['created_confirmed'] = True
        stage = 'readback'
        response = read(client.get_object, namespace_name=namespace, bucket_name=BUCKET,
                        object_name=result['object_key'], check_after=False)
        try:
            check_time()
            length = response.headers.get('content-length')
            if str(length) != str(len(PAYLOAD)) or response.headers.get('content-encoding', 'identity') != 'identity':
                raise StopProbe('READBACK_MISMATCH')
            check_time()
            body = response.data.raw.read(1025, decode_content=False)
            check_time()
            if not isinstance(body, bytes) or len(body) != len(PAYLOAD) or hashlib.sha256(body).hexdigest() != result['payload_sha256']:
                raise StopProbe('READBACK_MISMATCH')
        finally:
            response.data.close()
        result.update(status='VERIFIED', readback_verified=True)
    except StopProbe as exc:
        code = exc.code
        if stage == 'put' and result['put_attempted'] and not result['created_confirmed'] and code not in ('ACCESS_DENIED', 'COLLISION_STOP'):
            code = 'WRITE_OUTCOME_UNKNOWN'
        result.update(status=code, failed_stage=exc.stage or stage)
    except Exception:
        result.update(status='WRITE_OUTCOME_UNKNOWN' if stage == 'put' and result['put_attempted']
                      else 'LOCAL_OR_READ_FAILURE', failed_stage=stage)
    return result


def main():
    result = summary()
    try:
        require_gate(os.environ)
        if not hasattr(signal, 'SIGALRM'):
            raise StopProbe('UNSUPPORTED_PLATFORM', 'platform')
        def alarm(_signal, _frame):
            raise StopProbe('TIME_BUDGET_EXCEEDED')
        signal.signal(signal.SIGALRM, alarm)
        signal.alarm(BUDGET_SECONDS)
        try:
            config, key = credential_config(os.environ)
            result = run_probe(make_client(config, key), os.environ)
        finally:
            signal.alarm(0)
    except StopProbe as exc:
        result.update(status=exc.code, failed_stage=exc.stage)
    except Exception:
        result.update(status='LOCAL_SETUP_FAILED', failed_stage='setup')
    payload = json.dumps(result, sort_keys=True, ensure_ascii=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        try:
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
                output.write('```json\n' + payload + '\n```\n')
        except OSError:
            result.update(status='SUMMARY_WRITE_FAILED', failed_stage='summary')
            payload = json.dumps(result, sort_keys=True, ensure_ascii=True)
    print(payload)
    return 0 if result['status'] == 'VERIFIED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
