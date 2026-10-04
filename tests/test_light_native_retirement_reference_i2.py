"""Independent synthetic transport/output regressions; no live ports are used."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ops import light_native_lane_owner_runner as runner
from ops import light_native_retirement as r
from ops import light_native_retirement_live as live
from ops.native_maintenance_agreement import COVERAGE


ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'a' * 40
PRIVATE = 'SYNTHETIC_PRIVATE_NEVER_PUBLIC_79c012'


def reference():
    agreement = dict(version=1, owner='olegmed1-art', operation_digest='b' * 64,
        not_before='2040-01-01T00:00:00Z', expires_at='2040-01-01T00:20:00Z',
        coverage=COVERAGE, evidence=live.PUBLIC_EVIDENCE)
    return dict(version=1, action='retire-prepare-reference', source=SOURCE,
        accepted_controller_sha256='c' * 64, accepted_runtime_sha256='d' * 64,
        policy_sha256='e' * 64, index=0, record_sha256='f' * 64,
        journal_map_sha256='1' * 64, private_request_sha256='2' * 64,
        agreement=agreement, accepted_agreement_sha256=r.sha(r.encoded(agreement)))


def event(value=None):
    value = reference() if value is None else value
    raw = r.encoded(value)
    return dict(inputs=dict(expected_main_sha=SOURCE,
        accepted_controller_sha256=value['accepted_controller_sha256'],
        accepted_runtime_sha256=value['accepted_runtime_sha256'],
        accepted_payload_sha256=r.sha(raw), retirement_reference_json=raw.decode()))


def test_dispatch_reads_event_file_and_ignores_obsolete_payload_environment(tmp_path, monkeypatch, capsys):
    value = event()
    path = tmp_path / 'event.json'
    path.write_bytes(r.encoded(value))
    monkeypatch.setenv('LANE_PAYLOAD_BASE64', PRIVATE)
    monkeypatch.setenv('LANE_RETIREMENT_REFERENCE', PRIVATE)
    payload, pins = runner.dispatch_inputs(path, SOURCE)
    assert payload == r.encoded(reference())
    assert pins == ['c' * 64, 'd' * 64, r.sha(payload)]
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('fault', ['malformed', 'private_extra', 'private_evidence',
    'old_input', 'wrong_source', 'duplicate', 'oversize'])
def test_actual_runner_rejects_bad_event_before_network_without_private_output(tmp_path, fault):
    value = event()
    if fault == 'private_extra':
        value['inputs']['private_extra'] = PRIVATE
    elif fault == 'private_evidence':
        ref = reference()
        ref['agreement']['evidence'] = PRIVATE
        ref['accepted_agreement_sha256'] = r.sha(r.encoded(ref['agreement']))
        value = event(ref)
    elif fault == 'old_input':
        value['inputs']['payload_base64'] = base64.b64encode(PRIVATE.encode()).decode()
        del value['inputs']['retirement_reference_json']
    elif fault == 'wrong_source':
        value['inputs']['expected_main_sha'] = 'b' * 40
    raw = r.encoded(value)
    if fault == 'malformed':
        raw = ('{"inputs":' + PRIVATE).encode()
    elif fault == 'duplicate':
        raw = b'{"inputs":' + r.encoded(value['inputs']) + b',"inputs":' + json.dumps(PRIVATE).encode() + b'}'
    elif fault == 'oversize':
        raw = json.dumps(dict(inputs=value['inputs'], padding=PRIVATE * 40000)).encode()
    path = tmp_path / 'event.json'
    path.write_bytes(raw)
    env = dict(PATH=os.defpath, GITHUB_SHA=SOURCE,
        GITHUB_REPOSITORY='olegmed1-art/bridge-video-free', GITHUB_REF='refs/heads/main',
        GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_WORKFLOW_SHA=SOURCE,
        GITHUB_WORKFLOW_REF='olegmed1-art/bridge-video-free/.github/workflows/light-native-lane-owner.yml@refs/heads/main',
        GITHUB_JOB='step', GITHUB_ACTOR='olegmed1-art', GITHUB_TRIGGERING_ACTOR='olegmed1-art',
        GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1', GITHUB_EVENT_PATH=str(path))
    # Real __main__ exception boundary and parser; forbid any network/SSH port.
    script = """import os,runpy,socket,subprocess,sys
