import pytest
from ops.incident.light_pilot_cleanup_20260928 import parse


def test_two_environment_files_preserve_exact_order():
    assert parse('ExecStart=exact\nEnvironmentFiles=/first (ignore_errors=no)\nEnvironmentFiles=/second (ignore_errors=no)', ['ExecStart','EnvironmentFiles']) == {'ExecStart':'exact','EnvironmentFiles':'/first (ignore_errors=no) /second (ignore_errors=no)'}


@pytest.mark.parametrize('raw', ['EnvironmentFiles=/one', 'EnvironmentFiles=/one\nEnvironmentFiles=/two\nEnvironmentFiles=/three', 'EnvironmentFiles=/one\nEnvironmentFiles=/two\nUnknown=x', 'EnvironmentFiles=/one\nEnvironmentFiles=/two\nExecStart=x\nExecStart=y'])
def test_malformed_refused(raw):
    with pytest.raises(Exception):
        parse(raw, ['EnvironmentFiles','ExecStart'])
