"""One separately approved create-only ciphertext PUT and exact readback.

Reuses OCI credential normalization/factory from reviewed commit6279d2e.
No default discovery, credential files, upload manager, retries or remote cleanup.
"""
import hashlib
import json
import os
import re
import time

from ops.oci_backup_bucket_metadata import BUCKET, REGION, TENANCY, credential_config, make_client
from ops import neon_backup_validate_once as validation

NAMESPACE = 'frzcdnwzijyf'
TOKEN = 'OCI_BACKUP_ONE_CREATE_READBACK_V1'
PREFIX = 'neon-backups/v1/'
OCI_FIELDS = ('USER', 'TENANCY', 'FINGERPRINT', 'KEY_CONTENT', 'REGION')


class StorageFailure(Exception):
    pass


class Storage:
    def __init__(self, credentials):
        self.credentials = credentials
        self.client = None
        self.state = dict(namespace=NAMESPACE, bucket=BUCKET, region=REGION,
            object_key=None, ciphertext_sha256=None, ciphertext_bytes=None,
            put_outcome='NOT_ATTEMPTED', readback_verified=False, remote_delete=False,
            status='NOT_STARTED', policy_attestation='REQUIRED', immutable_retention=False)

    def stop(self, code):
        self.state['status'] = code
        raise StorageFailure()

    def check(self, runner):
        if time.monotonic() >= runner.deadline:
            self.stop('TIME_BUDGET_EXCEEDED')

    def record_intent(self):
        try:
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
                stream.write('```json\n' + json.dumps(dict(schema='oci-backup-create-intent-v1',
                    storage=self.state), sort_keys=True) + '\n```\n')
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            self.state['put_outcome'] = 'NOT_ATTEMPTED'
            self.stop('INTENT_RECORD_FAILED')

    def prepare(self, runner):
        # Old local-validation approval cannot select or authorize this route.
        sha = os.environ.get('EXPECTED_REVIEW', '')
        if not re.fullmatch('[0-9a-f]{40}', sha) or os.environ.get('OCI_WRITE_APPROVAL') != (
                TOKEN + ':' + sha + ':policies-reviewed:one-create-approved'):
            self.stop('WRITE_GATE_REFUSED')
        if (os.environ.get('GITHUB_WORKFLOW_SHA') != sha or os.environ.get('GITHUB_WORKFLOW_REF') !=
                validation.context_guard.REPOSITORY + '/.github/workflows/native-registry-credential-probe.yml@refs/heads/' + validation.context_guard.BRANCH):
            self.stop('WORKFLOW_IDENTITY_REFUSED')
        self.state['policy_attestation'] = 'OWNER_GATE_PRESENT_NOT_API_VERIFIED'
        self.check(runner)
        try:
            config, key = credential_config(self.credentials)
            self.client = make_client(config, key)
            self.credentials.clear()
            namespace = self.client.get_namespace(compartment_id=TENANCY).data
            self.check(runner)
            if namespace != NAMESPACE:
                self.stop('NAMESPACE_REFUSED')
            bucket = self.client.get_bucket(namespace_name=NAMESPACE, bucket_name=BUCKET,
                                            fields=['autoTiering']).data
            self.check(runner)
            expected = dict(name=BUCKET, namespace=NAMESPACE, compartment_id=TENANCY,
                public_access_type='NoPublicAccess', storage_tier='Standard',
                versioning='Disabled', auto_tiering='Disabled', kms_key_id=None)
            absent = object()
            if any(getattr(bucket, field, absent) != value for field, value in expected.items()):
                self.stop('BUCKET_PREFLIGHT_REFUSED')
            self.state['status'] = 'READY'
        except StorageFailure:
            raise
        except BaseException:
            self.stop('STORAGE_SETUP_OR_METADATA_FAILED')

    def exchange(self, path, digest, runner):
        self.check(runner)
        if self.state['status'] != 'READY' or validation.hash_file(path) != digest:
            self.stop('CIPHERTEXT_REFUSED')
        with path.open('rb') as stream:
            if stream.read(8) != b'Salted__':
                self.stop('CIPHERTEXT_REFUSED')
        size = path.stat().st_size
        run_id = os.environ.get('GITHUB_RUN_ID', '')
        sha = os.environ.get('EXPECTED_REVIEW', '')
        if (not re.fullmatch('[0-9]+', run_id) or not re.fullmatch('[0-9a-f]{40}', sha)
                or os.environ.get('GITHUB_RUN_ATTEMPT') != '1'):
            self.stop('WRITE_GATE_REFUSED')
        # Deterministic from known run/SHA: reconstructable without listing even
        # if the entire runner disappears before GitHub retains its summary.
        key = PREFIX + run_id + '-1-' + sha + '.dump.enc'
        # Reconciliation identity exists before PUT, including interrupted writes.
        self.state.update(object_key=key, ciphertext_sha256=digest.hex(), ciphertext_bytes=size,
                          put_outcome='NOT_ATTEMPTED', status='PUT_PENDING')
        self.record_intent()  # Must succeed before any PUT is attempted.
        try:
            with path.open('rb') as stream:
                self.check(runner)
                self.state.update(put_outcome='UNKNOWN', status='PUT_IN_PROGRESS')
                response = self.client.put_object(namespace_name=NAMESPACE, bucket_name=BUCKET,
                    object_name=key, put_object_body=stream, content_length=size,
                    content_type='application/octet-stream', if_none_match='*')
            if getattr(response, 'status', None) != 200:
                self.stop('WRITE_OUTCOME_UNKNOWN')
            self.state.update(put_outcome='CONFIRMED', status='CREATED')
        except BaseException as exc:
            if self.state['put_outcome'] == 'NOT_ATTEMPTED':
                self.stop('LOCAL_OR_DEADLINE_STOP_BEFORE_PUT')
            code = getattr(exc, 'status', None)
            if code == 412:
                self.state['put_outcome'] = 'COLLISION'
                self.stop('COLLISION_STOP')
            if code in (401, 403):
                self.state['put_outcome'] = 'DENIED'
                self.stop('ACCESS_DENIED')
            self.stop('WRITE_OUTCOME_UNKNOWN')
        # No local-ciphertext fallback: restore must consume verified GET bytes.
        path.unlink()
        response = None
        try:
            self.check(runner)
            response = self.client.get_object(namespace_name=NAMESPACE, bucket_name=BUCKET, object_name=key)
            if (str(response.headers.get('content-length')) != str(size) or
                    response.headers.get('content-encoding', 'identity') != 'identity'):
                self.stop('READBACK_MISMATCH')
            downloaded = 0
            actual = hashlib.sha256()
            with path.open('xb') as stream:
                while True:
                    self.check(runner)
                    block = response.data.raw.read(min(65536, size - downloaded + 1), decode_content=False)
                    self.check(runner)
                    if not isinstance(block, bytes):
                        self.stop('READBACK_MISMATCH')
                    if not block:
                        break
                    downloaded += len(block)
                    if downloaded > size or downloaded > validation.MAX_BYTES:
                        self.stop('READBACK_SIZE_REFUSED')
                    actual.update(block)
                    stream.write(block)
            if downloaded != size or actual.digest() != digest:
                self.stop('READBACK_MISMATCH')
            self.state.update(readback_verified=True, status='READBACK_VERIFIED')
        except StorageFailure:
            raise
        except BaseException:
            self.stop('READBACK_FAILED_OBJECT_PRESERVED')
        finally:
            if response is not None:
                response.data.close()


def main():
    # Keep OCI key material out of git, Docker, OpenSSL, and all child environments.
    credentials = {name: os.environ.pop(name, '') for name in ('OCI_CLI_' + x for x in OCI_FIELDS)}
    return validation.main(storage=Storage(credentials), operation=TOKEN)


if __name__ == '__main__':
    raise SystemExit(main())
