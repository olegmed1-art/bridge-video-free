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
