"""Serial READ_ONLY consumer for an owner-reviewed feed; not a work producer.

Dormant until separately installed. The owner supplies each new, finite permit
and accepts the previous terminal digest. This service never mints admission,
renews a permit, enables SQL controls, runs the legacy scheduler, or clears a
quarantine. A permanent process does not imply permanent task authorization.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import time

from . import codex_cli_bridge as bridge
from .light_native_control import root_bytes
from .light_native_pilot import Claim, Permit, Session, require

CONTROL = Path('/etc/bridge-school/light-native-lane')
STATE = bridge.LIGHT_ROOT / 'runtime/native-lane'
HEX = re.compile('[0-9a-f]{64}')
RECORD = re.compile(r'([0-9]{8})-(intent|terminal)\.json')
MAX_JOBS = 10000


def encoded(value):
    return bridge.canonical(value).encode()


def digest(raw):
    return bridge.digest(raw.decode('utf-8'))


def parse(raw):
    value = bridge.parse(raw.decode('utf-8'))
    require(type(value) is dict and encoded(value) == raw, 'LANE_RECORD_INVALID')
    return value


def entry(raw, source):
    value = parse(raw)
    require(set(value) == {'version', 'source', 'sequence', 'dispatch_id',
                           'permit_sha256', 'previous_terminal_sha256'}
            and type(value['version']) is int and value['version'] == 1
            and value['source'] == source and bridge.SHA.fullmatch(source)
            and type(value['sequence']) is int and 0 <= value['sequence'] < MAX_JOBS
            and type(value['dispatch_id']) is str and bridge.UUID.fullmatch(value['dispatch_id'])
            and type(value['permit_sha256']) is str and HEX.fullmatch(value['permit_sha256'])
            and (value['previous_terminal_sha256'] is None if value['sequence'] == 0
                 else type(value['previous_terminal_sha256']) is str
                 and HEX.fullmatch(value['previous_terminal_sha256'])), 'LANE_FEED_INVALID')
    return value


def terminal_record(intent_raw, request, result):
    intent = parse(intent_raw)
    bridge.validate_request(request)
    require(type(result) is dict and set(result) == {
        'state', 'dispatch_id', 'provider_task_id', 'terminal'}
        and result['state'] == 'DONE'
        and result['dispatch_id'] == request['dispatch_id'] == intent['dispatch_id']
        and type(result['provider_task_id']) is str
        and bridge.TASK_URL.fullmatch('https://chatgpt.com/codex/tasks/' + result['provider_task_id'])
        and type(result['terminal']) is dict
        and result['terminal'].get('status') in ('SUCCEEDED', 'BLOCKED')
        and type(result['terminal'].get('provider_evidence_sha256')) is str
        and HEX.fullmatch(result['terminal']['provider_evidence_sha256']), 'LANE_TERMINAL_INVALID')
    return dict(version=1, intent_sha256=digest(intent_raw), request=request, result=result)


def acceptance_record(intent_raw, terminal_raw):
    """Expected shape only; the owner verifier supplies primary evidence."""
    intent, terminal = parse(intent_raw), parse(terminal_raw)
    require(terminal == terminal_record(intent_raw, terminal['request'], terminal['result']),
            'LANE_TERMINAL_CHANGED')
    return dict(version=1, source=intent['source'], sequence=intent['sequence'],
                dispatch_id=intent['dispatch_id'], terminal_sha256=digest(terminal_raw),
                provider_evidence_sha256=terminal['result']['terminal']['provider_evidence_sha256'])


class Journal:
    """One local flock and immutable fsynced records, including a durable latch."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = Claim(self.path)

    def close(self):
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, name):
        self.lock.check()
        require(name == 'quarantine.json' or RECORD.fullmatch(name), 'LANE_RECORD_NAME')
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.lock.fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            Claim.regular(stream.fileno())
            raw = stream.read(262145)
        require(0 < len(raw) <= 262144, 'LANE_RECORD_SIZE')
        return raw

    def retain(self, name, raw):
        prior = self.read(name)
        if prior is None:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self.lock.fd)
            with os.fdopen(fd, 'wb') as stream:
                Claim.regular(stream.fileno())
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(self.lock.fd)
        require(self.read(name) == raw, 'LANE_RECORD_CONFLICT')

    def quarantine(self):
        # Fixed text only: driver exceptions may include credentials/task text.
        self.retain('quarantine.json', encoded({'version': 1, 'state': 'QUARANTINED'}))

    def history(self, source):
        require(self.read('quarantine.json') is None, 'LANE_QUARANTINED')
        names = os.listdir(self.lock.fd)
        records = [name for name in names if RECORD.fullmatch(name)]
        require(len(records) <= MAX_JOBS * 2
                and all(name == 'pilot.lock' or RECORD.fullmatch(name)
                        or re.fullmatch(r'claim-[0-9a-f-]{36}', name) for name in names),
                'LANE_JOURNAL_CONTENTS')
        numbers = sorted({int(RECORD.fullmatch(name)[1]) for name in records})
        require(numbers == list(range(len(numbers))), 'LANE_JOURNAL_GAP')
        previous = None
        seen = set()
        history = []
        for number in numbers:
            raw = self.read(f'{number:08d}-intent.json')
            require(raw is not None, 'LANE_INTENT_MISSING')
            value = entry(raw, source)
            require(value['sequence'] == number and value['previous_terminal_sha256'] == previous
                    and value['dispatch_id'] not in seen, 'LANE_CHAIN_CHANGED')
            seen.add(value['dispatch_id'])
            done = self.read(f'{number:08d}-terminal.json')
            if done is not None:
                record = parse(done)
                require(set(record) == {'version', 'intent_sha256', 'request', 'result'}
                        and record == terminal_record(raw, record['request'], record['result']),
                        'LANE_TERMINAL_CHANGED')
                with Claim(self.path / ('claim-' + value['dispatch_id']), create_lock=False) as claim:
                    claim.exact('request.json', encoded(record['request']))
                    fd = os.open('permit.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=claim.fd)
                    with os.fdopen(fd, 'rb') as stream:
                        Claim.regular(stream.fileno())
                        permit_raw = stream.read(65537)
                    require(0 < len(permit_raw) <= 65536
                            and digest(permit_raw) == value['permit_sha256'], 'LANE_CLAIM_CHANGED')
                previous = digest(done)
            else:
                require(number == numbers[-1], 'LANE_UNFINISHED_PREDECESSOR')
            history.append((raw, done))
        require({name for name in names if name.startswith('claim-')}
                <= {'claim-' + dispatch for dispatch in seen}, 'LANE_ORPHAN_CLAIM')
        return history

    def claim_path(self, dispatch_id):
        path = self.path / ('claim-' + dispatch_id)
        try:
            os.mkdir(path, 0o700)
            os.fsync(self.lock.fd)
        except FileExistsError:
            pass  # Claim checks type, ownership, inode and private mode.
        return path


class OwnerFeed:
    def __init__(self, source):
        self.source = source

    def admitted(self):
        return (os.environ.get('AUTOPILOT_ADMISSION_MODE') == 'NATIVE'
                and root_bytes(CONTROL / 'admission', 16) == b'RUN\n')

    def current(self):
        return root_bytes(CONTROL / 'current.json', 4096)

    def acceptance(self, value):
        return root_bytes(CONTROL / 'jobs' / value['dispatch_id'] / 'accepted-terminal.json', 4096)

    def permit(self, value):
        reader = lambda: root_bytes(CONTROL / 'jobs' / value['dispatch_id'] / 'permit.json', 65536)
        return Permit(reader(), value['permit_sha256'], reader, self.source)


class Lane:
    def __init__(self, source, journal, feed, execute):
        self.source, self.journal, self.feed, self.execute = source, journal, feed, execute
        self.failed = False

    def tick(self):
        require(not self.failed, 'LANE_QUARANTINED')
        try:
            return self._tick()
        except BaseException:
            self.failed = True
            self.journal.quarantine()
            raise

    def _tick(self):
        history = self.journal.history(self.source)
        active = bool(history and history[-1][1] is None)
        # execute is synchronous: an unfinished entry here is an interrupted
        # process, never permission to resume an uncertain provider operation.
        require(not active, 'LANE_INTERRUPTED_JOB')
        if self.feed.admitted() is not True:
            require(not active, 'LANE_HOLD_DURING_JOB')
            return {'state': 'HOLD'}
        raw = self.feed.current()
        value = entry(raw, self.source)
        sequence = value['sequence']
        if history and raw == history[-1][0] and history[-1][1] is not None:
            return {'state': 'AWAITING_OWNER', 'terminal_sha256': digest(history[-1][1])}
        require(sequence == len(history), 'LANE_SEQUENCE_CHANGED')
        previous = digest(history[-1][1]) if history else None
        require(value['previous_terminal_sha256'] == previous
                and value['dispatch_id'] not in {parse(item[0])['dispatch_id'] for item in history},
                'LANE_PREDECESSOR_NOT_ACCEPTED')
        if history and not active:
            prior_intent, prior_done = history[-1]
            prior = parse(prior_intent)
            accepted = parse(self.feed.acceptance(prior))
            require(accepted == acceptance_record(prior_intent, prior_done),
                    'LANE_PREDECESSOR_NOT_ACCEPTED')
        permit = self.feed.permit(value)
        require(type(permit) is Permit and permit.raw is not None
                and digest(permit.raw) == value['permit_sha256']
                and permit.value['dispatch']['dispatch_id'] == value['dispatch_id'], 'LANE_PERMIT_BINDING')
        permit.check()
        name = f'{sequence:08d}-intent.json'
        self.journal.retain(name, raw)  # Before database reservation/provider effects.

        def admitted():
            self.journal.lock.check()
            require(self.journal.read('quarantine.json') is None
                    and self.journal.read(name) == raw, 'LANE_JOURNAL_CHANGED')
            return self.feed.admitted() is True and self.feed.current() == raw

        require(admitted(), 'LANE_NOT_ADMITTED')
        with Claim(self.journal.claim_path(value['dispatch_id'])) as claim:
            request, result = self.execute(permit, claim, admitted)
            require(admitted(), 'LANE_NOT_ADMITTED')
            permit.bind(request)
            claim.exact('request.json', encoded(request))
            done = encoded(terminal_record(raw, request, result))
            self.journal.retain(f'{sequence:08d}-terminal.json', done)
        # A root-authored next cursor with this exact digest is the owner's
        # acceptance of independent DB/provider/task readback, never a model ACK.
        return {'state': 'AWAITING_OWNER', 'terminal_sha256': digest(done)}


def execute_job(permit, claim, admitted):
    """Use the proven Session/queue/provider path on one direct DB connection."""
    import psycopg
    from .light_native_loader import runtime_parameters, runtime_identity, read_pr
    from .light_native_provider import LightProvider
    with psycopg.connect(**runtime_parameters(os.environ.get('AUTOPILOT_DATABASE_URL', ''))) as conn:
        runtime_identity(conn)
        session = Session(permit, claim, conn, read_pr, LightProvider(permit.target), admission=admitted)
        request = session.reserve()
        while True:
            result = session.step()
            if result['state'] == 'DONE':
                return request, result
            time.sleep(10)


def main():
    # No connection or provider construction before explicit live admission.
    from .light_native_loader import release_source
    with ExitStack() as stack:
        try:
            source = release_source()
            journal = stack.enter_context(Journal(STATE))
            lane = Lane(source, journal, OwnerFeed(source), execute_job)
            last = None
            while True:
                result = lane.tick()
                if result != last:
                    print(bridge.canonical(dict(audit='LIGHT_NATIVE_LANE', **result)), flush=True)
                    last = result
                time.sleep(10)
        except BaseException:
            print('{"audit":"LIGHT_NATIVE_LANE_QUARANTINED"}', flush=True)
        # Keep the global lock and latch alive; no Restart=always retry cycle.
        while True:
            time.sleep(30)


if __name__ == '__main__':
    main()
