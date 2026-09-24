import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from ops import oracle_light_recovery_attest as target


def test_bundle_is_exact_previously_installed_release():
    packet = target.packet('a' * 40)
    assert packet['admin_revision'] == 'a' * 40
    assert packet['bundle']['revision'] == target.INSTALLED
    assert packet['bundle']['sha256'] == target.BUNDLE_SHA
    assert 'AUTOPILOT_TOKEN_BROKER_URL=' in packet['bundle']['files']['ops/autopilot/broker-hold.env']


@pytest.mark.parametrize('field,value', [('revision','b'*40), ('sha256','0'*64)])
def test_wrong_installed_identity_fails_before_host_reads(monkeypatch,field,value):
    packet=target.packet('a'*40)
    packet['bundle'][field]=value
    monkeypatch.setattr(target.os,'geteuid',lambda:0)
    monkeypatch.setattr(target.os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
    with pytest.raises(RuntimeError,match='INSTALLED_BUNDLE_IDENTITY'):
        target.attest(packet)


def test_wrong_host_fails_before_file_or_network_access(monkeypatch):
    packet=target.packet('a'*40)
    monkeypatch.setattr(target.os,'geteuid',lambda:1000)
    with pytest.raises(RuntimeError,match='HOST_IDENTITY'):
        target.attest(packet)
