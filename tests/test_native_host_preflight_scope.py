"""Exercise the actual selector and its loaded-source trigger dependencies."""
import ast
from contextlib import contextmanager, redirect_stdout
import fnmatch
import io
import json
import os
from pathlib import Path
import re
import textwrap
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = '.github/workflows/native-host-preflight.yml'


def workflow_text():
    return (ROOT / WORKFLOW).read_text(encoding='utf-8')


def trigger_paths(text):
    block = re.search(r'(?m)^  pull_request:\n    paths:\n((?:      - .+\n)+)', text)
    if block is None:
        raise AssertionError('Exact pull_request.paths block required')
    result = [ast.literal_eval(line[8:]) for line in block[1].splitlines()]
    if not result or any(type(value) is not str for value in result):
        raise AssertionError('Literal path filters required')
    return result


def loaded_codec_sources():
    tree = ast.parse((ROOT / 'tests/test_light_native_service_authority.py').read_bytes())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == 'actual_codec_contracts']
    if len(functions) != 1:
        raise AssertionError('Actual codec loader required')
    calls = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'load']
    if not calls or any(not node.args or not isinstance(node.args[0], ast.Constant)
                        or type(node.args[0].value) is not str for node in calls):
        raise AssertionError('Every loaded codec source must be identifiable')
    integration = ast.parse((ROOT / 'tests/test_light_native_plan_codec.py').read_bytes())
    selected = [node for node in ast.walk(integration) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == 'selected']
    if not selected or any(not node.args or not isinstance(node.args[0], ast.Constant)
                           or type(node.args[0].value) is not str for node in selected):
        raise AssertionError('Every integration source must be identifiable')
    return sorted({node.args[0].value for node in (*calls, *selected)})


def select(changed, *, base_text=None, head_text=None):
    text = workflow_text() if head_text is None else head_text
    base_text = text if base_text is None else base_text
    match = re.search(r"(?ms)^        run: \|\n          /usr/bin/python3 -I -S -B - <<'PY'\n(.*?)^          PY\n", text)
    if match is None:
        raise AssertionError('Actual scope script required')
    script = textwrap.dedent(match[1])
    base, head = 'a' * 40, 'b' * 40
    def git(argv, *, timeout):
        if argv == ['git', 'diff', '--no-renames', '--name-only', '-z', base, head, '--']:
            return b''.join(name.encode('utf-8') + b'\0' for name in changed)
        if argv == ['git', 'show', base + ':' + WORKFLOW]:
            return base_text.encode('utf-8')
        if argv == ['git', 'show', head + ':' + WORKFLOW]:
            return text.encode('utf-8')
        raise AssertionError('Unexpected selector command')
    output = io.StringIO()
    @contextmanager
    def open_output(path, mode):
        if path != 'NATIVE_SCOPE_OUTPUT' or mode != 'a':
            raise AssertionError('Unexpected selector output port')
        yield output
    env = {'BASE': base, 'HEAD': head, 'GITHUB_OUTPUT': 'NATIVE_SCOPE_OUTPUT'}
    with patch.dict(os.environ, env), patch('subprocess.check_output', side_effect=git), \
         patch('builtins.open', side_effect=open_output), redirect_stdout(io.StringIO()):
        exec(compile(script, WORKFLOW, 'exec'), {})
    rows = dict(line.split('=', 1) for line in output.getvalue().splitlines())
    if any(value not in ('true', 'false') for value in rows.values()):
        raise AssertionError('Boolean proof outputs required')
    return {name: value == 'true' for name, value in rows.items()}


class ScopeContracts(unittest.TestCase):
    def assert_codec_only(self, changed):
        self.assertEqual(select(changed), dict(legacy=False, transition=False, host=False, codec=True))

    def test_every_actual_loaded_codec_source_triggers_workflow(self):
        patterns = trigger_paths(workflow_text())
        for name in loaded_codec_sources():
            with self.subTest(source=name):
                self.assertTrue(any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns), name)

    def test_each_loaded_source_selects_codec_and_only_its_other_proofs(self):
        for name in loaded_codec_sources():
            with self.subTest(source=name):
                self.assertEqual(select([name]), dict(legacy=name=='ops/light_native_retirement.py',
                    transition=False, host=False, codec=True))

    def test_codec_consumers_and_tests_trigger_and_select_their_contract(self):
        patterns = trigger_paths(workflow_text())
        for name in ('ops/light_native_lane_issuer.py', 'tests/test_light_native_plan_codec.py',
                     'tests/test_native_host_preflight_scope.py'):
            with self.subTest(source=name):
                self.assertTrue(any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns), name)
                self.assert_codec_only([name])

    def test_unchanged_workflow_job_bodies_select_only_portable_scope_proof(self):
        self.assert_codec_only([WORKFLOW])

    def test_actual_host_and_transition_sources_keep_their_separate_proofs(self):
        for name in ('ops/light_native_service_unit.py', 'ops/light_native_readiness.py'):
            with self.subTest(source=name):
                self.assertEqual(select([name]), dict(legacy=False, transition=False, host=True, codec=False))
        self.assertEqual(select(['ops/light_native_legacy_transition.py']),
                         dict(legacy=True, transition=True, host=True, codec=False))

    def test_old_executable_job_changes_still_select_affected_proofs(self):
        text = workflow_text()
        for name, marker, expected in (
            ('root', 'assert result.testsRun >= 150', dict(legacy=True, transition=False, host=False, codec=True)),
            ('transition', 'ci "$GITHUB_WORKSPACE" "$GITHUB_SHA"', dict(legacy=False, transition=True, host=False, codec=True)),
            ('host', 'assert result.testsRun >= 107', dict(legacy=False, transition=False, host=True, codec=True)),
        ):
            self.assertEqual(text.count(marker), 1)
            with self.subTest(job=name):
                self.assertEqual(select([WORKFLOW], base_text=text, head_text=text.replace(marker, marker + ' changed', 1)), expected)

    def test_unrelated_changes_do_not_request_native_proofs(self):
        self.assertEqual(select(['docs/unrelated.md']),
                         dict(legacy=False, transition=False, host=False, codec=False))


if __name__ == '__main__':
    unittest.main()
