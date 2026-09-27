"""The launcher accepts data, never caller-selected host code or shell."""
import hashlib
import pytest
from ops import light_native_pilot_control as target


def generate(action='baseline',payload=None):
    package=b'{}'
    payload=target.release.encoded(payload or dict(agreement={},accepted_agreement_sha256='b'*64,scope={}))
    return target.program(package,'a'*40,hashlib.sha256(package).hexdigest(),action,
                          payload,hashlib.sha256(payload).hexdigest())


def test_fixed_bootstrap_compiles_and_has_no_shell_interpolation():
    code=generate()
    compile(code,'bootstrap','exec')
    assert 'controller.prepare_baseline(' in code
    assert 'os.system' not in code and 'shell=True' not in code
    assert "except BaseException:" in code


@pytest.mark.parametrize('action',['run arbitrary','first_install','stage','delete',''])
def test_unreviewed_actions_refused(action):
    with pytest.raises(RuntimeError,match='PILOT_CONTROL_ACTION'):generate(action)


def test_external_digest_is_required_before_any_bootstrap():
    with pytest.raises(RuntimeError,match='PILOT_CONTROL_PAYLOAD'):
        target.program(b'{}','a'*40,hashlib.sha256(b'{}').hexdigest(),'baseline',b'{}','b'*64)


def test_cleanup_payload_cannot_select_commands_or_paths():
    for action in ('restore','observe'):
        with pytest.raises(RuntimeError,match='PILOT_CONTROL_REQUEST'):
            generate(action,{'request_sha256':'../other'})
        compile(generate(action,{'request_sha256':'c'*64}),'cleanup','exec')
