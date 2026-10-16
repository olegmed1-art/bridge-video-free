import os
from pathlib import Path

import pytest

from oracle_autopilot import light_native_loader as loader


@pytest.mark.skipif(os.getuid() != 0, reason='root-controlled file contract')
def test_root_control_rejects_symlinks_writable_files_and_parents(tmp_path):
    # /tmp is writable, so use a private directory under the root-owned home.
    import tempfile
    with tempfile.TemporaryDirectory(dir='/root') as directory:
        path = Path(directory) / 'admission'
        path.write_bytes(b'PILOT\n')
        path.chmod(0o640)
        assert loader.root_bytes(path, 16) == b'PILOT\n'
        link = path.with_name('link')
        link.symlink_to(path)
        with pytest.raises(OSError): loader.root_bytes(link, 16)
        path.chmod(0o666)
        with pytest.raises(RuntimeError, match='CONTROL_FILE'): loader.root_bytes(path, 16)
        path.chmod(0o640)
        path.parent.chmod(0o777)
        with pytest.raises(RuntimeError, match='CONTROL_PARENT'): loader.root_bytes(path, 16)
        path.parent.chmod(0o700)
        path.chmod(0o444)
        assert loader.root_bytes(path, 16, private=False) == b'PILOT\n'
        with pytest.raises(RuntimeError, match='CONTROL_FILE'): loader.root_bytes(path, 16)


def test_runtime_dsn_reconstructed_without_injected_options():
    host = loader.EXPECTED_TARGET['neon']['host']
    value = loader.runtime_parameters('postgresql://autopilot_light_worker_login:test@'+host+
        '/neondb?sslmode=require&channel_binding=require&hostaddr=127.0.0.1&options=bad')
    assert value['host'] == host and value['options'] == ''
    assert value['sslmode'] == 'verify-full' and value['gssencmode'] == 'disable'
    assert 'hostaddr' not in value
    with pytest.raises(ValueError):
        loader.runtime_parameters('postgresql://neondb_owner:test@'+host+
            '/neondb?sslmode=require&channel_binding=require')


def test_admission_reads_live_control_each_time(monkeypatch):
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE', 'PILOT')
    state = [b'PILOT\n']
    monkeypatch.setattr(loader, 'root_bytes', lambda *args: state[0])
    assert loader.admitted()
    state[0] = b'HOLD\n'
    assert not loader.admitted()
    state[0] = b'PILOT\n'
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE', 'HOLD')
    assert not loader.admitted()


def test_controlled_exec_closes_both_contexts_and_uses_fixed_gate(monkeypatch):
    import sys
    from types import SimpleNamespace
    from oracle_autopilot.light_native_restart import RestartImage
    from ops.light_native_service_plan import pilot_argv
    events=[]
    class Context:
        def __init__(self,name):self.name=name
        def __enter__(self):events.append('open-'+self.name);return self
        def __exit__(self,*args):events.append('close-'+self.name)
    class Session:
        def __init__(self,*args,**kwargs):pass
        def reserve(self):events.append('reserve')
        def step(self):raise RestartImage()
    permit=SimpleNamespace(target='target',check=lambda:events.append('permit-check'))
    monkeypatch.setattr(loader,'release_source',lambda:'c'*40)
    monkeypatch.setattr(loader,'admitted',lambda:True)
    monkeypatch.setattr(loader,'root_bytes',lambda *args:b'a'*64)
    monkeypatch.setattr(loader,'Permit',lambda *args:permit)
    monkeypatch.setattr(loader,'LightProvider',lambda *args:object())
    monkeypatch.setattr(loader,'Claim',lambda *args:Context('claim'))
    monkeypatch.setattr(loader,'runtime_parameters',lambda *args:{})
    monkeypatch.setattr(loader,'runtime_identity',lambda *args:None)
    monkeypatch.setattr(loader,'RestartingProvider',lambda *args:SimpleNamespace(attach=lambda s:None))
    monkeypatch.setattr(loader,'Session',Session)
    monkeypatch.setitem(sys.modules,'psycopg',SimpleNamespace(connect=lambda **kwargs:Context('db')))
    def execv(binary,argv):
        assert events[-3:]==['close-db','close-claim','permit-check']
        assert [binary]+argv==[pilot_argv('c'*40)[0]]+pilot_argv('c'*40)
        raise OSError('simulated exec failure')
    monkeypatch.setattr(loader.os,'execv',execv)
    with pytest.raises(OSError,match='simulated exec failure'):loader.execute()
    assert events.count('reserve')==1
