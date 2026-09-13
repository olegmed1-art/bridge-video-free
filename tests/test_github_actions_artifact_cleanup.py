#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / 'tools' / 'github_actions_artifact_cleanup.py'
spec = importlib.util.spec_from_file_location('artifact_cleanup', MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

SHA = 'a' * 64
PART1 = 'b' * 64
PART2 = 'c' * 64
MANIFEST = 'd' * 64


def github_artifact():
    return {
        'id': 123,
        'name': 'state-artifact',
        'size_in_bytes': 30,
        'digest': f'sha256:{SHA}',
        'workflow_run': {'id': 456},
    }


def record(**provenance_updates):
    provenance = {
        'github_artifact_id': 123,
        'github_run_id': 456,
        'github_artifact_name': 'state-artifact',
        'logical_original_sha256': SHA,
        'logical_original_size': 30,
        'roundtrip_sha256_verified': True,
        'storage_layout': 'split-2-parts-plus-manifest',
        'parts': [
            {'size': 10, 'sha256': PART1, 'drive_file_id': 'drive-1'},
            {'size': 20, 'sha256': PART2, 'drive_file_id': 'drive-2'},
        ],
        'manifest_sha256': MANIFEST,
        'manifest_drive_file_id': 'drive-manifest',
    }
    provenance.update(provenance_updates)
    return {
        'artifact_type': 'dds_training_state',
        'asset_id': '11111111-1111-1111-1111-111111111111',
        'provenance': provenance,
    }


class StaticGateTests(unittest.TestCase):
    def test_verified_split_p2_is_eligible(self):
        lifecycle_class, refs = mod.static_gate(github_artifact(), record())
        self.assertEqual(lifecycle_class, 'P2')
        self.assertEqual([r['kind'] for r in refs], ['part', 'part', 'manifest'])

    def test_digest_mismatch_fails_closed(self):
        artifact = github_artifact()
        artifact['digest'] = 'sha256:' + ('e' * 64)
        with self.assertRaisesRegex(mod.GateError, 'digest'):
            mod.static_gate(artifact, record())

    def test_size_mismatch_fails_closed(self):
        artifact = github_artifact()
        artifact['size_in_bytes'] = 31
        with self.assertRaisesRegex(mod.GateError, 'size'):
            mod.static_gate(artifact, record())

    def test_roundtrip_false_fails_closed(self):
        with self.assertRaisesRegex(mod.GateError, 'roundtrip'):
            mod.static_gate(github_artifact(), record(roundtrip_sha256_verified=False))

    def test_split_size_mismatch_fails_closed(self):
        bad_parts = [
            {'size': 9, 'sha256': PART1, 'drive_file_id': 'drive-1'},
            {'size': 20, 'sha256': PART2, 'drive_file_id': 'drive-2'},
        ]
        with self.assertRaisesRegex(mod.GateError, 'reconstruct'):
            mod.static_gate(github_artifact(), record(parts=bad_parts))

    def test_p0_explicit_class_is_never_auto_deleted(self):
        with self.assertRaisesRegex(mod.GateError, 'not eligible'):
            mod.static_gate(github_artifact(), record(lifecycle_class='P0'))

    def test_unknown_type_without_classification_fails_closed(self):
        r = record()
        r['artifact_type'] = 'unknown_type'
        with self.assertRaisesRegex(mod.GateError, 'no approved lifecycle classification'):
            mod.static_gate(github_artifact(), r)

    def test_explicit_p3_allows_unknown_type(self):
        r = record(lifecycle_class='P3')
        r['artifact_type'] = 'future_report_type'
        lifecycle_class, _ = mod.static_gate(github_artifact(), r)
        self.assertEqual(lifecycle_class, 'P3')

    def test_single_file_layout_supported(self):
        r = record(
            storage_layout='single-file',
            drive_file_id='drive-single',
            drive_sha256=SHA,
        )
        r['provenance'].pop('parts', None)
        r['provenance'].pop('manifest_sha256', None)
        r['provenance'].pop('manifest_drive_file_id', None)
        lifecycle_class, refs = mod.static_gate(github_artifact(), r)
        self.assertEqual(lifecycle_class, 'P2')
        self.assertEqual(refs, [{
            'kind': 'single-file',
            'drive_file_id': 'drive-single',
            'sha256': SHA,
            'size': 30,
        }])


if __name__ == '__main__':
    unittest.main()
