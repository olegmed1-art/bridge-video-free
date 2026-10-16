"""One controlled image restart after durable ACK, within the original unit.

No unit restart, retry, authority renewal or provider creation is implemented
here. Missing or conflicting records quarantine instead of repeating the test.
"""
import math
import os
import re
import secrets

from . import codex_cli_bridge as bridge
from .light_native_pilot import require

IMAGE = secrets.token_hex(32)
NAMES = ('image-start.json', 'image-restart.json', 'image-resumed.json')


class RestartImage(BaseException):
    """Unwind the DB session and local lock before fixed-argv exec."""


def read(claim, name):
    claim.check()
    require(name in NAMES, 'PILOT_IMAGE_RECORD_NAME')
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=claim.fd)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        claim.regular(stream.fileno())
        raw = stream.read(8193)
    require(0 < len(raw) <= 8192, 'PILOT_IMAGE_RECORD_SIZE')
    value = bridge.parse(raw.decode())
    require(type(value) is dict and bridge.canonical(value).encode() == raw, 'PILOT_IMAGE_RECORD')
    claim.check()
    return value


def create(claim, name, value):
    claim.check()
    require(name in NAMES, 'PILOT_IMAGE_RECORD_NAME')
    raw = bridge.canonical(value).encode()
    require(len(raw) <= 8192, 'PILOT_IMAGE_RECORD_SIZE')
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=claim.fd)
    with os.fdopen(fd, 'wb') as stream:
        claim.regular(stream.fileno())
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(claim.fd)
    require(read(claim, name) == value, 'PILOT_IMAGE_RECORD_READBACK')


def digest(value):
    return bridge.digest(bridge.canonical(value))


def proof(claim, permit_raw, request, provider_id):
    """Read-only retained evidence; caller separately proves DB/provider/HOLD."""
    start, intent, resumed = (read(claim, name) for name in NAMES)
    return validate_proof(start, intent, resumed, permit_raw, request, provider_id)


def validate_proof(start, intent, resumed, permit_raw, request, provider_id):
    permit = bridge.parse(permit_raw.decode())
    bridge.validate_request(request)
    require({k: v for k, v in request.items() if k != 'reservation_id'} == permit['dispatch'],
            'PILOT_IMAGE_DISPATCH')
    require(type(start) is dict and set(start) == {'version', 'source', 'permit_sha256',
        'pid', 'invocation_id', 'image_id', 'deadline', 'last_wall'}
        and type(start['version']) is int and start['version'] == 1
        and start['source'] == permit['source']
        and start['permit_sha256'] == bridge.digest(permit_raw.decode())
        and type(start['pid']) is int and start['pid'] > 0
        and re.fullmatch('[0-9a-f]{32}', start['invocation_id'] or '')
        and re.fullmatch('[0-9a-f]{64}', start['image_id'] or '')
        and all(type(start[k]) in (int, float) and math.isfinite(start[k])
                for k in ('deadline', 'last_wall')), 'PILOT_IMAGE_START')
    require(type(intent) is dict and set(intent) == {'version', 'start_sha256',
        'request_sha256', 'dispatch_id', 'provider_task_id', 'prompt_sha256', 'last_wall'}
        and type(intent['version']) is int and intent['version'] == 1
        and intent['start_sha256'] == digest(start)
        and intent['request_sha256'] == digest(request)
        and intent['dispatch_id'] == request['dispatch_id']
        and intent['provider_task_id'] == provider_id
        and intent['prompt_sha256'] == bridge.digest(bridge.prompt_for(request))
        and type(intent['last_wall']) in (int, float) and math.isfinite(intent['last_wall'])
        and permit['issued_at'] <= start['last_wall'] <= intent['last_wall'] < permit['expires_at'],
        'PILOT_IMAGE_INTENT')
    require(type(resumed) is dict and set(resumed) == {'version', 'intent_sha256',
        'image_id', 'pid', 'invocation_id'}
        and type(resumed['version']) is int and resumed['version'] == 1
        and resumed['intent_sha256'] == digest(intent)
        and re.fullmatch('[0-9a-f]{64}', resumed['image_id'] or '')
        and resumed['image_id'] != start['image_id']
        and type(resumed['pid']) is int and resumed['pid'] == start['pid']
        and resumed['invocation_id'] == start['invocation_id'], 'PILOT_IMAGE_RESUMED')
    return dict(version=1, kind='CONTROLLED_IMAGE_RESTART', dispatch_id=request['dispatch_id'],
        provider_task_id=provider_id, pid=start['pid'], invocation_id=start['invocation_id'],
        start_sha256=digest(start), intent_sha256=digest(intent), resumed_sha256=digest(resumed))


