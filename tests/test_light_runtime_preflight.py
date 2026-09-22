"""Behavioral rejection tests for the no-claim production preflight."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from ops import oracle_light_runtime_preflight as host
from oracle_autopilot import light_runtime_probe as probe


def test_current_bundle_and_executable_transport():
    value = host.bundle('a'*40)
    host.validate_bundle(value)
    program = subprocess.check_output([sys.executable,str(Path(host.__file__)),'bundle','a'*40],text=True)
    compile(program,'bundled-preflight','exec')
    assert 'BUNDLE = ' in program


@pytest.mark.parametrize('change',[{'revision':'main'},{'revision':'a'*39},{'sha256':'0'*64}])
def test_bundle_identity_rejected(change):
    value = host.bundle('a'*40)
    value.update(change)
    with pytest.raises(host.PreflightBlocked):
        host.validate_bundle(value)


@pytest.mark.parametrize('name',['../escape','oracle_autopilot/../../env','oracle_autopilot/script.sh','/etc/environment','ops/payload.py'])
def test_bundle_path_traversal_rejected_even_with_matching_digest(name):
    value = host.bundle('a'*40)
    value['files'][name]='pass\n'
    value['sha256']=hashlib.sha256(json.dumps(value['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with pytest.raises(host.PreflightBlocked,match='BUNDLE_PATH'):
        host.validate_bundle(value)


@pytest.mark.parametrize('raw',[b'KEY=x\nKEY=y\n',b'KEY=x y\n',b'KEY\n',b'BAD-KEY=value\n'])
def test_ambiguous_environment_rejected(raw):
    with pytest.raises(host.PreflightBlocked):
        host.parse_environment(raw)


def test_quoted_environment_not_executed():
    assert host.parse_environment(b"KEY='$(touch /never)'\n") == {'KEY':'$(touch /never)'}


def ready():
    return {'task_id':probe.CANARY,'status':'READY','attempts':0,'goal_type':'CHATGPT_ROLE_DISPATCH_V1',
            'lease_until':None,'lease_epoch':0,'cost_reserved_microusd':0,'cost_actual_microusd':0}


@pytest.mark.parametrize('change',[{'task_id':'another'},{'status':'RUNNING'},{'attempts':1},
    {'lease_until':'later'},{'lease_epoch':1},{'cost_reserved_microusd':1},
    {'goal_type':'CHATGPT_ROLE_FOLLOWUP_V1'}])
def test_queue_rejects_claims_and_unexpected_work(change):
    with pytest.raises(ValueError):
        probe.validate_queue([{**ready(),**change}],{'active_workers':1,'probe_reservations':0})


def test_queue_rejects_duplicate_and_probe_reservations():
    probe.validate_queue([ready()],{'active_workers':1,'probe_reservations':0})
    for rows,capacity in [([],{'active_workers':0,'probe_reservations':0}),
        ([ready(),ready()],{'active_workers':2,'probe_reservations':0}),
        ([ready()],{'active_workers':1,'probe_reservations':1})]:
        with pytest.raises(ValueError):
            probe.validate_queue(rows,capacity)


def test_failure_is_redacted(capsys):
    with patch.object(probe.os,'geteuid',return_value=1002),patch.object(probe.worker,'load_config',side_effect=RuntimeError('postgresql://SECRET')):
        with pytest.raises(SystemExit):
            probe.main()
    result=capsys.readouterr()
    assert 'SECRET' not in result.out and not result.err
    assert json.loads(result.out) == {'status':'FAIL','stage':'configuration','error_type':'RuntimeError'}


def test_probe_has_no_queue_mutation_calls():
    import ast
    tree=ast.parse(Path(probe.__file__).read_text())
    prohibited={'claim_one','claim_next_task','_rpc_one','drain_ready','drain_cycle',
        'drain_project_work','drain_role_dispatch_outbox','reconcile_role_dispatch_callbacks','execute_task'}
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
            assert node.func.attr not in prohibited
            if node.func.attr=='execute':
                assert isinstance(node.args[0],ast.Constant) and node.args[0].value.startswith('SELECT ')


def test_workflow_untrusted_events_cannot_access_ssh():
    text=(Path(__file__).resolve().parents[1]/'.github/workflows/oracle-light-runtime-preflight.yml').read_text()
    assert "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'" in text
    assert 'group: oracle-light-backup-mutation' in text
    assert 'contents: write' not in text and 'actions: write' not in text
    assert 'SOURCE_REVISION: ${{ github.sha }}' in text
    assert 'sudo -n /usr/bin/python3 -' in text
    assert 'systemctl restart' not in text
