"""Journalled workflow exclusion primitive, not a production maintenance guard.

No HTTP client, credentials, workflow selection or automatic cleanup is supplied.
The adapter must use authenticated exact-repository requests. The journal must
live on persistent operator storage, never a disposable Actions runner. A PUT
intent is durable before dispatch; an interrupted intent is never retried or
automatically reversed. Disabled workflows survive controller cancellation.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat


class Refused(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_KEY')
        result[key] = value
    return result


def validate_plan(plan):
    require(type(plan) is dict and set(plan) == {'version', 'repository', 'source', 'workflows'},
            'PLAN_SHAPE')
    require(plan['version'] == 1 and type(plan['version']) is int
            and plan['repository'] == 'olegmed1-art/bridge-video-free'
            and re.fullmatch('[0-9a-f]{40}', plan['source'] or ''), 'PLAN_IDENTITY')
    rows = plan['workflows']
    require(type(rows) is list and 0 < len(rows) <= 64, 'PLAN_SIZE')
    ids, paths = set(), set()
    for row in rows:
        require(type(row) is dict and set(row) == {'id', 'path', 'state', 'updated_at'}, 'WORKFLOW_SHAPE')
        require(type(row['id']) is int and row['id'] > 0 and row['id'] not in ids, 'WORKFLOW_ID')
        require(type(row['path']) is str and re.fullmatch(r'\.github/workflows/[A-Za-z0-9_-]+\.ya?ml', row['path'])
                and row['path'] not in paths, 'WORKFLOW_PATH')
        require(row['state'] in ('active', 'disabled_manually', 'disabled_inactivity')
                and type(row['updated_at']) is str
                and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.[0-9]{1,9})?Z', row['updated_at']),
                'WORKFLOW_STATE')
        ids.add(row['id'])
        paths.add(row['path'])


class Journal:
    """Exclusive, append-only, fsynced records in an existing private directory.

No record replacement/deletion API. A corrupt or incomplete tail blocks resume.
Trusted operator storage and its backup are deployment prerequisites; fsync is
not a claim that a CI runner's disk survives runner deletion.
"""
    def __init__(self, root):
        self.fd = None
        self.lock = None
        self.records = []
        self.failed = False
        self.root = Path(root)
        self.fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(self.fd)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_DIRECTORY_REQUIRED')
            self.lock = os.open('lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.fd)
            self._regular(self.lock)
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            names = os.listdir(self.fd)
            require(all(name == 'lock' or re.fullmatch(r'[0-9]{6}\.json', name) for name in names), 'JOURNAL_EXTRA_FILE')
            files = sorted(name for name in names if name != 'lock')
            require(len(files) <= 1024, 'JOURNAL_TOO_LONG')
            for index, name in enumerate(files):
                require(name == f'{index:06d}.json', 'JOURNAL_GAP')
                handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                try:
                    self._regular(handle)
                    data = os.read(handle, 262145)
                finally:
                    os.close(handle)
                require(len(data) <= 262144, 'JOURNAL_RECORD_SIZE')
                record = json.loads(data, object_pairs_hook=unique)
                require(set(record) == {'previous', 'event'} and record['previous'] == self.tail, 'JOURNAL_CHAIN')
                self.records.append(record)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _regular(fd):
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, 'PRIVATE_REGULAR_FILE_REQUIRED')

    @property
    def tail(self):
        return digest(self.records[-1]) if self.records else None

    def assert_live(self):
        require(not self.failed and self.fd is not None and self.lock is not None, 'JOURNAL_UNAVAILABLE')
        directory = os.stat(self.root, follow_symlinks=False)
        opened = os.fstat(self.fd)
        lock_path = os.stat('lock', dir_fd=self.fd, follow_symlinks=False)
        lock_opened = os.fstat(self.lock)
        require(stat.S_ISDIR(directory.st_mode) and (directory.st_dev, directory.st_ino) ==
                (opened.st_dev, opened.st_ino) and directory.st_uid == os.getuid()
                and stat.S_IMODE(directory.st_mode) == 0o700, 'JOURNAL_DIRECTORY_CHANGED')
        require((lock_path.st_dev, lock_path.st_ino) == (lock_opened.st_dev, lock_opened.st_ino),
                'JOURNAL_LOCK_CHANGED')
        self._regular(self.lock)

    def append(self, event):
        self.assert_live()
        record = {'previous': self.tail, 'event': event}
        data = encoded(record)
        require(len(data) <= 262144 and len(self.records) < 1024, 'JOURNAL_RECORD_SIZE')
        try:
            # O_EXCL leaves a partial record detectable after any failed write.
            handle = os.open(f'{len(self.records):06d}.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.fd)
            with os.fdopen(handle, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(self.fd)
            self.records.append(record)
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class WorkflowPause:
    """Scoped reversible API writes with explicit recovery, never a DB permit.

