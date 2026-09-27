"""Timing observes the same calls without exporting inputs or changing failures."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase
from unittest.mock import patch

from ops import native_maintenance_stage_launcher as launcher


class TimingTests(TestCase):
    def test_same_exception_and_no_exception_text_in_profile(self):
        profile = launcher.TimingProfile()
        refusal = RuntimeError('private-request-secret')
        with patch.object(launcher.time, 'monotonic', side_effect=[10, 10.025]):
            with self.assertRaises(RuntimeError) as caught:
                with profile.observe('rpc_store'):
                    raise refusal
        self.assertIs(caught.exception, refusal)
        report = profile.report()
        self.assertEqual(report['operations']['rpc_store']['calls'], 1)
        self.assertNotIn('private', json.dumps(report))
        self.assertTrue(report['runner_only'])
        self.assertTrue(report['overlapping_durations'])
        self.assertEqual(report['rpc_budget_seconds'], 60)

    def test_measurement_failure_does_not_mask_operation_refusal(self):
        profile = launcher.TimingProfile()
        refusal = RuntimeError('original')
        with patch.object(launcher.time, 'monotonic', side_effect=[10, ValueError('clock')]):
            with self.assertRaises(RuntimeError) as caught:
                with profile.observe('rpc_wait'):
                    raise refusal
        self.assertIs(caught.exception, refusal)
        self.assertTrue(profile.report()['incomplete'])

    def test_clock_failure_does_not_skip_the_original_operation(self):
        profile = launcher.TimingProfile()
        calls = []
        with patch.object(launcher.time, 'monotonic', side_effect=ValueError('clock')):
            with profile.observe('github_get'):
                calls.append('called')
        self.assertEqual(calls, ['called'])
        self.assertTrue(profile.report()['incomplete'])

    def test_exchange_counters_exclude_prelaunch_and_final_readback(self):
        profile = launcher.TimingProfile()
        with patch.object(launcher, 'PROFILE', profile):
            for phase in ('request_retention', 'host_exchange', 'independent_readback_after_exit'):
                with patch.object(launcher, 'PHASE', phase):
                    with launcher.measured('oci_read'):
                        pass
        report = profile.report()
        self.assertEqual(report['operations']['oci_read']['calls'], 3)
        self.assertEqual(report['host_exchange_operations']['oci_read']['calls'], 1)

    def test_report_failure_is_sanitized_and_does_not_mask_refusal(self):
        profile = launcher.TimingProfile()
        with patch.object(launcher, 'PROFILE', profile), \
                patch.object(profile, 'report', side_effect=ValueError('private-secret')):
            report = launcher.timing()
        self.assertTrue(report['timing_profile']['incomplete'])
        self.assertNotIn('private-secret', json.dumps(report))

    def test_concurrent_observations_are_bounded_and_detached(self):
        profile = launcher.TimingProfile()
        def record(_):
            with profile.observe('github_get'):
                pass
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(record, range(100)))
        report = profile.report()
        self.assertEqual(report['operations']['github_get']['calls'], 100)
        report['operations']['github_get']['calls'] = 999
        self.assertEqual(profile.report()['operations']['github_get']['calls'], 100)
        with self.assertRaisesRegex(ValueError, 'TIMING_LABEL_INVALID'):
            with profile.observe('secret-url'):
                self.fail('Invalid labels must not enter the operation')
        self.assertNotIn('secret-url', json.dumps(profile.report()))

    def test_api_calls_original_once_without_caching_or_retry(self):
        profile = launcher.TimingProfile()
        path = '/private/path?secret=not-for-logs'
        result = object()
        with patch.object(launcher, 'PROFILE', profile):
            api = launcher.MeasuredAPI('private-token')
            with patch.object(launcher.API, 'get', return_value=result) as original:
                self.assertIs(api.get(path), result)
                original.assert_called_once_with(path)
            refusal = RuntimeError('original')
            with patch.object(launcher.API, 'get', side_effect=refusal) as original:
                with self.assertRaises(RuntimeError) as caught:
                    api.get(path)
                self.assertIs(caught.exception, refusal)
                original.assert_called_once_with(path)
        self.assertEqual(profile.report()['operations']['github_get']['calls'], 2)
        self.assertNotIn('private', json.dumps(profile.report()))

    def test_store_calls_original_once_with_unchanged_arguments(self):
        profile = launcher.TimingProfile()
        # Avoid OCI setup: the adapter call itself is the mocked boundary.
        store = object.__new__(launcher.MeasuredStore)
        result = object()
        with patch.object(launcher, 'PROFILE', profile):
            for method, label in [('get_object', 'oci_read'), ('put_object', 'oci_write')]:
                with patch.object(launcher.adapter.OCIJournalStore, '_call', return_value=result) as original:
                    self.assertIs(store._call(method, 'private-bucket', body=b'secret'), result)
                    original.assert_called_once_with(method, 'private-bucket', body=b'secret')
                self.assertEqual(profile.report()['operations'][label]['calls'], 1)
        self.assertNotIn('secret', json.dumps(profile.report()))
        self.assertNotIn('bucket', json.dumps(profile.report()))

    def test_source_guard_does_not_change_order_or_arguments(self):
        profile = launcher.TimingProfile()
        with patch.object(launcher, 'PROFILE', profile), \
                patch.dict(launcher.os.environ, {'EXPECTED_MAIN': 'a'*40}), \
                patch.object(launcher, 'source_check') as original:
            launcher.source_guard()
            original.assert_called_once_with('a'*40)
        self.assertEqual(profile.report()['operations']['source_check']['calls'], 1)
