"""Exact front-controller lifecycle and fragment boundary; no CMS claims."""
from site_integration.yan_spot_cycle.build_application_candidate import build, APPLICATION_HOOK
from site_integration.yan_spot_cycle.build_candidate import HERE, original


def test_application_hook_preserves_original_after_bootstrap_and_session(tmp_path):
    output = tmp_path/'application'
    result = build(output)
    data = (output/'index.php').read_bytes()
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    hook = APPLICATION_HOOK.encode().replace(b'\n', newline)
    assert data.replace(hook, b'', 1) == original('index.php.txt')
    assert data.index(b"require 'cms/bootstrap.inc.php';") < data.index(b'session_start();') < data.index(b'$bridgeDianaRoute')
    assert (output/'.htaccess').read_bytes() == original('htaccess.txt')
    assert not (output/'cms').exists()
    assert result['local_loopback_fallback'] is False


def test_application_view_is_a_scoped_fragment_not_a_separate_document():
    fragment = (HERE/'site/page/diana19.fragment.inc.php').read_text(encoding='utf8')
    assert '<section class="bridge-diana19"' in fragment
    for disallowed in ('<html', '<head', '<body', '<header', '<footer', 'server/', 'expected_solution'):
        assert disallowed not in fragment
    for name in ('lesson-picker', 'answer-form', 'saved-response', 'lesson-explanation', 'source-timecode'):
        assert f'id="{name}"' in fragment
    css = (HERE/'site/css/diana19.application.css').read_text()
    assert all(line.startswith('.bridge-diana19') for line in css.splitlines()[1:] if line.strip())
