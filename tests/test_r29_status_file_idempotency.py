from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import route_drive_job_outputs as route


class R29StatusFileIdempotencyTests(unittest.TestCase):
    def test_identical_existing_status_is_reused(self):
        payload = {'status': 'SPEAKER_MAPPING_ANONYMOUS_ONLY', 'job_id': '0' * 32}
        original = Mock(return_value={'id': 'new'})
        guarded = route._r29_idempotent_upload_wrapper(original)
        existing = {'id': 'existing', 'name': 'R29_IDENTITY_STATUS_x_ANONYMOUS.json', 'modifiedTime': '2026'}
        with patch.object(route.io, 'search', return_value=[existing]), \
             patch.object(route, '_read_json', return_value=dict(payload)):
            result = guarded('token', 'parent', existing['name'], payload)
        original.assert_not_called()
        self.assertEqual(result['id'], 'existing')
        self.assertEqual(result['idempotency_status'], 'already_exists_verified')

    def test_conflicting_same_named_status_fails_closed(self):
        payload = {'status': 'SPEAKER_MAPPING_ANONYMOUS_ONLY', 'job_id': '0' * 32}
        original = Mock(return_value={'id': 'new'})
        guarded = route._r29_idempotent_upload_wrapper(original)
        existing = {'id': 'existing', 'name': 'R29_IDENTITY_STATUS_x_ANONYMOUS.json', 'modifiedTime': '2026'}
        with patch.object(route.io, 'search', return_value=[existing]), \
             patch.object(route, '_read_json', return_value={'status': 'DIFFERENT'}):
            with self.assertRaisesRegex(RuntimeError, 'R29_STATUS_EXISTING_ARTIFACT_CONTENT_MISMATCH'):
                guarded('token', 'parent', existing['name'], payload)
        original.assert_not_called()

    def test_non_status_json_keeps_original_behavior(self):
        original = Mock(return_value={'id': 'new'})
        guarded = route._r29_idempotent_upload_wrapper(original)
        payload = {'status': 'SPEAKER_MAPPING_OPERATIONAL'}
        result = guarded('token', 'parent', 'R29_SPEAKER_MAPPING_abc.json', payload)
        self.assertEqual(result, {'id': 'new'})
        original.assert_called_once_with('token', 'parent', 'R29_SPEAKER_MAPPING_abc.json', payload)


if __name__ == '__main__':
    unittest.main()
