"""Authenticated GitHub run boundary for the fixed single-pilot owner step.

This profile grants no rehearsal or grant-stage authority.  It observes the
actual manual workflow, both jobs, owner actor, immutable workflow bytes and
fresh main before each externally supplied effect boundary.
"""
import re
import time

from ops.native_maintenance_run_guard import API, RunBinding, check, REPOSITORY, OWNER


WORKFLOW = '.github/workflows/light-native-pilot-owner.yml'
WORKFLOW_SHA256 = '2397e40dbc84f421ca51670162062a41507ed6a671118d3e0a72a0c1f33f527b'
OWNER_STEP_SECONDS = 100


class OwnerRunBinding(RunBinding):
    workflow = WORKFLOW
    workflow_sha256 = WORKFLOW_SHA256
    job_name = 'step'
    job_names = ('contract', 'step')
    events = ('workflow_dispatch',)

    def __init__(self, source, run_id, attempt, api):
        # The existing generic profile is limited to 60 seconds.  This fixed
        # owner step runs inside a 100-second PID1 supervisor, so it has its
        # own fixed bound, with no caller-controlled extension or renewal.
        super().__init__(source, run_id, attempt, api, seconds=60)
        self.deadline = time.monotonic() + OWNER_STEP_SECONDS

    def assert_running(self):
        check(type(self) is OwnerRunBinding, 'PILOT_OWNER_PROFILE_CHANGED')
        return super().assert_running()


def local_context(env):
    """Check the runner's declared context before creating an SSH payload."""
    source = env.get('GITHUB_SHA', '')
    check(type(source) is str and re.fullmatch('[0-9a-f]{40}', source)
          and env.get('GITHUB_REPOSITORY') == REPOSITORY
          and env.get('GITHUB_REF') == 'refs/heads/main'
          and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
          and env.get('GITHUB_WORKFLOW_REF') == REPOSITORY+'/'+WORKFLOW+'@refs/heads/main'
          and env.get('GITHUB_WORKFLOW_SHA') == source
          and env.get('GITHUB_JOB') == 'step'
          and env.get('GITHUB_ACTOR') == OWNER
          and env.get('GITHUB_TRIGGERING_ACTOR') == OWNER,
          'PILOT_OWNER_LOCAL_CONTEXT')
    run_id,attempt = env.get('GITHUB_RUN_ID',''),env.get('GITHUB_RUN_ATTEMPT','')
    check(re.fullmatch('[1-9][0-9]{0,19}',run_id or '')
          and re.fullmatch('[1-9][0-9]{0,5}',attempt or ''),
          'PILOT_OWNER_LOCAL_RUN')
    return source,int(run_id),int(attempt)


def authenticated(source, run_id, attempt, token):
    guard=OwnerRunBinding(source,run_id,attempt,API(token))
    guard.assert_running()
    return guard