sys.path.insert(0,sys.argv[1])
from ops import light_native_lane_owner_runner as loaded
def forbidden(*a,**kw):os._exit(97)
socket.socket=forbidden
subprocess.run=forbidden
sys.argv=['owner','unused-key','unused-known-hosts','unused-wheels']
runpy.run_path(loaded.__file__,run_name='__main__')
"""
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(ROOT)],
        env=env, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {'audit': 'LIGHT_LANE_RUNNER_REFUSED'}
    assert result.stderr == b'' and PRIVATE.encode() not in result.stdout


def test_workflow_never_interpolates_dispatch_values_into_steps():
    raw = (ROOT / '.github/workflows/light-native-lane-owner.yml').read_text()
    # Input-bearing job/concurrency admission guards are permitted; step run/env
    # sections must contain no input expressions, even before Python validation.
    in_steps = False
    for line in raw.splitlines():
        if line.startswith('    steps:'):
            in_steps = True
        elif line.startswith('  ') and not line.startswith('   ') and line.strip():
            in_steps = False
        if in_steps:
            assert 'inputs.' not in line and 'inputs[' not in line
    assert 'payload_base64:' not in raw and 'LANE_RETIREMENT_REFERENCE:' not in raw
    assert 'LANE_PAYLOAD_BASE64:' not in raw


@pytest.mark.parametrize('fault', ['extra_private', 'wrong_digest', 'exception_private', 'valid'])
def test_real_bootstrap_filters_phase_output_in_fresh_root_interpreter(fault):
    assert os.geteuid() == 0, 'these integration tests require the isolated root fixture container'
    ref = reference()
    helpers = {name: '' for name in dict.fromkeys((*runner.release.HELPERS, *runner.controller.EXTRA))}
    # Actual reference/result schema and agreement parsing code; only external
    # authenticated-run and phase ports are synthetic within this private fixture.
    for name in ('ops/light_native_retirement.py', 'ops/light_native_retirement_live.py',
                 'ops/native_maintenance_agreement.py', 'ops/native_maintenance_workflow_pause.py'):
        helpers[name] = (ROOT / name).read_text()
    helpers['ops/light_native_lane_run_guard.py'] = (
        'class Guard:\n def assert_running(self):pass\n'
        'def authenticated(*args):return Guard()\n')
    output = dict(audit='LIGHT_LANE_RETIREMENT', state='PROPOSAL_RETAINED_UNACCEPTED',
        record_sha256=ref['record_sha256'], incident_closed=False,
        execution_acknowledged=False, new_task_authorized=False)
    if fault == 'extra_private':
        output['private'] = PRIVATE
    elif fault == 'wrong_digest':
        output['record_sha256'] = '0' * 64
    if fault == 'exception_private':
        helpers['ops/light_native_lane_controller.py'] = 'def phase(*args):raise RuntimeError(' + repr(PRIVATE) + ')\n'
    else:
        helpers['ops/light_native_lane_controller.py'] = 'def phase(*args):return ' + repr(output) + '\n'
    package = r.encoded(dict(version=1, kind='LIGHT_LANE_CONTROLLER', source=SOURCE, helpers=helpers))
    runtime, driver = b'synthetic-runtime', b'synthetic-driver'
    ref['accepted_controller_sha256'] = r.sha(package)
    ref['accepted_runtime_sha256'] = r.sha(runtime)
    payload = r.encoded(ref)
    code = runner.bootstrap(SOURCE, r.sha(package), r.sha(runtime), r.sha(payload), r.sha(driver), 123, 1)
    wire = {k: base64.b64encode(v).decode() for k, v in
        dict(controller=package, runtime=runtime, payload=payload, driver=driver).items()}
    wire.update(credential=PRIVATE, token=PRIVATE)
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code],
        input=r.encoded(wire), capture_output=True, timeout=10)
    assert result.stderr == b'' and PRIVATE.encode() not in result.stdout
    if fault == 'valid':
        assert result.returncode == 0 and json.loads(result.stdout) == output
    else:
        assert result.returncode == 2
        assert json.loads(result.stdout) == dict(audit='LIGHT_LANE_OWNER_REFUSED',
            reason='UNCLASSIFIED' if fault == 'exception_private' else 'LANE_RETIREMENT_REFUSED')
