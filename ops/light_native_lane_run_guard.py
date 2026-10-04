"""Authenticated fixed lane workflow; bounded live guard survives SSH loss."""
import re
from ops.native_maintenance_run_guard import API,RunBinding,check,REPOSITORY,OWNER

WORKFLOW='.github/workflows/light-native-lane-owner.yml'
WORKFLOW_SHA256='1c8d787c63a8e1cde3f72734b553cc4115625b443566ce77a60b5bf7806e5c3f'
OBSERVATION_WORKFLOW='.github/workflows/light-native-retirement-observe.yml'
OBSERVATION_WORKFLOW_SHA256='46d8d01c36df6b3711bf73fa7d6f1a462fd6c85c130ead28957eafb646e876e0'


class LaneRunBinding(RunBinding):
    workflow=WORKFLOW
    workflow_sha256=WORKFLOW_SHA256
    job_name='step'
    job_names=('contract','step')
    events=('workflow_dispatch',)
    duration_limit=1800

    def __init__(self,source,run_id,attempt,api):
        super().__init__(source,run_id,attempt,api,seconds=1800)


class LaneObservationBinding(LaneRunBinding):
    workflow=OBSERVATION_WORKFLOW
    workflow_sha256=OBSERVATION_WORKFLOW_SHA256


def local_context(env,*,read_only=False):
    check(type(read_only) is bool,'LANE_OWNER_LOCAL_CONTEXT')
    workflow=OBSERVATION_WORKFLOW if read_only else WORKFLOW
    source=env.get('GITHUB_SHA','')
    check(re.fullmatch('[0-9a-f]{40}',source or '')
        and env.get('GITHUB_REPOSITORY')==REPOSITORY and env.get('GITHUB_REF')=='refs/heads/main'
        and env.get('GITHUB_EVENT_NAME')=='workflow_dispatch'
        and env.get('GITHUB_WORKFLOW_REF')==REPOSITORY+'/'+workflow+'@refs/heads/main'
        and env.get('GITHUB_WORKFLOW_SHA')==source and env.get('GITHUB_JOB')=='step'
        and env.get('GITHUB_ACTOR')==env.get('GITHUB_TRIGGERING_ACTOR')==OWNER,'LANE_OWNER_LOCAL_CONTEXT')
    run,attempt=env.get('GITHUB_RUN_ID',''),env.get('GITHUB_RUN_ATTEMPT','')
    check(re.fullmatch('[1-9][0-9]{0,19}',run or '') and re.fullmatch('[1-9][0-9]{0,5}',attempt or ''),
          'LANE_OWNER_LOCAL_RUN')
    return source,int(run),int(attempt)


def authenticated(source,run,attempt,token,*,read_only=False):
    check(type(read_only) is bool,'LANE_OWNER_LOCAL_CONTEXT')
    result=(LaneObservationBinding if read_only else LaneRunBinding)(source,run,attempt,API(token))
    result.assert_running()
    return result
