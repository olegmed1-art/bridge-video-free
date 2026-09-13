from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import diana_longitudinal_postprocess_v4_2 as v42


class DianaLongitudinalPostprocessV42Tests(unittest.TestCase):
    def test_database_runtime_counts_do_not_change_generation_identity(self):
        first = {
            'status': 'PERSISTED', 'inserted': 691, 'already_existing': 0,
            'candidate_records': 691, 'analysis_run_id': 'run-a',
            'input_fingerprint': 'fingerprint', 'method_version': 'diana-quality-v4.2',
            'authoritative_tables_modified': False,
        }
        repeat = dict(first, inserted=0, already_existing=691, analysis_run_id='run-b')
        a = v42._stable_database_summary(first, 691)
        b = v42._stable_database_summary(repeat, 691)
        self.assertEqual(a, b)
        p1 = {'created_at': '2026-01-01T00:00:00Z', 'database_staging': a, 'job_id': '0' * 32}
        p2 = {'created_at': '2026-01-01T00:00:00Z', 'database_staging': b, 'job_id': '0' * 32}
        self.assertEqual(v42._generation_key(p1), v42._generation_key(p2))

    def test_stable_created_at_never_uses_wall_clock(self):
        self.assertEqual(
            v42._stable_created_at({'createdAt': '2026-02-03T04:05:06Z'}, {}, {}),
            '2026-02-03T04:05:06Z',
        )
        self.assertEqual(v42._stable_created_at({}, {}, {}), '1970-01-01T00:00:00Z')

    def test_existing_same_named_artifact_must_match_sha(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'artifact.json'
            path.write_bytes(b'canonical')

            def same_download(_token, _file_id, target):
                Path(target).write_bytes(b'canonical')

            with patch.object(v42.base.io, 'search', return_value=[{'id': 'existing', 'name': path.name, 'modifiedTime': '2026'}]), \
                 patch.object(v42.base.io, 'download', side_effect=same_download):
                result = v42._upload_idempotent_verified('t', 'parent', path, 'application/json')
            self.assertEqual(result['status'], 'already_exists_verified')

            def bad_download(_token, _file_id, target):
                Path(target).write_bytes(b'different')

            with patch.object(v42.base.io, 'search', return_value=[{'id': 'existing', 'name': path.name, 'modifiedTime': '2026'}]), \
                 patch.object(v42.base.io, 'download', side_effect=bad_download):
                with self.assertRaisesRegex(RuntimeError, 'LONGITUDINAL_EXISTING_ARTIFACT_CONTENT_MISMATCH'):
                    v42._upload_idempotent_verified('t', 'parent', path, 'application/json')


if __name__ == '__main__':
    unittest.main()
