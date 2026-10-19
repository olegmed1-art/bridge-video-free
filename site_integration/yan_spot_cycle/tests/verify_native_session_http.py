"""Focused Linux proof for the native-session contract on neutral data only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import urlopen

from site_integration.yan_spot_cycle.build_application_candidate import build
from site_integration.yan_spot_cycle.build_candidate import HERE, REPO
from site_integration.yan_spot_cycle.verify_persistent import ports, stop
from .native_session_protocol_fixture import FIXTURE_SOURCE


def verify(root):
    if sys.platform != 'linux':
        raise RuntimeError('Linux-only HTTP proof; no Windows socket retry')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    site, private, artifact = root/'site', root/'private', root/'artifact'
    build(site)
    (site/'cms').mkdir()
    shutil.copyfile(HERE/'tests/anonymous_session_bootstrap_fixture.inc.php', site/'cms/bootstrap.inc.php')
    install_native_shell_fixture(site)
    for path in (private, private/'php-sessions', private/'session-projections', private/'journals', artifact):
        path.mkdir(mode=0o700)
    front_port, back_port = ports()
    front_origin, back_origin = f'http://127.0.0.1:{front_port}', f'http://127.0.0.1:{back_port}'
    (private/'fixture-context.json').write_text(json.dumps({'schema':'native-session-test-context-v1',
        'frontend_origin':front_origin,'backend_origin':back_origin}))
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1',
        BRIDGE_LOCAL_SINGLE_USER='1', BRIDGE_YAN_CANDIDATE=str(site),
        BRIDGE_SINGLE_USER_FRONTEND_ORIGIN=front_origin, BRIDGE_SINGLE_USER_BACKEND_ORIGIN=back_origin,
        BRIDGE_NATIVE_SESSION_FIXTURE_PRIVATE=str(private))
    def launch():
        return subprocess.Popen([sys.executable,'-m','site_integration.yan_spot_cycle.tests.native_session_protocol_fixture',
            '--private',str(private),'--port',str(back_port)],cwd=REPO,env=env,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def ready(process):
        deadline=time.monotonic()+20
        while process.poll() is None and time.monotonic()<deadline:
            try:
                urlopen(back_origin+'/school/lessons/spot-cycle/',timeout=1).close()
            except HTTPError as error:
                if error.code==503:return
            except OSError:
                pass
            time.sleep(0.1)
        raise AssertionError('explicit fixture backend not ready')
    def journal_hashes():
        return {path.name:hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((private/'journals').glob('*.sqlite3'))}
    back=front=browser=None
    try:
        back=launch();ready(back)
        front=subprocess.Popen(['php','-d','session.save_path='+str(private/'php-sessions'),
            '-d','session.use_strict_mode=1','-S',f'127.0.0.1:{front_port}',str(HERE/'local_router.php')],
            cwd=REPO,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        # Readiness request without a native session must not become an owner.
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                urlopen(front_origin+'/index.php?route=practice%2Fdiana19%2Fcatalog',timeout=1).close()
            except HTTPError as error:
                if error.code==403:break
            except OSError:
                pass
            time.sleep(0.1)
        else:raise AssertionError('native session guard not ready')
        browser=subprocess.Popen(['node',str(HERE/'tests/verify_native_session_browser.cjs'),
            front_origin,str(artifact/'BROWSER_HTTP_EVIDENCE.json')],cwd=REPO,env=env,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        line=browser.stdout.readline().strip()
        if line!='NATIVE_HTTP_BEFORE_RESTART_READY':
            stdout,stderr=browser.communicate(timeout=5)
            raise AssertionError('browser HTTP proof failed: '+line+stdout+stderr)
        before=journal_hashes()
        assert len(before)==4  # Three-lesson and staged journal for each of two test sessions.
        stop(back);back=launch();ready(back)
        stdout,stderr=browser.communicate(input='continue\n',timeout=30)
        assert browser.returncode==0,stdout+stderr
        assert journal_hashes()==before
    finally:
        stop(browser);stop(front);stop(back)
    browser_report=json.loads((artifact/'BROWSER_HTTP_EVIDENCE.json').read_bytes())
    assert browser_report['status']=='PASS'
    for path in (private/'journals').glob('*.sqlite3'):
        shutil.copyfile(path,artifact/path.name)
    report={'schema':'native-php-session-stage-http-evidence-v1','status':'PASS',
        'fixture_source':FIXTURE_SOURCE,'head_commit':os.environ.get('GITHUB_SHA'),
        'native_php_sessions':2,'new_automated_answer_records':4,'journals_sha256':before,
        'browser_report':'BROWSER_HTTP_EVIDENCE.json','backend_restarted':True,
        'journal_bytes_equal_after_restart_readback':True,'unbound_school_route_http_status':503,
        'sessionless_application_catalog_status':403,'actual_yan_cms_verified':False,
        'reviewed_teacher_materials_uploaded':False,'production_changed':False,
        'cms_renderer':'native page/includeView adapter on neutral contract fixture',
        'cms_static_interface_report_available':True,'actual_cms_archive_executed':False,
        'route_mapping_applied':False,'student_auth_enabled':False,
        'yaml_mapping_required':False,'global_cms_settings_unchanged':True,
        'remaining_blocker':'Real CMS shell/runtime acceptance and exact public subroute availability; verified student session and durable journal service remain unbound.'}
    (artifact/'HTTP_NATIVE_SESSION_EVIDENCE.json').write_text(json.dumps(report,indent=2)+'\n')
    print('HTTP_NATIVE_SESSION_EVIDENCE='+json.dumps(report))
    print('BROWSER_HTTP_EVIDENCE='+json.dumps(browser_report))


def install_native_shell_fixture(site):
    """Only neutral shell markers. No PHP source from the private archive runs."""
    site = Path(site)
    shutil.copyfile(HERE/'tests/native_cms_interface_fixture.inc.php', site/'include/native-cms-interface-fixture.inc.php')
    blocks = site/'include/block'
    blocks.mkdir()
    contents = {
        's-header': '''<?php echo '<!doctype html><html><head><title>'.htmlspecialchars($headerData['sitetitle'], ENT_QUOTES, 'UTF-8').'</title></head><body><div class="wrapper">';''',
        'header': '<header id="native-session-contract-fixture">NATIVE_CMS_INTERFACE_FIXTURE_NOT_ARCHIVE_RUNTIME</header>',
        'footer': '<footer id="native-footer-fixture">NATIVE_FOOTER_FIXTURE</footer>',
        'js': '<script src="/legacy-jquery-fixture.js"></script>',
        's-footer': '</div></body></html>',
    }
    for name, content in contents.items():
        (blocks/(name+'.inc.php')).write_text(content, encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    verify(parser.parse_args().output)
