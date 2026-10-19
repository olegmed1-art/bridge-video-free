"""Persistent host reconciliation around the existing finite issuer.

Only two integration ports exist: primary_observe(ChildContext), which performs
read-only primary checks, and issue_once(ChildContext), which invokes the existing
issuer exactly once using host-authenticated children. They are trusted installed
code, never model/tool inputs. This core deliberately has no production launcher:
the existing GitHub-only cycle child must be explicitly wired to HostAuthority
before it can be used here. Unit ports do not establish production acceptance.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import stat
import threading
import time

from ops import light_native_service_authority as authority

require = authority.require
encoded = authority.encoded
parse = authority.parse
sha = authority.sha
digest = authority.digest
BLOCKED = {'UNKNOWN', 'INCOMPLETE_REQUIRES_RECONCILIATION',
           'COMPLETE_ACK_MISSING', 'RETIREMENT_PROPOSAL_UNACCEPTED'}
OBSERVATION_KEYS = {'version', 'kind', 'manifest_sha256', 'policy_sha256', 'source',
                    'observed_at', 'state', 'issued', 'predecessor', 'cycle_sha256',
                    'live_verified', 'controls_restored'}


class LockBusy(RuntimeError):
    """Only the actual supervisor lock can yield the public BUSY state."""


@dataclass(frozen=True)
class Outcome:
    state: str
    issued: int

    def public(self):
        return dict(audit='LIGHT_NATIVE_SERVICE_SUPERVISOR', **asdict(self))


def observation(value, host, context):
    """Exact primary readback contract; diagnostics can never authorize issue."""
    authority.exact(value, OBSERVATION_KEYS)
    now = time.time()
    require(type(value['version']) is int and value['version'] == 1
            and value['kind'] == 'LIGHT_NATIVE_SERVICE_PRIMARY'
            and value['manifest_sha256'] == host.accepted
            and value['policy_sha256'] == host.policy.digest and value['source'] == host.source
            and type(value['observed_at']) is int
            and context.not_before <= value['observed_at'] <= now
            and now-value['observed_at'] <= 30
            and type(value['issued']) is int and 0 <= value['issued'] <= len(host.policy.plans)
            and type(value['live_verified']) is bool and type(value['controls_restored']) is bool
            and type(value['state']) is str
            and value['state'] in BLOCKED | {'READY', 'EXHAUSTED', 'COMPLETE_ACKNOWLEDGED'},
            'NATIVE_SERVICE_PRIMARY')
    authority.predecessor(value['predecessor'])
    require(value['cycle_sha256'] is None or digest(value['cycle_sha256']), 'NATIVE_SERVICE_PRIMARY')
    if value['state'] not in BLOCKED:
        issued = value['issued']
        require(value['live_verified'] is True and value['controls_restored'] is True
                and value['predecessor']['sequence'] == host.policy.predecessor['sequence']+issued,
                'NATIVE_SERVICE_PRIMARY')
        if issued == 0:
            require(value['predecessor'] == host.policy.predecessor and value['cycle_sha256'] is None,
                    'NATIVE_SERVICE_PRIMARY')
        else:
            require(value['predecessor']['plan_sha256'] == host.policy.plans[issued-1].plan_sha256
                    and digest(value['cycle_sha256']), 'NATIVE_SERVICE_PRIMARY')
        require((value['state'] != 'EXHAUSTED' or issued == len(host.policy.plans))
                and (value['state'] != 'READY' or issued < len(host.policy.plans))
                and (value['state'] != 'COMPLETE_ACKNOWLEDGED' or issued > 0), 'NATIVE_SERVICE_PRIMARY')
    return parse(encoded(value))


def issued_result(result, host, index):
    """Accept only the existing issuer's fixed complete, restored-HOLD result."""
    authority.exact(result, ('audit', 'state', 'issued', 'remaining', 'cycle'))
    require(result['audit'] == 'LIGHT_LANE_ISSUER' and result['state'] == 'ISSUED_COMPLETE_HOLD'
            and type(result['issued']) is int and result['issued'] == index+1
            and type(result['remaining']) is int and result['remaining'] == len(host.policy.plans)-index-1,
            'NATIVE_SERVICE_ISSUER_RESULT')
    cycle = result['cycle']
    authority.exact(cycle, ('audit', 'state', 'plan_sha256', 'sequence', 'dispatch_id', 'task_id',
                            'terminal_sha256', 'result_code', 'controls_restored'))
    require(cycle['audit'] == 'LIGHT_LANE_CYCLE' and cycle['state'] == 'COMPLETE_HOLD'
            and cycle['plan_sha256'] == host.policy.plans[index].plan_sha256
            and type(cycle['sequence']) is int and cycle['sequence'] == host.policy.predecessor['sequence']+index+1
            and digest(cycle['terminal_sha256']) and cycle['controls_restored'] is True
            and cycle['result_code'] in ('AUDIT_PASSED', 'AUDIT_FINDINGS_REPORTED')
            and all(type(cycle[k]) is str and 0 < len(cycle[k]) <= 200
                    and all(ord(x) >= 32 for x in cycle[k]) for k in ('dispatch_id', 'task_id')),
            'NATIVE_SERVICE_ISSUER_RESULT')
    return parse(encoded(cycle))


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    """Create-only root journal; a partial intent is a permanent replay barrier.

    Production locking and metadata checks are real Linux operations. There is no
    replace/reset/delete method and no ACK-repair port. Existing issuer recovery
    requires its own independently accepted exact reconciliation request.
    """
    def __init__(self, root):
        self.root = Path(root)

    @contextmanager
    def exclusive(self):
        import fcntl
        authority.root_directory(self.root, private=True)
        path = self.root/'supervisor.lock'
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            row = os.fstat(fd)
            signature = authority.metadata_signature(row)
            require(stat.S_ISREG(row.st_mode) and row.st_uid == 0 and row.st_nlink == 1
                    and stat.S_IMODE(row.st_mode) == 0o600, 'NATIVE_SERVICE_LOCK')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LockBusy() from None
            require(authority.metadata_signature(os.fstat(fd)) == signature
                    and authority.metadata_signature(path.stat(follow_symlinks=False)) == signature,
                    'NATIVE_SERVICE_LOCK')
            yield
            require(authority.metadata_signature(os.fstat(fd)) == signature
                    and authority.metadata_signature(path.stat(follow_symlinks=False)) == signature,
                    'NATIVE_SERVICE_LOCK')
        finally:
            os.close(fd)

    def _directory(self):
        path = self.root/'journal'
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
            fsync_directory(self.root)
        authority.root_directory(path, private=True)
        return path

    def _new(self, path, raw):
        authority.root_directory(path.parent, private=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(path.parent)
        require(authority.protected_read(path, sha(raw)) == raw, 'NATIVE_SERVICE_JOURNAL_READBACK')

    def records(self):
        root = self._directory()
        names = sorted(p.name for p in root.iterdir())
        require(len(names) <= 8 and names == [f'{i:04d}' for i in range(len(names))],
                'NATIVE_SERVICE_JOURNAL_HISTORY')
        records = []
        for name in names:
            directory = root/name
            authority.root_directory(directory, private=True)
            files = {p.name for p in directory.iterdir()}
            require(files <= {'intent.json', 'done.json'}, 'NATIVE_SERVICE_JOURNAL_HISTORY')
            # Empty directory, missing/partial intent or extra records never mean READY.
            if 'intent.json' not in files:
                records.append((None, None))
                continue
            intent = parse(authority.protected_read(directory/'intent.json', limit=32768))
            done = parse(authority.protected_read(directory/'done.json', limit=32768)) if 'done.json' in files else None
            records.append((intent, done))
        return records

    def intent(self, index, value):
        path = self._directory()/f'{index:04d}'
        path.mkdir(mode=0o700)
        fsync_directory(path.parent)
        self._new(path/'intent.json', encoded(value))

    def done(self, index, value):
        self._new(self._directory()/f'{index:04d}'/'done.json', encoded(value))


def history(records, host):
    """Validate every local binding before comparing it with primary history."""
    require(len(records) <= len(host.policy.plans), 'NATIVE_SERVICE_JOURNAL_HISTORY')
    previous = host.policy.predecessor
    for index, (intent, done) in enumerate(records):
        if intent is None:
            return index, previous, True
        authority.exact(intent, ('version', 'kind', 'manifest_sha256', 'policy_sha256',
                                 'index', 'plan_sha256', 'predecessor', 'context', 'observation_sha256'))
        context = authority.ChildContext.load(encoded(intent['context']))
        authority._read_context(host.root, context.wire())
        binding = {k: v for k, v in intent.items() if k != 'context'}
        authority.issue_binding(binding, host)
        require(type(intent['version']) is int and intent['version'] == 1
                and intent['kind'] == 'LIGHT_NATIVE_SERVICE_ISSUE_INTENT'
                and intent['manifest_sha256'] == host.accepted and intent['policy_sha256'] == host.policy.digest
                and type(intent['index']) is int and intent['index'] == index
                and intent['plan_sha256'] == host.policy.plans[index].plan_sha256
                and intent['predecessor'] == previous and digest(intent['observation_sha256'])
                and context.manifest_sha256 == host.accepted and context.policy_sha256 == host.policy.digest
                and context.installation_sha256 == sha(host.installation)
                and context.controller_sha256 == host.manifest.controller_sha256
                and context.runtime_sha256 == host.manifest.runtime_sha256
                and context.source == host.source and context.purpose == 'ISSUE' and context.index == index
                and context.plan_sha256 == intent['plan_sha256']
                and context.predecessor_sha256 == sha(encoded(previous))
                and context.intent_binding_sha256 == sha(encoded(binding)), 'NATIVE_SERVICE_JOURNAL_HISTORY')
        if done is None:
            require(index == len(records)-1, 'NATIVE_SERVICE_JOURNAL_HISTORY')
            return index, previous, True
        authority.exact(done, ('version', 'kind', 'manifest_sha256', 'policy_sha256', 'index',
                               'intent_sha256', 'plan_sha256', 'sequence', 'terminal_sha256',
                               'cycle_sha256', 'readback_sha256'))
        require(type(done['version']) is int and done['version'] == 1
                and done['kind'] == 'LIGHT_NATIVE_SERVICE_ISSUE_DONE'
                and done['manifest_sha256'] == host.accepted and done['policy_sha256'] == host.policy.digest
                and type(done['index']) is int and done['index'] == index
                and done['intent_sha256'] == sha(encoded(intent))
                and done['plan_sha256'] == intent['plan_sha256']
                and type(done['sequence']) is int and done['sequence'] == previous['sequence']+1
                and all(digest(done[k]) for k in ('terminal_sha256', 'cycle_sha256', 'readback_sha256')),
                'NATIVE_SERVICE_JOURNAL_HISTORY')
        previous = dict(plan_sha256=done['plan_sha256'], terminal_sha256=done['terminal_sha256'], sequence=done['sequence'])
    return len(records), previous, False


class Supervisor:
    def __init__(self, host, *, primary_observe, issue_once):
        require(type(host) is authority.HostAuthority and callable(primary_observe) and callable(issue_once),
                'NATIVE_SERVICE_PORTS')
        self.host = host
        self.journal = Journal(host.root)
        self.primary_observe = primary_observe
        self.issue_once = issue_once

    def _observe(self, context):
        self.host.assert_current()
        value = self.primary_observe(context)
        self.host.assert_current()
        require(time.time() < context.expires_at and time.monotonic_ns() < context.monotonic_end_ns,
                'NATIVE_SERVICE_CHILD_EXPIRED')
        return observation(value, self.host, context)

    def step(self):
        """One event, at most one issue; restarts always observe before deciding."""
        issued = 0
        try:
            with self.journal.exclusive():
                self.host.assert_current()
                context = self.host.child_context()
                # Always query primary sources first, including after a crash.
                before = self._observe(context)
                records = self.journal.records()
                issued, previous, partial = history(records, self.host)
                if partial or before['state'] in BLOCKED:
                    return Outcome('RECONCILIATION_REQUIRED', issued)
                require(before['issued'] == issued and before['predecessor'] == previous,
                        'NATIVE_SERVICE_PRIMARY_HISTORY_MISMATCH')
                if issued:
                    require(before['cycle_sha256'] == records[-1][1]['cycle_sha256'],
                            'NATIVE_SERVICE_PRIMARY_HISTORY_MISMATCH')
                if issued == len(self.host.policy.plans):
                    require(before['state'] in ('EXHAUSTED', 'COMPLETE_ACKNOWLEDGED'), 'NATIVE_SERVICE_PRIMARY')
                    return Outcome('EXHAUSTED', issued)
                require(before['state'] in ('READY', 'COMPLETE_ACKNOWLEDGED'), 'NATIVE_SERVICE_PRIMARY')
                binding = dict(version=1, kind='LIGHT_NATIVE_SERVICE_ISSUE_INTENT',
                    manifest_sha256=self.host.accepted, policy_sha256=self.host.policy.digest,
                    index=issued, plan_sha256=self.host.policy.plans[issued].plan_sha256,
                    predecessor=previous, observation_sha256=sha(encoded(before)))
                context = self.host.child_context(purpose='ISSUE', binding=binding)
                # Existing issuer needs >=960s remaining at its final intent check.
                require(context.expires_at-time.time() >= 1000
                        and context.monotonic_end_ns-time.monotonic_ns() >= 1000*10**9,
                        'NATIVE_SERVICE_WINDOW')
                intent = dict(**binding, context=asdict(context))
                self.host.assert_current()
                self.journal.intent(issued, intent)
                # Guard again after durable intent. Any failure leaves a replay barrier.
                self.host.assert_current()
                cycle = issued_result(self.issue_once(context), self.host, issued)
                after = self._observe(self.host.child_context())
                expected = dict(plan_sha256=cycle['plan_sha256'], terminal_sha256=cycle['terminal_sha256'],
                                sequence=cycle['sequence'])
                require(after['state'] == 'COMPLETE_ACKNOWLEDGED' and after['issued'] == issued+1
                        and after['predecessor'] == expected and after['cycle_sha256'] == sha(encoded(cycle)),
                        'NATIVE_SERVICE_PRIMARY_RESULT_MISMATCH')
                self.host.assert_current()
                done = dict(version=1, kind='LIGHT_NATIVE_SERVICE_ISSUE_DONE',
                    manifest_sha256=self.host.accepted, policy_sha256=self.host.policy.digest,
                    index=issued, intent_sha256=sha(encoded(intent)), plan_sha256=cycle['plan_sha256'],
                    sequence=cycle['sequence'], terminal_sha256=cycle['terminal_sha256'],
                    cycle_sha256=sha(encoded(cycle)), readback_sha256=sha(encoded(after)))
                self.journal.done(issued, done)
                count, actual, partial = history(self.journal.records(), self.host)
                require(count == issued+1 and actual == expected and not partial, 'NATIVE_SERVICE_JOURNAL_READBACK')
                return Outcome('ISSUED_COMPLETE_HOLD', count)
        except LockBusy:
            return Outcome('BUSY', issued)
        except Exception:
            # Never log exception text, infer completion, delete intents, or retry.
            return Outcome('AUTHORITY_CLOSED' if self.host.failed else 'RECONCILIATION_REQUIRED', issued)

    def serve(self, shutdown, wakeup=None, *, heartbeat_seconds=30):
        """Remain installed/dormant after finite authority closes; no renewal API.

        The installed runtime supplies signal-backed Events. Events only wake
        reconciliation; neither a signal nor a heartbeat is task authority.
        """
        require(type(shutdown) is threading.Event and (wakeup is None or type(wakeup) is threading.Event)
                and type(heartbeat_seconds) is int and 1 <= heartbeat_seconds <= 60,
                'NATIVE_SERVICE_EVENTS')
        wakeup = threading.Event() if wakeup is None else wakeup
        last = Outcome('DORMANT', 0)
        while not shutdown.is_set():
            last = self.step()
            if last.state == 'ISSUED_COMPLETE_HOLD':
                continue
            wakeup.wait(heartbeat_seconds)
            wakeup.clear()
        return last
