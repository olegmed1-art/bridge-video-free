"""Fixed incident authority: execution commit and historical operation are distinct.

No stage RunBinding impersonation, approval minting, window renewal, or retry.
The owner dispatch supplies canonical request bytes and an independently reviewed
execution commit. This profile grants only completion of the named AFTER restore.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import re
import time

SOURCE = '8bbc1d61010ef70c3fca02b5151ac86fce02a144'
SCOPE = 'c0795e9e1533c3baf554a0d785b303180d8bf6771d07e6d98e5f67f0b96ef542'
HEAD = '4da1bcf5d0aef685289dc3c72cbf710f94f9508f41cab1380c87c1f6a29d6dac'
PAIR = '9d427c665d3248b6b8d8ee730e8acf2a5539ae65499576c8075f2e35b59ec99a'
BRANCH = 'recovery/light-after-incident-20260928'
WORKFLOW = '.github/workflows/native-maintenance-stages.yml'
REPO = 'olegmed1-art/bridge-video-free'
OWNER = 'olegmed1-art'
OWNER_ID = 315099490
PREFIX = 'native-journal/incident-restore-20260928/' + SCOPE + '/'
CLAIM = hashlib.sha256(('LIGHT_INCIDENT_RESTORE_ONCE:' + SCOPE).encode()).hexdigest()


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'INCIDENT_DUPLICATE_KEY')
        result[key] = value
    return result


def authorization(raw, execution):
    require(type(raw) is bytes and 0 < len(raw) <= 4096
            and re.fullmatch('[0-9a-f]{40}', execution or ''), 'INCIDENT_AUTH_INPUT')
    v = json.loads(raw, object_pairs_hook=unique)
    require(type(v) is dict and encoded(v) == raw and set(v) == {
        'version','purpose','source','scope','head','pair','execution','nonce','not_before','expires_at',
        'owner','coverage','evidence'}
        and type(v['version']) is int and v['version'] == 1
        and v['owner'] == OWNER
        and v['coverage'] == ['direct_owner_sql','host_administration','workflow_administration','workflow_reruns','main_pushes']
        and type(v['evidence']) is str and 0 < len(v['evidence']) <= 1024
        and v['purpose'] == 'complete_existing_restore_AFTER_only'
        and v['source'] == SOURCE and v['scope'] == SCOPE and v['head'] == HEAD and v['pair'] == PAIR
        and v['execution'] == execution and re.fullmatch('[0-9a-f]{32}', v['nonce'] or ''),
        'INCIDENT_AUTH_BINDING')
    times = []
    for key in ('not_before','expires_at'):
        require(type(v[key]) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',v[key]),
                'INCIDENT_AUTH_TIME')
        times.append(datetime.strptime(v[key], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())
    require(0 < times[1] - times[0] <= 1800, 'INCIDENT_AUTH_DURATION')
    return v, times


class Authority:
    def __init__(self, raw, execution, run_id, attempt, api, workflow_digest, *, host):
        self.value, (self.start, self.end) = authorization(raw, execution)
        require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0
                and re.fullmatch('[0-9a-f]{64}', workflow_digest or ''), 'INCIDENT_RUN_INPUT')
        self.raw, self.accepted, self.execution = raw, sha(raw), execution
        self.run_id, self.attempt, self.api = run_id, attempt, api
        self.workflow_digest = workflow_digest
        self.last_wall = time.time()
        self.anchor = (time.monotonic(), self.last_wall)
        self.deadline = min(self.anchor[0] + (540 if host else 900), self.anchor[0] + self.end-self.last_wall)
        self.job_id = None
        self.failed = False
        self.assert_current()

    def assert_current(self):
        try:
            wall, mono = time.time(), time.monotonic()
            require(not self.failed and encoded(self.value) == self.raw and sha(self.raw) == self.accepted
                    and self.start <= wall < self.end and wall >= self.last_wall
                    and mono < self.deadline
                    and abs((wall-self.anchor[1])-(mono-self.anchor[0])) <= 2,
                    'INCIDENT_AUTH_EXPIRED_OR_CHANGED')
            self.last_wall = wall
        except BaseException:
            self.failed = True
            raise

    def assert_live(self):
        self.assert_current()
        try:
            run = self.api.get('/actions/runs/' + str(self.run_id))
            require(run['id'] == self.run_id and run['run_attempt'] == self.attempt
                and run['head_sha'] == self.execution and run['head_branch'] == BRANCH
                and run['event'] == 'workflow_dispatch' and run['status'] == 'in_progress'
                and run['conclusion'] is None and run['path'] == WORKFLOW
                and run['repository']['full_name'] == run['head_repository']['full_name'] == REPO
                and all(run[k]['login'] == OWNER and type(run[k]['id']) is int
                    and run[k]['id'] == OWNER_ID for k in ('actor','triggering_actor')),
                'INCIDENT_LIVE_RUN')
            jobs = self.api.get(f'/actions/runs/{self.run_id}/attempts/{self.attempt}/jobs?per_page=100')
            rows = jobs['jobs']
            require(type(jobs['total_count']) is int and jobs['total_count'] == 2
                and type(rows) is list and len(rows) == 2
                and sorted(j['name'] for j in rows) == ['contract','restore']
                and len({j['id'] for j in rows}) == 2
                and all(type(j['id']) is int and j['id'] > 0 and j['run_id'] == self.run_id
                    and j['run_attempt'] == self.attempt and j['head_sha'] == self.execution
                    and (j['status'],j['conclusion']) == (('completed','success') if j['name']=='contract'
                                                        else ('in_progress',None)) for j in rows),
                'INCIDENT_LIVE_JOBS')
            job = next(j for j in rows if j['name'] == 'restore')
            require(self.job_id is None or self.job_id == job['id'], 'INCIDENT_JOB_CHANGED')
            main = self.api.get('/git/ref/heads/main')
            require(main['ref'] == 'refs/heads/main' and main['object']['type'] == 'commit'
                    and main['object']['sha'] == SOURCE, 'INCIDENT_MAIN_CHANGED')
            blob = self.api.get('/contents/' + WORKFLOW + '?ref=' + self.execution)
            require(blob['type'] == 'file' and blob['path'] == WORKFLOW and blob['encoding'] == 'base64'
                    and 0 < blob['size'] <= 32768, 'INCIDENT_WORKFLOW')
            raw = base64.b64decode(blob['content'].replace('\n',''), validate=True)
            require(len(raw) == blob['size'] and sha(raw) == self.workflow_digest, 'INCIDENT_WORKFLOW')
            self.assert_current()
            self.job_id = job['id']
        except BaseException:
            self.failed = True
            raise

    @property
    def run_identity(self):
        require(type(self.job_id) is int and self.job_id > 0, 'INCIDENT_JOB_UNOBSERVED')
        return dict(run_id=self.run_id, attempt=self.attempt, job_id=self.job_id)
