"""The administrative reload must refuse drift and never restart the service."""
from pathlib import Path
import importlib.util
from types import SimpleNamespace
from unittest.mock import patch

import pytest


PATH = Path(__file__).resolve().parents[1] / 'ops/oracle_light_daemon_reload.py'
spec = importlib.util.spec_from_file_location('light_reload', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
INVOCATION = 'bb05e493284743ad947b2843c70e52da'


def state(reload='yes', pid='29894'):
    return dict(ActiveState='active', SubState='running', MainPID=pid,
                InvocationID=INVOCATION, WorkingDirectory=module.RELEASE,
                Environment='PYTHONUNBUFFERED=1 AUTOPILOT_ADMISSION_MODE=HOLD',
                User='school-autopilot', Group='school-autopilot',
                FragmentPath=str(module.BASE), DropInPaths=str(module.DROP),
                ExecStart='path=/opt/bridge-school/school-autopilot/.venv/bin/python ; argv[]=/opt/bridge-school/school-autopilot/.venv/bin/python -m oracle_autopilot.worker_v17 ;',
                NeedDaemonReload=reload)


def test_reload_keeps_identity_and_hold(capsys):
    before, after = state(), state('no')
    with patch.object(module.os, 'geteuid', return_value=0), \
         patch.object(module.os, 'uname', return_value=SimpleNamespace(nodename='autopilot-lite-vnic')), \
         patch.object(module.sys, 'argv', ['script', INVOCATION]), \
         patch.object(module, 'pinned_file', return_value=b'pinned') as files, \
         patch.object(module, 'show', side_effect=[before, after]), \
         patch.object(module.subprocess, 'run') as run:
        module.main()
    run.assert_called_once_with(['systemctl', 'daemon-reload'], check=True, timeout=30)
    assert files.call_count == 4
    assert '"restart": false' in capsys.readouterr().out


@pytest.mark.parametrize('changed,value', [('MainPID', '12345'),
                                           ('InvocationID', 'a' * 32),
                                           ('WorkingDirectory', '/tmp'),
                                           ('Environment', 'PYTHONUNBUFFERED=1')])
def test_post_reload_drift_is_rejected(changed, value):
    before, after = state(), state('no')
    after[changed] = value
    with patch.object(module.os, 'geteuid', return_value=0), \
         patch.object(module.os, 'uname', return_value=SimpleNamespace(nodename='autopilot-lite-vnic')), \
         patch.object(module.sys, 'argv', ['script', INVOCATION]), \
         patch.object(module, 'pinned_file', return_value=b'pinned'), \
         patch.object(module, 'show', side_effect=[before, after]), \
         patch.object(module.subprocess, 'run') as run:
        with pytest.raises(RuntimeError):
            module.main()
    run.assert_called_once()


def test_unexpected_unit_file_never_runs_reload():
    with patch.object(module.os, 'geteuid', return_value=0), \
         patch.object(module.os, 'uname', return_value=SimpleNamespace(nodename='autopilot-lite-vnic')), \
         patch.object(module.sys, 'argv', ['script', INVOCATION]), \
         patch.object(module, 'pinned_file', side_effect=RuntimeError('UNIT_FILE_DRIFT')), \
         patch.object(module.subprocess, 'run') as run:
        with pytest.raises(RuntimeError, match='UNIT_FILE_DRIFT'):
            module.main()
    run.assert_not_called()
