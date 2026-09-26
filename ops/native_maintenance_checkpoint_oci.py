"""Private existing-bucket adapter for journal checkpoints; no credential loader.

No deletes, bucket changes, automatic retries or public links. A separately
trusted runtime supplies the scoped OCI client and a fresh mutation guard.
"""
import re

from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_snapshot as snapshot
from ops.native_maintenance_workflow_pause import require

TENANCY = 'ocid1.tenancy.oc1..aaaaaaaa52xuylcexzuwuqj4un36qchvmcxqtggmthadvoiho75r6vbkv24q'
BUCKET = 'bridge-light-autopilot-backups'
TAG = 'bridge-light-autopilot-backups-v1'
PREFIX = 'native-journal/checkpoint-v1/'
LIMIT = 8 * 1024**3


class OCIJournalStore:
    def __init__(self, client, namespace, mutation_guard):
        import oci
        require(type(namespace) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}', namespace),
                'CHECKPOINT_NAMESPACE')
        require(callable(mutation_guard), 'CHECKPOINT_MUTATION_GUARD_REQUIRED')
        self.client, self.namespace, self.guard = client, namespace, mutation_guard
        self.no_retry = oci.retry.NoneRetryStrategy()
        self.service_error = oci.exceptions.ServiceError
        self.failed = False

    def _call(self, method, *args, **kwargs):
        require(not self.failed, 'OCI_CHECKPOINT_ALREADY_FAILED')
        return getattr(self.client, method)(*args, retry_strategy=self.no_retry, **kwargs)

    def _path(self, scope, suffix):
        require(snapshot._hex(scope), 'CHECKPOINT_SCOPE')
        return PREFIX + scope + '/' + suffix

    def assert_private(self):
        detail = self._call('get_bucket', self.namespace, BUCKET, fields=['autoTiering']).data
        require(detail.compartment_id == TENANCY and detail.freeform_tags.get('managed_by') == TAG
                and detail.public_access_type == 'NoPublicAccess' and detail.storage_tier == 'Standard'
                and detail.versioning == 'Disabled' and detail.auto_tiering == 'Disabled'
                and not detail.is_read_only and not detail.kms_key_id, 'CHECKPOINT_BUCKET_REFUSED')
        for method in ('list_preauthenticated_requests', 'list_replication_policies'):
            response = self._call(method, self.namespace, BUCKET, limit=1)
            require(not response.data and not response.headers.get('opc-next-page'), 'CHECKPOINT_PUBLIC_OR_REPLICATED')
        try:
            lifecycle = self._call('get_object_lifecycle_policy', self.namespace, BUCKET).data
        except self.service_error as exc:
            if exc.status != 404:
                raise
        else:
            require(not lifecycle.items, 'CHECKPOINT_LIFECYCLE_REFUSED')

    def _budget(self, size, objects=1):
        """Exact root-compartment inventory; runtime must prove compartment closure.

This bounds the already approved root-compartment object budget, not account-wide
billing. Concurrent independent writers still require actual operator exclusion.
"""
        total = count = 0
        page, seen_pages, bucket_names = None, set(), set()
        while True:
            response = self._call('list_buckets', self.namespace, compartment_id=TENANCY,
                                  limit=1000, **({'page': page} if page else {}))
            for bucket in response.data:
                require(bucket.name not in bucket_names, 'CHECKPOINT_DUPLICATE_BUCKET')
                bucket_names.add(bucket.name)
                detail = self._call('get_bucket', self.namespace, bucket.name).data
                require(detail.versioning == 'Disabled' and detail.storage_tier == 'Standard',
                        'CHECKPOINT_BUDGET_INCOMPLETE')
                start, seen, names = None, set(), set()
                while True:
                    rows = self._call('list_objects', self.namespace, bucket.name, fields='name,size', limit=1000,
                                      **({'start': start} if start else {})).data
                    for item in rows.objects:
                        require(type(item.name) is str and item.name not in names
                                and type(item.size) is int and item.size >= 0, 'CHECKPOINT_OBJECT_INVALID')
                        names.add(item.name)
                        total += item.size
                        count += 1
                        require(total + size < LIMIT and count + objects <= 10000, 'CHECKPOINT_BUDGET_REFUSED')
                    start = rows.next_start_with
                    if not start:
                        break
                    require(type(start) is str and start not in seen, 'CHECKPOINT_PAGINATION_LOOP')
                    seen.add(start)
            page = response.headers.get('opc-next-page')
            if not page:
                break
            require(type(page) is str and page not in seen_pages, 'CHECKPOINT_PAGINATION_LOOP')
            seen_pages.add(page)
        require(BUCKET in bucket_names and total + size < LIMIT and count + objects <= 10000,
                'CHECKPOINT_BUDGET_REFUSED')

    def _read(self, path, limit):
        try:
            response = self._call('get_object', self.namespace, BUCKET, path)
        except self.service_error as exc:
            if exc.status == 404:
                return None
            raise
        try:
            length = response.headers.get('content-length', '')
            revision = response.headers.get('etag', '')
            require(type(length) is str and re.fullmatch('[0-9]{1,10}', length)
                    and 0 < int(length) <= limit, 'CHECKPOINT_DOWNLOAD_SIZE')
            require(type(revision) is str and re.fullmatch('[!-~]{1,256}', revision), 'CHECKPOINT_ETAG')
            pieces, size = [], 0
            for chunk in response.data.raw.stream(16384, decode_content=False):
                require(type(chunk) is bytes, 'CHECKPOINT_DOWNLOAD_TYPE')
                size += len(chunk)
                require(size <= int(length), 'CHECKPOINT_DOWNLOAD_OVERSIZE')
                pieces.append(chunk)
            require(size == int(length), 'CHECKPOINT_DOWNLOAD_TRUNCATED')
            return b''.join(pieces), revision
        finally:
            response.data.close()

    def read_head(self, scope):
        self.assert_private()
        anchor = self._read(self._path(scope, 'registered.json'), 4096)
        head = self._read(self._path(scope, 'head.json'), 4096)
        require((anchor is None) == (head is None), 'CHECKPOINT_REGISTRY_HEAD_INCOMPLETE')
        if head is not None:
            initial = checkpoint.parse_head(anchor[0], scope)
            current = checkpoint.parse_head(head[0], scope)
            require(initial['sequence'] == 1 and (current['sequence'] != 1 or head[0] == anchor[0]),
                    'CHECKPOINT_REGISTRATION_CHANGED')
        return head

    def read_archive(self, scope, digest, limit):
        require(snapshot._hex(digest) and type(limit) is int and 0 < limit <= snapshot.MAX_BYTES,
                'CHECKPOINT_ARCHIVE_REQUEST')
        self.assert_private()
        result = self._read(self._path(scope, 'archive-' + digest + '.json'), limit)
        require(result is not None and checkpoint.sha(result[0]) == digest, 'CHECKPOINT_ARCHIVE_MISSING_OR_CORRUPT')
        return result[0]

    def _put(self, path, data, *, revision=None):
        self.assert_private()
        self.guard()
        self._call('put_object', self.namespace, BUCKET, path, data, content_length=len(data),
                   content_type='application/json',
                   **({'if_none_match': '*'} if revision is None else {'if_match': revision}))

    def put_archive(self, scope, digest, data):
        try:
            require(snapshot._hex(digest) and type(data) is bytes and 0 < len(data) <= snapshot.MAX_BYTES
                    and checkpoint.sha(data) == digest and snapshot._parse(data)['scope_digest'] == scope,
                    'CHECKPOINT_ARCHIVE_INVALID')
            self.assert_private()
            path = self._path(scope, 'archive-' + digest + '.json')
            existing = self._read(path, snapshot.MAX_BYTES)
            if existing is not None:
                require(existing[0] == data, 'CHECKPOINT_IMMUTABLE_CONFLICT')
                return
            self._budget(len(data))
            self._put(path, data)
        except BaseException:
            self.failed = True
            raise

    def compare_head(self, scope, expected_revision, data):
        try:
            new = checkpoint.parse_head(data, scope)
            current = self.read_head(scope)
            self.read_archive(scope, new['archive_digest'], snapshot.MAX_BYTES)
            require((current[1] if current else None) == expected_revision, 'CHECKPOINT_CAS_CONFLICT')
            if current is None:
                require(new['sequence'] == 1, 'CHECKPOINT_INITIAL_SEQUENCE')
                self._budget(2 * len(data), objects=2)
                # Persist a create-only registration first. A lost registration
                # reply or head disappearance leaves a permanent refusing scope.
                self._put(self._path(scope, 'registered.json'), data)
                registered = self._read(self._path(scope, 'registered.json'), 4096)
                require(registered is not None and registered[0] == data, 'CHECKPOINT_REGISTRATION_ACK')
            else:
                old = checkpoint.parse_head(current[0], scope)
                require(new['sequence'] == old['sequence'] + 1 and new['previous'] == checkpoint.sha(current[0]),
                        'CHECKPOINT_HEAD_NOT_SUCCESSOR')
                self._budget(len(data))
            self._put(self._path(scope, 'head.json'), data, revision=expected_revision)
        except BaseException:
            self.failed = True
            raise
