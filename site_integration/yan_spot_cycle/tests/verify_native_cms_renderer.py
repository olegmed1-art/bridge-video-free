"""Focused PHP renderer acceptance against the sanitized CMS interface contract."""
import argparse
import json
from pathlib import Path
import subprocess

from site_integration.yan_spot_cycle.build_application_candidate import build
from site_integration.yan_spot_cycle.build_candidate import HERE
from .verify_native_session_http import install_native_shell_fixture


def verify(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    site = root/'site'
    build(site)
    install_native_shell_fixture(site)
    sessions = root/'private-php-sessions'
    sessions.mkdir()
    result = subprocess.run(['php', '-d', 'session.save_path='+str(sessions),
        str(HERE/'tests/native_cms_renderer_driver.php'), str(site)],
        capture_output=True, text=True, timeout=15, check=True)
    if result.stderr:
        raise AssertionError('unexpected PHP renderer diagnostics: '+result.stderr)
    report = json.loads(result.stdout)
    assert report['status'] == 'PASS'
    (root/'NATIVE_CMS_RENDERER_EVIDENCE.json').write_text(json.dumps(report, indent=2)+'\n')
    print('NATIVE_CMS_RENDERER_EVIDENCE='+json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    verify(parser.parse_args().output)
