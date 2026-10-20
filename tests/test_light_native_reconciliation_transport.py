"""Execute transport refusal boundaries without SSH, secrets or live services."""
import ast
import base64
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops import light_native_current_runner as runner
from ops import light_native_reconciliation as q
from tests.test_light_native_reconciliation import reference_value, seal

ROOT = Path(__file__).resolve().parents[1]


class CurrentTransportTests(unittest.TestCase):
    def test_bootstrap_rejects_bad_bindings_and_compiles(self):
        args = ['a'*40, 'b'*64, 'c'*64, 'd'*64, 'e'*64, 'f'*64, 123, 1]
        compile(runner.bootstrap(*args), 'current-bootstrap', 'exec')
        for position, bad in ((0, '../x'), (1, 'bad'), (2, 'bad'), (3, 'bad'), (4, 'bad'),
                              (5, 'bad'), (6, True), (6, 0), (6, 10**20), (7, False), (7, 10**6)):
            changed = args.copy(); changed[position] = bad
            with self.subTest(position=position, bad=bad), self.assertRaises(ValueError):
                runner.bootstrap(*changed)

    def execute_bootstrap(self, wire, pins, *, reaches_extraction=False, code=None):
        output = io.StringIO()
        code = runner.bootstrap('a'*40, *pins, 123, 1) if code is None else code
        # All these malformed wires must fail before package extraction/import.
        with patch.object(os, 'geteuid', return_value=0, create=True), \
             patch.object(os, 'uname', return_value=SimpleNamespace(nodename='autopilot-lite-vnic'), create=True), \
             patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(wire))), \
             patch.object(sys, 'stdout', output), \
             patch.object(tempfile, 'TemporaryDirectory', side_effect=AssertionError('EXTRACTION_FORBIDDEN')) as extract:
            with self.assertRaises(SystemExit) as stopped:
                exec(compile(code, 'current-bootstrap', 'exec'), {'__name__': '__main__'})
            self.assertEqual(stopped.exception.code, 2)
            if reaches_extraction: extract.assert_called_once()
            else: extract.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), runner.REFUSED)
        self.assertNotIn('PRIVATE_SENTINEL', output.getvalue())

    def valid_preimport_envelope(self):
        controller = q.encoded(dict(version=1, kind='LIGHT_LANE_CONTROLLER', source='a'*40,
                                    helpers={'ops/__init__.py': ''}))
        helpers = q.encoded(dict(version=1, kind='CURRENT_RECONCILIATION_HELPERS', source='a'*40,
                                helpers=dict.fromkeys(q.HELPERS, '# PRIVATE_SENTINEL\n')))
        blobs = dict(controller=controller, runtime=b'2', capture_helpers=helpers,
                     legacy_package=b'4', payload=b'5')
        pins = [q.sha(raw) for raw in blobs.values()]
        envelope = dict(**{key: base64.b64encode(raw).decode() for key, raw in blobs.items()}, token='token')
        return envelope, pins

    def test_all_five_hashes_checked_before_extracting_or_importing(self):
        envelope, pins = self.valid_preimport_envelope(); wire = q.encoded(envelope)
        self.execute_bootstrap(wire, pins, reaches_extraction=True)
        for index in range(5):
            changed = pins.copy(); changed[index] = '0'*64
            with self.subTest(index=index): self.execute_bootstrap(wire, changed)

    def test_envelope_extra_duplicate_invalid_base64_and_oversize_fail_closed(self):
        envelope, pins = self.valid_preimport_envelope()
        self.execute_bootstrap(q.encoded(envelope), pins, reaches_extraction=True)
        for wire in (q.encoded(dict(**envelope, credential='PRIVATE_SENTINEL')),
                     b'{"token":"duplicate",'+q.encoded(envelope)[1:],
                     q.encoded(dict(envelope, runtime='!PRIVATE_SENTINEL')),
                     b' '*(16*1024*1024+1)):
            with self.subTest(size=len(wire)): self.execute_bootstrap(wire, pins)

    def test_payload_scope_matches_all_existing_dispatch_inputs(self):
        reference = reference_value(); raw = seal(reference)
        inputs = dict(expected_main_sha=reference['source'], accepted_payload_sha256=q.sha(raw),
            accepted_controller_sha256=reference['accepted_controller_sha256'],
            accepted_runtime_sha256=reference['accepted_runtime_sha256'], retirement_reference_json=raw.decode())
        with patch.object(Path, 'read_bytes') as read:
            path = Path('synthetic-event.json')
            def write(value): read.return_value = q.encoded(dict(inputs=value))
            write(inputs)
            self.assertEqual(runner.dispatch_inputs(path, reference['source']), (raw, reference))
            for key, bad in (('expected_main_sha', '0'*40), ('accepted_payload_sha256', '0'*64),
                             ('accepted_controller_sha256', '0'*64), ('accepted_runtime_sha256', '0'*64)):
                write(dict(inputs, **{key: bad}))
                with self.subTest(key=key), self.assertRaises((q.Refusal, q.r.Refusal)):
                    runner.dispatch_inputs(path, reference['source'])
            write(dict(inputs, extra='PRIVATE_SENTINEL'))
            with self.assertRaises(q.r.Refusal): runner.dispatch_inputs(path, reference['source'])

    def test_output_never_promotes_unknown_or_swapped_original_pins(self):
        reference = reference_value()
        value = q._result(reference, witness='3'*64, anchor='4'*64)
        self.assertEqual(runner.checked_output(q.encoded(value), 0, reference), (value, 0))
        unknown = q._result(reference, reason='LOCAL_JOURNALS')
        self.assertEqual(runner.checked_output(q.encoded(unknown), 2, reference), (unknown, 2))
        for original in (value, unknown):
            for key in ('original_baseline_sha256', 'original_inventory_sha256'):
                bad = dict(original, **{key: '9'*64})
                with self.subTest(key=key), self.assertRaises(q.Refusal):
                    runner.checked_output(q.encoded(bad), 0 if original is value else 2, reference)
        for value, status in ((unknown, 0), (value, 2), (runner.REFUSED, 0),
                              (dict(runner.REFUSED, exception='PRIVATE_SENTINEL'), 2)):
            with self.subTest(status=status), self.assertRaises((q.Refusal, q.r.Refusal)):
                runner.checked_output(q.encoded(value), status, reference)

    def test_output_size_corruption_duplicates_and_nonauthority(self):
        reference = reference_value(); value = q._result(reference, witness='3'*64, anchor='4'*64)
        for raw in (b'', b'{' , b'x'*4097, b'{"version":1,'+q.encoded(value)[1:]):
            with self.subTest(size=len(raw)), self.assertRaises((q.Refusal, q.r.Refusal)):
                runner.checked_output(raw, 0, reference)
        for key in q.FALSE_FLAGS:
            with self.subTest(key=key), self.assertRaises(q.Refusal):
                runner.checked_output(q.encoded(dict(value, **{key: True})), 0, reference)
        self.assertEqual(runner.checked_output(q.encoded(runner.REFUSED), 2, reference), (runner.REFUSED, 2))

    def test_literal_loader_does_not_execute_git_source(self):
        text = b"CONST='accepted'\nraise RuntimeError('PRIVATE_SENTINEL')\n"
        self.assertEqual(runner._literal(text, 'CONST'), 'accepted')
        for text in (b"CONST=__import__('os').getcwd()", b"CONST=1\nCONST=2", b'OTHER=1'):
            with self.assertRaises((ValueError, TypeError)): runner._literal(text, 'CONST')

    def test_workflow_fixed_current_branch_has_no_database_or_dependency_capability(self):
        text = (ROOT/'.github/workflows/light-native-retirement-observe.yml').read_text()
        current = text.split('      - name: Reconcile original retained inputs', 1)[1].split('      - name: Observe retirement', 1)[0]
        self.assertIn("steps.observation.outputs.current == 'true'", current)
        for forbidden in ('DATABASE_URL', 'LIGHT_MAINTENANCE', 'pip ', 'psycopg'):
            self.assertNotIn(forbidden, current)
        self.assertIn("steps.observation.outputs.current != 'true'", text.split('      - name: Observe retirement', 1)[1])
        tree = ast.parse((ROOT/'ops/light_native_lane_run_guard.py').read_bytes())
        pin = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'OBSERVATION_WORKFLOW_SHA256' for target in node.targets))
        self.assertEqual(pin, q.sha(text.replace('\r\n', '\n').encode()))


if __name__ == '__main__': unittest.main()
