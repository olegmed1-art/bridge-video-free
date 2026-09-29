"""Authenticated fixed lane workflow; bounded live guard survives SSH loss."""
import re
from ops.native_maintenance_run_guard import API,RunBinding,check,REPOSITORY,OWNER

WORKFLOW='.github/workflows/light-native-lane-owner.yml'
WORKFLOW_SHA256='dba10a80ba5a7752b9c27fbf17995db3d2b64c96123adae6cf4504ca215b64fd'


class LaneRunBinding(RunBinding):
    workflow=WORKFLOW
    workflow_sha256=WORKFLOW_SHA256
    job_name='step'
    job_names=('contract','step')
    events=('workflow_dispatch',)
    duration_limit=1800

    def __init__(self,source,run_id,attempt,api):
        super().__init__(source,run_id,attempt,api,seconds=1800)


def local_context(env):
    source=env.get('GITHUB_SHA','')
    check(re.fullmatch('[0-9a-f]{40}',source or '')
        and env.get('GITHUB_REPOSITORY')==REPOSITORY and env.get('GITHUB_REF')=='refs/heads/main'
        and env.get('GITHUB_EVENT_NAME')=='workflow_dispatch'
        and env.get('GITHUB_WORKFLOW_REF')==REPOSITORY+'/'+WORKFLOW+'@refs/heads/main'
        and env.get('GITHUB_WORKFLOW_SHA')==source and env.get('GITHUB_JOB')=='step'
        and env.get('GITHUB_ACTOR')==env.get('GITHUB_TRIGGERING_ACTOR')==OWNER,'LANE_OWNER_LOCAL_CONTEXT')
    run,attempt=env.get('GITHUB_RUN_ID',''),env.get('GITHUB_RUN_ATTEMPT','')
    check(re.fullmatch('[1-9][0-9]{0,19}',run or '') and re.fullmatch('[1-9][0-9]{0,5}',attempt or ''),
          'LANE_OWNER_LOCAL_RUN')
    return source,int(run),int(attempt)


def authenticated(source,run,attempt,token):
    result=LaneRunBinding(source,run,attempt,API(token))
    result.assert_running()
    return result