coordination.assert_scope(plan_digest) must verify the independently reviewed
writer coverage, pinned code and coordination of direct operators, workflow
administrators and rerun-capable actors. No implementation/default is supplied.
release.assert_reconciled(plan_digest) must additionally establish remote/DB
completion, including lingering sessions, before any workflow is enabled.
"""
    def __init__(self, plan, approved_digest, api, journal, coordination):
        validate_plan(plan)
        require(digest(plan) == approved_digest, 'PLAN_DIGEST_MISMATCH')
        self.plan = json.loads(encoded(plan))
        self.plan_digest = approved_digest
        self.api, self.journal, self.coordination = api, journal, coordination
        self.failed = False
        require(callable(getattr(coordination, 'assert_scope', None)), 'COORDINATION_REQUIRED')
        if not journal.records:
            coordination.assert_scope(approved_digest)
            journal.append({'kind': 'PLAN', 'digest': approved_digest, 'plan': self.plan})
        self.states = {row['id']: {'phase': 'original', 'observed': row} for row in self.plan['workflows']}
        first = journal.records[0]['event']
        require(first == {'kind': 'PLAN', 'digest': approved_digest, 'plan': self.plan}, 'JOURNAL_PLAN_MISMATCH')
        for record in journal.records[1:]:
            self._transition(record['event'])

    def _transition(self, event):
        require(type(event) is dict and set(event) == {'kind', 'id', 'observed'}, 'JOURNAL_EVENT_SHAPE')
        state = self.states.get(event['id'])
        require(state is not None, 'JOURNAL_WORKFLOW_UNKNOWN')
        kind = event['kind']
        rules = {'DISABLE_INTENT': ('original', 'disable_unknown'),
                 'DISABLED': ('disable_unknown', 'paused'),
                 'ENABLE_INTENT': ('paused', 'enable_unknown'),
                 'ENABLED': ('enable_unknown', 'restored')}
        require(kind in rules and state['phase'] == rules[kind][0], 'JOURNAL_PHASE')
        observed = event['observed']
        require(type(observed) is dict and set(observed) == {'id', 'path', 'state', 'updated_at'}, 'JOURNAL_OBSERVATION')
        require(observed['id'] == event['id'] and observed['path'] == state['observed']['path'], 'JOURNAL_IDENTITY')
        if kind.endswith('INTENT'):
            require(observed == state['observed'], 'JOURNAL_INTENT_DRIFT')
        require(observed['state'] == ('disabled_manually' if kind in ('DISABLED', 'ENABLE_INTENT') else 'active'),
                'JOURNAL_STATE')
        self.states[event['id']] = {'phase': rules[kind][1], 'observed': observed}

    def _event(self, kind, row):
        event = {'kind': kind, 'id': row['id'], 'observed': row}
        self.journal.append(event)
        self._transition(event)

    def _scope(self):
        require(not self.failed, 'PAUSE_ALREADY_FAILED')
        self.journal.assert_live()
        self.coordination.assert_scope(self.plan_digest)

    def _read(self, row):
        result = self.api.get_workflow(row['id'])
        require(type(result) is dict, 'WORKFLOW_RESPONSE')
        actual = {key: result.get(key) for key in ('id', 'path', 'state', 'updated_at')}
        validate_plan({**self.plan, 'workflows': [actual]})
        require(actual['id'] == row['id'] and actual['path'] == row['path'], 'WORKFLOW_IDENTITY_CHANGED')
        return actual

    def pause(self):
        try:
            self._scope()
            require(all(s['phase'] in ('original', 'paused') for s in self.states.values()), 'AMBIGUOUS_WRITE_REQUIRES_RECONCILIATION')
            for row in self.plan['workflows']:
                self._scope()
                state = self.states[row['id']]
                require(self._read(row) == state['observed'], 'WORKFLOW_DRIFT')
                if state['phase'] == 'paused' or row['state'] != 'active':
                    continue
                self._event('DISABLE_INTENT', row)
                self._scope()
                # No automatic retry: lost response leaves durable uncertainty.
                self.api.disable_workflow(row['id'])
                actual = self._read(row)
                require(actual['state'] == 'disabled_manually', 'DISABLE_NOT_OBSERVED')
                self._event('DISABLED', actual)
            self.assert_paused()
        except BaseException:
            self.failed = True
            raise

    def assert_paused(self):
        """Check disabled states only; existing/rerun/remote work is NOT drained."""
        try:
            self._scope()
            for row in self.plan['workflows']:
                state = self.states[row['id']]
                require(state['phase'] == ('paused' if row['state'] == 'active' else 'original'), 'WORKFLOW_NOT_PAUSED')
                require(self._read(row) == state['observed'], 'WORKFLOW_DRIFT')
            self._scope()
        except BaseException:
            self.failed = True
            raise

    def restore(self, release):
        """Separate operator recovery; never call this in finally/on expiry."""
        try:
            self._scope()
            require(callable(getattr(release, 'assert_reconciled', None)), 'RECONCILED_RELEASE_REQUIRED')
            require(all(s['phase'] in ('original', 'paused', 'restored') for s in self.states.values()),
                    'AMBIGUOUS_WRITE_REQUIRES_RECONCILIATION')
            for row in self.plan['workflows']:
                self._scope()
                release.assert_reconciled(self.plan_digest)
                state = self.states[row['id']]
                require(self._read(row) == state['observed'], 'WORKFLOW_DRIFT')
                if state['phase'] != 'paused':
                    continue
                self._event('ENABLE_INTENT', state['observed'])
                self._scope()
                release.assert_reconciled(self.plan_digest)
                self.api.enable_workflow(row['id'])
                actual = self._read(row)
                require(actual['state'] == 'active', 'ENABLE_NOT_OBSERVED')
                self._event('ENABLED', actual)
            self._scope()
            release.assert_reconciled(self.plan_digest)
        except BaseException:
            self.failed = True
            raise
