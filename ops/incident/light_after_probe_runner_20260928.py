"""Manual branch-only transport for the incident's read-only probe.

No production stage, main mutation, remote installation, or automatic retry.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_driver as driver
from ops.native_maintenance_store_runner import HOST, loader
from ops.native_maintenance_run_guard import PersistentAPI, OWNER_ID, REPOSITORY
from ops.native_maintenance_workflow_api import Transport, source_matches

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'ops/incident/light_after_probe_20260928.py'
spec = importlib.util.spec_from_file_location('incident_helper', HELPER)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
BRANCH = 'fix/light-after-readonly-probe-20260928'
WORKFLOW = '.github/workflows/native-maintenance-owner-host.yml'
MAX_WIRE = 16 * 1024 * 1024
PHASE = 'context'


def context(env):
    accepted = env.get('ACCEPTED_PROBE_SHA', '')
    bundle.check(bundle.identifier(accepted, 40)
        and env.get('EXPECTED_MAIN') == probe.SOURCE
        and env.get('GITHUB_SHA') == accepted
        and env.get('GITHUB_WORKFLOW_SHA') == accepted
        and env.get('GITHUB_REPOSITORY') == REPOSITORY
        and env.get('GITHUB_REF') == 'refs/heads/' + BRANCH
        and env.get('GITHUB_WORKFLOW_REF') == REPOSITORY + '/' + WORKFLOW + '@refs/heads/' + BRANCH
        and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
        and env.get('GITHUB_ACTOR') == env.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art'
        and env.get('GITHUB_JOB') == 'probe'
        and re.fullmatch('[1-9][0-9]{0,19}', env.get('GITHUB_RUN_ID', ''))
        and re.fullmatch('[1-9][0-9]{0,5}', env.get('GITHUB_RUN_ATTEMPT', '')),
        'PROBE_DISPATCH_CONTEXT')
    return accepted, int(env['GITHUB_RUN_ID']), int(env['GITHUB_RUN_ATTEMPT'])


def live_context(api, accepted, run_id, attempt):
    run = api.get('/actions/runs/' + str(run_id))
    bundle.check(run['id'] == run_id and run['run_attempt'] == attempt
        and run['head_sha'] == accepted and run['head_branch'] == BRANCH
        and run['event'] == 'workflow_dispatch' and run['status'] == 'in_progress'
        and run['conclusion'] is None and run['path'] == WORKFLOW
        and run['repository']['full_name'] == run['head_repository']['full_name'] == REPOSITORY
        and all(run[k]['login'] == 'olegmed1-art' and type(run[k]['id']) is int
                and run[k]['id'] == OWNER_ID for k in ('actor', 'triggering_actor')),
        'PROBE_LIVE_RUN')
    jobs = api.get(f'/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100')
    rows = jobs['jobs']
    bundle.check(type(jobs['total_count']) is int and jobs['total_count'] == 2
        and type(rows) is list and len(rows) == 2
        and sorted(j['name'] for j in rows) == ['contract', 'probe']
        and len({j['id'] for j in rows}) == 2
        and all(type(j['id']) is int and j['id'] > 0 and j['run_id'] == run_id
            and j['run_attempt'] == attempt and j['head_sha'] == accepted for j in rows)
        and all((j['status'], j['conclusion']) ==
            (('completed', 'success') if j['name'] == 'contract' else ('in_progress', None))
            for j in rows), 'PROBE_LIVE_JOBS')
    source_matches(Transport(api._token, read_api=api), probe.SOURCE)


def bootstrap(repo, helper, run, binding):
    lifetime = bundle.git(repo, 'show', probe.SOURCE + ':ops/native_maintenance_lifetime.py')
    # Code/nonce are public. Source, driver and ALL credentials travel in stdin.
    inner = ('import base64,json,sys,types,os,signal\n'
        + loader('probe', helper)
        + "def expired(*_): raise RuntimeError('PROBE_TIMEOUT')\n"
        + "result=dict(audit='LIGHT_AFTER_READ_ONLY_PROBE',source=probe.SOURCE,production_mutations=False,resume_authorized=False,historical_cause_proven=False)\n"
        + "status=2\ntry:\n"
        + " probe.require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,'PROBE_ISOLATION')\n"
        + " probe.require(os.getuid()==0 and os.uname().nodename=='autopilot-lite-vnic','PROBE_HOST')\n"
        + " signal.signal(signal.SIGALRM,expired);signal.alarm(120)\n"
        + ' raw=sys.stdin.buffer.read(' + str(MAX_WIRE + 1) + ')\n'
        + ' probe.require(len(raw)<=' + str(MAX_WIRE) + ",'PROBE_INPUT')\n"
        + " value=json.loads(raw)\n"
        + " probe.require(type(value) is dict and set(value)=={'source','driver','envelope','binding'},'PROBE_INPUT')\n"
        + ' probe.require(value["binding"]==' + repr(binding) + ",'PROBE_INPUT')\n"
        + " probe.execute(base64.b64decode(value['source'],validate=True),base64.b64decode(value['driver'],validate=True),value['envelope']," + repr(tuple(int(n) for n in run.split('-'))) + ")\n"
        + " result.update(status='PASS',phase=probe.PHASE);status=0\n"
        + "except BaseException as exc:\n result.update(status='REFUSED',phase=probe.PHASE,code=probe.code(exc))\n"
        + 'result["binding"]=' + repr(binding) + '\n'
        + "signal.alarm(0)\nprint(json.dumps(result,sort_keys=True),flush=True)\nsys.exit(status)\n")
    outer = ('import base64,types\n' + loader('lifetime', lifetime)
        + 'try:\n result=lifetime.managed_stage(' + repr(inner) + ','
        + repr(base64.b64encode(lifetime).decode()) + ',' + repr(probe.SOURCE) + ',' + repr(run) + ')\n'
        + 'except BaseException:\n result=2\nraise SystemExit(result)\n')
    bundle.check(len(outer.encode()) <= 98304, 'PROBE_BOOTSTRAP_SIZE')
    return outer


def validate_report(raw, returncode, binding):
    bundle.check(len(raw) <= 2048, 'PROBE_REPORT_SIZE')
    value = json.loads(raw, object_pairs_hook=bundle.unique)
    expected = dict(audit='LIGHT_AFTER_READ_ONLY_PROBE', source=probe.SOURCE,
        production_mutations=False, resume_authorized=False, historical_cause_proven=False,
        binding=binding)
    keys = set(expected) | {'status', 'phase'}
    bundle.check(type(value) is dict and all(type(value.get(k)) is type(v) and value[k] == v
                  for k, v in expected.items()), 'PROBE_REPORT_BINDING')
    phases = {'input','source_bundle','driver','request','source_and_failed_run','hold',
              'workflow_drain','prior_host_drain','owner_backend_drain','after_snapshot','continuity','supervisor','supervisor_final','complete'}
    if value.get('status') == 'PASS':
        bundle.check(set(value) == keys and value['phase'] == 'complete' and returncode == 0,
                     'PROBE_REPORT_PASS')
    else:
        bundle.check(set(value) == keys | {'code'} and value.get('status') == 'REFUSED'
            and value.get('phase') in phases and value.get('code') in
                probe.CODES | {'UNCLASSIFIED','TIMEOUT','DB_AUTHENTICATION'} and returncode == 2,
            'PROBE_REPORT_REFUSAL')
    return value


def main():
    global PHASE
    bundle.check(len(sys.argv) == 4, 'PROBE_ARGS')
    accepted, run_id, attempt = context(os.environ)
    key, known_hosts, wheel_directory = sys.argv[1:]
    token = os.environ.pop('GH_TOKEN', '')
    credential = os.environ.pop('NATIVE_OWNER_DATABASE_URL', '')
    helper = bundle.git(ROOT, 'show', accepted + ':ops/incident/light_after_probe_20260928.py')
    bundle.check(HELPER.read_bytes() == helper, 'PROBE_CHECKOUT_CHANGED')
    source_payload = bundle.build(ROOT, probe.SOURCE)
    bundle.check(bundle.digest(source_payload) == probe.BUNDLE, 'PROBE_SOURCE')
    wheels = driver.build(wheel_directory)
    binding = bundle.digest(bundle.canonical(dict(commit=accepted,run=run_id,attempt=attempt,
                                                  helper=bundle.digest(helper))))
    wire = bundle.canonical(dict(source=base64.b64encode(source_payload).decode(),
        driver=base64.b64encode(wheels).decode(),envelope=dict(token=token,credential=credential),binding=binding))
    bundle.check(len(wire) <= MAX_WIRE, 'PROBE_WIRE_SIZE')
    with PersistentAPI(token) as api:
        PHASE = 'source_before'
        live_context(api, accepted, run_id, attempt)
        code = bootstrap(ROOT, helper, f'{run_id}-{attempt}', binding)
        command = ['ssh','-F','/dev/null','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','ForwardAgent=no','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known_hosts,
            '-o','ConnectTimeout=15','-o','ConnectionAttempts=1','-o','ServerAliveInterval=5',
            '-o','ServerAliveCountMax=2',HOST,
            shlex.join(['sudo','-n','/usr/bin/python3','-I','-B','-S','-c',code])]
        PHASE = 'host_exchange'
        result = subprocess.run(command,input=wire,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                timeout=170,env={'PATH':'/usr/bin:/bin'})
        value = validate_report(result.stdout, result.returncode, binding)
        PHASE = 'source_after'
        live_context(api, accepted, run_id, attempt)
        print(json.dumps(value,sort_keys=True))
        return 0 if value['status'] == 'PASS' else 2


if __name__ == '__main__':
    try:
        status = main()
    except BaseException:
        print(json.dumps(dict(audit='LIGHT_AFTER_TRANSPORT_REFUSED',phase=PHASE,
                              production_mutations=False,resume_authorized=False)))
        status = 2
    sys.exit(status)
