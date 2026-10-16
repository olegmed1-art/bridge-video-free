"""No provider/DB imports or calls before the supervisor releases admission."""
import subprocess
import sys
from unittest.mock import Mock
import pytest
from oracle_autopilot import light_native_launch_gate as gate


def test_gate_imports_under_isolation_without_database_or_provider_modules():
    code='import sys;sys.path.insert(0,".");from oracle_autopilot import light_native_launch_gate;assert "psycopg" not in sys.modules;assert "oracle_autopilot.light_native_loader" not in sys.modules'
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',code],capture_output=True)
    assert result.returncode==0


def test_hold_blocks_until_root_releases(monkeypatch):
    values=iter([b'HOLD\n',b'HOLD\n',b'PILOT\n'])
    monkeypatch.setattr(gate,'root_bytes',lambda *a:next(values))
    sleep=Mock();monkeypatch.setattr(gate.time,'sleep',sleep)
    gate.wait_for_admission()
    assert sleep.call_count==2


@pytest.mark.parametrize('value',[b'',b'PILOT',b'UNKNOWN\n'])
def test_unknown_admission_never_enters_loader(monkeypatch,value):
    monkeypatch.setattr(gate,'root_bytes',lambda *a:value)
    with pytest.raises(RuntimeError,match='PILOT_LAUNCH_GATE_STATE'):gate.main()


def test_gate_expires_without_loading_executor(monkeypatch):
    clock=iter([0,1801])
    monkeypatch.setattr(gate.time,'monotonic',lambda:next(clock))
    with pytest.raises(RuntimeError,match='PILOT_LAUNCH_GATE_EXPIRED'):gate.main()
