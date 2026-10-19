"""Run exact privately supplied class bytes after static review, without bootstrap."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from site_integration.yan_spot_cycle.build_application_candidate import build
from site_integration.yan_spot_cycle.build_candidate import HERE
from .verify_native_session_http import install_native_shell_fixture


def verify(source, output, php):
    executable = shutil.which(php)
    if not executable:
        raise RuntimeError('PHP CLI unavailable; no source execution attempted')
    pins = {'cms.class.php':'708bd88fe1588f75c0c0b343bbed0ddc809ac467b9d7ffd066b78acab656a2c0',
            'routing.class.php':'e02acef2e8ec0c3c427f999841210f93d23560bdf9c058ef23bb754bd869e835'}
    source = Path(source).resolve()
    for name, pin in pins.items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest() != pin:
            raise ValueError('unreviewed CMS source rejected')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    site = output/'site'
    build(site)
    install_native_shell_fixture(site)
    sessions = output/'private-php-sessions'
    sessions.mkdir()
    result = subprocess.run([executable,'-n','-d','session.save_path='+str(sessions),
        str(HERE/'tests/reviewed_cms_driver.php'),str(source),str(site)],
        capture_output=True,text=True,timeout=15,check=True)
    if result.stderr:
        raise AssertionError('unexpected component diagnostics: '+result.stderr)
    report = json.loads(result.stdout)
    assert report['status'] == 'PASS' and report['cases'] == 8
    report['source_sha256'] = pins
    (output/'REVIEWED_CMS_COMPONENT_EVIDENCE.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--php',default='php')
    args = parser.parse_args()
    verify(args.source_dir,args.output,args.php)
