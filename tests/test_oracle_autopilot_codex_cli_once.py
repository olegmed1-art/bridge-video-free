"""Native one-shot transport rejects untrusted PR data and HOLD activation."""
from io import BytesIO
import json
import subprocess

import pytest

from oracle_autopilot import codex_cli_once as once


def pull(number=1935):
    repo = {'full_name': once.REPOSITORY}
    return {'number': number, 'state': 'open',
            'head': {'sha': 'a' * 40, 'ref': 'codex/checked', 'repo': repo},
            'base': {'repo': repo}}


class Reply:
    status = 200

    def __init__(self, url, body):
        self.url, self.body = url, BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.body.close()

    def geturl(self):
        return self.url

    def read(self, count):
        return self.body.read(count)


def test_exact_repository_request_and_pr():
    def open_pr(req, timeout):
        assert timeout == 10
        assert req.full_url == 'https://api.github.com/repos/olegmed1-art/bridge-video-free/pulls/1935'
        return Reply(req.full_url, json.dumps(pull()).encode())
    assert once.read_pr(1935, 'scoped-token', open_pr) == pull()


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(number=1936),
    lambda p: p['head']['repo'].update(full_name='other/fork'),
    lambda p: p['base'].update(repo={'full_name': 'other/repo'}),
    lambda p: p['head'].update(sha='x' * 40),
])
def test_forged_primary_pr_rejected(mutation):
    pr = pull()
    mutation(pr)
    with pytest.raises(ValueError):
        once.read_pr(1935, 'scoped-token',
                     lambda req, timeout: Reply(req.full_url, json.dumps(pr).encode()))


def test_redirect_and_oversized_pr_rejected():
    with pytest.raises(ValueError, match='HTTP_INVALID'):
        once.read_pr(1935, 'scoped-token', lambda req, timeout: Reply('https://evil.example', b'{}'))
    with pytest.raises(ValueError, match='TOO_LARGE'):
        once.read_pr(1935, 'scoped-token',
                     lambda req, timeout: Reply(req.full_url, b'x' * (once.RESPONSE_LIMIT + 1)))


def test_hold_fences_all_provider_and_database_io(monkeypatch):
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE', 'HOLD')
    monkeypatch.setattr(once.bridge, 'configure_profile', lambda *args: pytest.fail('CLI touched'))
    with pytest.raises(ValueError, match='AUTOPILOT_ADMISSION_HELD'):
        once.one_step('12345678-1234-4234-8234-123456789012', 'unused', 'unused', 'ubuntu')


def test_invalid_dispatch_fences_all_provider_and_database_io(monkeypatch):
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE', 'ACTIVE')
    monkeypatch.setattr(once.bridge, 'configure_profile', lambda *args: pytest.fail('CLI touched'))
    with pytest.raises(ValueError, match='NATIVE_DISPATCH_ID_INVALID'):
        once.one_step('unexpected', 'unused', 'unused', 'ubuntu')


@pytest.mark.parametrize('unit_env,active', [('AUTOPILOT_ADMISSION_MODE=HOLD', 'active'),
                                             ('AUTOPILOT_ADMISSION_MODE=ACTIVE', 'inactive'),
                                             ('', 'active')])
def test_caller_cannot_override_live_hold(monkeypatch, unit_env, active):
    monkeypatch.setenv('AUTOPILOT_ADMISSION_MODE', 'ACTIVE')
    monkeypatch.setattr(once.os, 'uname', lambda: type('Uname', (), {'nodename': once.LIGHT_HOST})())
    monkeypatch.setattr(once.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0,
                                                    f'ActiveState={active}\nSubState=running\n'
                                                    f'Environment={unit_env}\n', ''))
    with pytest.raises(ValueError, match='AUTOPILOT_ADMISSION_HELD'):
        once.require_live_active()
