"""Dormant single-reservation composition; no executable entrypoint or scheduler.

All ports are trusted integration dependencies, never task/model inputs. The
future production loader must independently verify the service DB identity,
Light provider profile and cloud environment/repository binding. This module
does not provide that loader or authorize activation. A gate must read current
admission and the separately approved exact pilot binding on EVERY call.
"""
from copy import deepcopy
from dataclasses import dataclass
import re

from . import codex_cli_bridge as bridge
from .codex_cli_delivery import advance
from .codex_cli_queue import NativeAuthority, NativeQueue


@dataclass(frozen=True)
class ProviderTarget:
    """Trusted loader's verified binding; not proof of cloud access by itself."""
    environment_id: str
    profile: str = 'light'
    repository: str = 'olegmed1-art/bridge-video-free'

    def __post_init__(self):
        if (self.profile != 'light'
                or self.repository != 'olegmed1-art/bridge-video-free'
                or not isinstance(self.environment_id, str)
                or self.environment_id == 'bridge-video-free'
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', self.environment_id)):
            raise ValueError('LIGHT_PROVIDER_TARGET_INVALID')


FALSE_FLAGS = ('repository_mutation', 'production_mutation', 'canon_mutation',
               'neon_mutation', 'server_mutation', 'external_mutation',
               'media_execution', 'paid_action', 'merge', 'deploy')


class LightNativeAdapter:
    """Reconcile only the immutable request chosen at construction.

    gate(request, target) must return exactly True only while that one-item pilot is
    admitted. HOLD, missing/expired approval, unavailable evidence, or any
    binding change must deny. No environment-variable default enables this.
    Checks are cooperative boundaries, not an atomic revocation mechanism;
    DB fencing and a serialized production controller remain required.
    """

    def __init__(self, request, *, profile, target, rpc, read_pr, provider, gate):
        # The legacy global bridge is deliberately incompatible: it neither
        # accepts nor persists target. A future provider must durably bind all
        # three target fields BEFORE creation and reject a changed replay.
        if profile != 'light':
            raise ValueError('LIGHT_PROFILE_REQUIRED')
        if type(target) is not ProviderTarget:
            raise ValueError('LIGHT_PROVIDER_TARGET_REQUIRED')
        self._target = target
        self._request = bridge.validate_request(bridge.parse(bridge.canonical(request)))
        self._request_json = bridge.canonical(self._request)
        if self._request['mode'] not in ('READ_ONLY', 'VERIFY'):
            raise ValueError('LIGHT_PILOT_MODE_FORBIDDEN')
        assignment = self._request['assignment']
        spec = assignment['task_spec_json']
        if (assignment.get('execution_scope') != 'REPOSITORY'
                or type(assignment.get('can_repair')) is not bool
                or assignment.get('task_kind') != {
                    'READ_ONLY': 'REPOSITORY_AUDIT', 'VERIFY': 'REPOSITORY_VERIFY'
                }[self._request['mode']]
                or spec.get('assignment_schema') != 'SLAVIK_DISPATCH_ASSIGNMENT_V1'
                or spec.get('exact_head_binding') is not True
                or any(spec.get(key) is not False for key in FALSE_FLAGS)
                or any(type(spec.get(key)) is not int or spec[key] != 0
                       for key in ('cost_cap_microusd', 'max_repair_attempts'))):
            raise ValueError('LIGHT_PILOT_SCOPE_FORBIDDEN')
        self._gate = gate
        self._rpc = rpc
        self._read_pr = read_pr
        self._provider = provider
        self._queue = NativeQueue(self._guarded_rpc)
        self._authority = NativeAuthority(self._queue, self._guarded_pr)

    def _check(self):
        if self._gate(deepcopy(self._request), self._target) is not True:
            raise RuntimeError('LIGHT_PILOT_NOT_ADMITTED')

    def _bound(self, request):
        if bridge.canonical(request) != self._request_json:
            raise ValueError('LIGHT_PILOT_BINDING_CHANGED')

    def _guarded_rpc(self, sql, parameters):
        self._check()
        return deepcopy(self._rpc(sql, deepcopy(parameters)))

    def _guarded_pr(self, number):
        if number != self._request['target_pr']:
            raise ValueError('LIGHT_PILOT_BINDING_CHANGED')
        self._check()
        return deepcopy(self._read_pr(number))

    def snapshot(self, dispatch_id):
        if dispatch_id != self._request['dispatch_id']:
            raise ValueError('LIGHT_PILOT_BINDING_CHANGED')
        row = self._queue.snapshot(dispatch_id)
        self._bound(row['request'])
        return row

    def begin_submission(self, request):
        self._bound(request)
        return self._queue.begin_submission(request)

    def acknowledge(self, request, task_id, prompt_sha256):
        self._bound(request)
        return self._queue.acknowledge(request, task_id, prompt_sha256)

    def finish(self, request, task_id, receipt):
        self._bound(request)
        return self._queue.finish(request, task_id, receipt)

    def lookup(self, request):
        self._bound(request)
        self._check()
        return deepcopy(self._provider.lookup(deepcopy(request), target=self._target))

    def submit(self, request):
        self._bound(request)
        self._check()
        return deepcopy(self._provider.submit(deepcopy(request), target=self._target))

    def collect(self, dispatch_id):
        if dispatch_id != self._request['dispatch_id']:
            raise ValueError('LIGHT_PILOT_BINDING_CHANGED')
        self._check()
        return deepcopy(self._provider.collect(dispatch_id, target=self._target))

    def step(self):
        """One tick; no reserve, loop, claim, retry, or alternate identity."""
        self._check()
        return advance(self._request['dispatch_id'], self, self._authority, self)