class RestartingProvider:
    def __init__(self, provider, permit, claim):
        self.provider, self.permit, self.claim = provider, permit, claim
        self.session = None
        self.image = IMAGE
        self.invocation = os.environ.get('INVOCATION_ID', '')
        require(re.fullmatch('[0-9a-f]{32}', self.invocation), 'PILOT_IMAGE_UNIT')
        permit.check()
        self.start = read(claim, NAMES[0])
        self.resuming = self.start is not None
        self.intent = read(claim, NAMES[1])
        require(read(claim, NAMES[2]) is None, 'PILOT_IMAGE_ALREADY_RESUMED')
        if not self.resuming:
            require(self.intent is None and claim.prior_request() is None, 'PILOT_IMAGE_START_LOST')
            self.start = dict(version=1, source=permit.value['source'],
                permit_sha256=bridge.digest(permit.raw.decode()), pid=os.getpid(),
                invocation_id=self.invocation, image_id=self.image,
                deadline=permit.deadline, last_wall=permit.last_wall)
            create(claim, NAMES[0], self.start)
        else:
            require(type(self.intent) is dict and claim.prior_request() is not None,
                    'PILOT_IMAGE_RESTART_MISSING')
            # Validate all retained fields with the prospective resume record before
            # touching DB/provider. No accepted permit or monotonic time is renewed.
            require(self.start.get('pid') == os.getpid()
                    and self.start.get('invocation_id') == self.invocation
                    and self.start.get('image_id') != self.image, 'PILOT_IMAGE_IDENTITY')
            require(self.start.get('permit_sha256') == bridge.digest(permit.raw.decode())
                    and self.start.get('source') == permit.value['source']
                    and self.intent.get('start_sha256') == digest(self.start), 'PILOT_IMAGE_BINDING')
            for value in (self.start.get('deadline'), self.intent.get('last_wall')):
                require(type(value) in (int, float) and math.isfinite(value), 'PILOT_IMAGE_CLOCK')
            permit.deadline = min(permit.deadline, self.start['deadline'])
            permit.last_wall = max(permit.last_wall, self.intent['last_wall'])
            permit.check()

    def attach(self, session):
        self.session = session
        if self.resuming:
            row, creation = self.bound_submission()
            require(self.intent == self.intent_value(row, creation, self.intent['last_wall']),
                    'PILOT_IMAGE_RESUME_CONFLICT')
            resumed = dict(version=1, intent_sha256=digest(self.intent),
                image_id=self.image, pid=os.getpid(), invocation_id=self.invocation)
            validate_proof(self.start, self.intent, resumed, self.permit.raw,
                           session.request, row['provider_task_id'])
            create(self.claim, NAMES[2], resumed)
            proof(self.claim, self.permit.raw, session.request, row['provider_task_id'])
            session.check()
        else:
            row = session.queue.snapshot(session.request['dispatch_id'])
            require(row['state'] == 'RESERVED' and row['request'] == session.request
                    and self.provider.lookup(session.request, target=self.permit.target) is None,
                    'PILOT_IMAGE_FRESH_RESERVATION_REQUIRED')
            session.check()

    def bound_submission(self):
        self.session.check()
        request = self.session.request
        row = self.session.queue.snapshot(request['dispatch_id'])
        require(row['state'] == 'SUBMITTED' and row['request'] == request, 'PILOT_IMAGE_ACK_REQUIRED')
        creation = self.provider.lookup(request, target=self.permit.target)
        require(type(creation) is dict and creation.get('state') == 'SUBMITTED'
                and creation.get('provider_task_id') == row['provider_task_id']
                and bridge.TASK_URL.fullmatch('https://chatgpt.com/codex/tasks/'+row['provider_task_id'])
                and creation.get('prompt_sha256') == bridge.digest(bridge.prompt_for(request)),
                'PILOT_IMAGE_PROVIDER_CONFLICT')
        self.session.check()
        return row, creation

    def intent_value(self, row, creation, wall):
        return dict(version=1, start_sha256=digest(self.start), request_sha256=digest(row['request']),
            dispatch_id=row['request']['dispatch_id'], provider_task_id=row['provider_task_id'],
            prompt_sha256=creation['prompt_sha256'], last_wall=wall)

    def lookup(self, request, *, target):
        return self.provider.lookup(request, target=target)

    def submit(self, request, *, target):
        require(not self.resuming, 'PILOT_IMAGE_RESUBMIT_FORBIDDEN')
        return self.provider.submit(request, target=target)

    def collect(self, dispatch_id, *, target):
        require(target == self.permit.target and dispatch_id == self.session.request['dispatch_id'],
                'PILOT_IMAGE_TARGET')
        row, creation = self.bound_submission()
        require(read(self.claim, NAMES[0]) == self.start, 'PILOT_IMAGE_START_CHANGED')
        if not self.resuming:
            self.intent = self.intent_value(row, creation, self.permit.last_wall)
            create(self.claim, NAMES[1], self.intent)
            self.session.check()
            raise RestartImage()
        evidence = proof(self.claim, self.permit.raw, self.session.request, row['provider_task_id'])
        require(read(self.claim, NAMES[2])['image_id'] == self.image
                and evidence['pid'] == os.getpid(), 'PILOT_IMAGE_RESUME_CHANGED')
        self.session.check()
        return self.provider.collect(dispatch_id, target=target)
