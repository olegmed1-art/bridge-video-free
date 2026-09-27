"""Scoped, nonrenewing operator window; no credentials or database imports."""
from datetime import datetime, timezone
import json
import re
import time
from ops.native_maintenance_workflow_pause import digest, require

COVERAGE = ['direct_owner_sql', 'host_administration', 'workflow_administration', 'workflow_reruns', 'main_pushes']


class Agreement:
    """Validate independently approved bytes, not their own self-computed hash.

    accepted_digest must come from the separate review/owner acceptance path.
    No helper in this module mints approvals or extends a supplied window.
    """
    def __init__(self, record, accepted_digest, scope):
        require(type(record) is dict and set(record) == {
            'version', 'owner', 'operation_digest', 'not_before', 'expires_at', 'coverage', 'evidence'},
            'COORDINATION_AGREEMENT_SHAPE')
        require(re.fullmatch('[0-9a-f]{64}', accepted_digest or '')
                and digest(record) == accepted_digest, 'COORDINATION_AGREEMENT_NOT_ACCEPTED')
        spec = {k: v for k, v in scope.items() if k != 'origin_run'}
        require(record['version'] == 1 and type(record['version']) is int
                and record['owner'] == 'olegmed1-art' and record['coverage'] == COVERAGE
                and record['operation_digest'] == digest(spec)
                and type(record['evidence']) is str and 0 < len(record['evidence']) <= 1024,
                'COORDINATION_AGREEMENT_SCOPE')
        self.record = json.loads(json.dumps(record))
        self.accepted = accepted_digest
        self.scope = digest(scope)
        self.start, self.end = [self.timestamp(record[k]) for k in ('not_before', 'expires_at')]
        require(0 < self.end - self.start <= 1800, 'COORDINATION_AGREEMENT_DURATION')
        now = time.time()
        require(self.start <= now < self.end, 'COORDINATION_AGREEMENT_NOT_CURRENT')
        self.deadline = time.monotonic() + self.end - now
        self.last_wall = now
        self.failed = False

    @staticmethod
    def timestamp(value):
        require(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value),
                'COORDINATION_AGREEMENT_TIME')
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()

    def assert_held(self, scope):
        require(not self.failed, 'COORDINATION_AGREEMENT_ALREADY_FAILED')
        try:
            now = time.time()
            require(scope == self.scope and digest(self.record) == self.accepted,
                    'COORDINATION_AGREEMENT_CHANGED')
            require(self.start <= now < self.end and now >= self.last_wall
                    and time.monotonic() < self.deadline, 'COORDINATION_AGREEMENT_EXPIRED')
            self.last_wall = now
        except BaseException:
            self.failed = True
            raise

