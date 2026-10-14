import unittest

from ops.ibm_vpc_lifecycle import (
    ObservationError,
    SCHEMA,
    decide,
    video_queue_readiness_observation_fields,
    video_queue_work_counts,
)


def observation(**overrides):
    value = {
        "schema": SCHEMA,
        "observed_at_epoch": 1000,
        "vpc_status": "stopped",
        "queue_snapshot_complete": True,
        "video_queue_snapshot_complete": True,
        "lease_snapshot_complete": True,
        "worker_snapshot_complete": True,
        "storage_snapshot_complete": True,
        "disk_snapshot_complete": True,
        "disk_headroom_safe": True,
        "eligible_pending_jobs": 0,
        "running_jobs": 0,
        "active_leases": 0,
        "host_active_jobs": 0,
        "active_spool_items": 0,
        "maintenance_leases": 0,
        "video_queue_readiness_blocked": False,
        "video_queue_retry_waiting_jobs": 0,
        "next_queue_wake_epoch": None,
        "idle_since_epoch": 1000,
    }
    value.update(overrides)
    return value


class IBMVPCLifecycleDecisionTests(unittest.TestCase):

    @staticmethod
    def readiness_row(**overrides):
        row = {
            "observed_at": "2026-09-23T12:00:00+00:00",
            "processing_profile": "bridge_3_1_free",
            "algorithm_revision": "3.1-free-r25.16",
            "batch_status": "QUEUED_CANARY",
            "runnable_now_count": 0,
            "active_leases_count": 0,
            "retry_waiting_count": 0,
            "next_retry_at": None,
            "pending_canary_count": 0,
            "expired_attempts_exhausted_count": 0,
            "blocked_nonterminal_count": 0,
            "unknown_job_status_count": 0,
            "unknown_batch_status_count": 0,
            "invalid_lease_shape_count": 0,
        }
        row.update(overrides)
        return row

    def readiness_fields(self, rows, *, complete=True, supported=None):
        return video_queue_readiness_observation_fields(
            rows,
            snapshot_complete=complete,
            supported_worker_tuples=supported or {("bridge_3_1_free", "3.1-free-r25.16")},
            other_observed_at_epoch=1790164800,
        )

    def test_video_pending_canary_does_not_wake_ibm(self):
        counts = video_queue_work_counts({"PENDING_CANARY": 1})
        self.assertEqual({"eligible_pending_jobs": 0, "running_jobs": 0}, counts)
        result = decide(observation(**counts), now_epoch=1000)
        self.assertEqual("IDLE_STOPPED", result["decision"])

    def test_video_queued_and_leased_jobs_are_compute_work(self):
        queued = video_queue_work_counts({"QUEUED": 2})
        leased = video_queue_work_counts({"LEASED": 1})
        self.assertEqual({"eligible_pending_jobs": 2, "running_jobs": 0}, queued)
        self.assertEqual({"eligible_pending_jobs": 0, "running_jobs": 1}, leased)
        result = decide(
            observation(vpc_status="running", **leased),
            now_epoch=1000,
        )
        self.assertEqual("KEEP_RUNNING", result["decision"])

    def test_video_queue_unknown_status_or_invalid_count_fails_closed(self):
        with self.assertRaises(ObservationError):
            video_queue_work_counts({"FUTURE_STATUS": 1})
        with self.assertRaises(ObservationError):
            video_queue_work_counts({"QUEUED": True})

    def test_readiness_adapter_preserves_future_retry_wake_without_starting_stopped_vm(self):
        fields = self.readiness_fields([self.readiness_row(
            observed_at="2026-09-23T12:00:00+00:00",
            retry_waiting_count=2,
            next_retry_at="2026-09-23T12:05:00+00:00",
        )])
        self.assertTrue(fields["video_queue_snapshot_complete"])
        self.assertEqual(2, fields["video_queue_retry_waiting_jobs"])
        self.assertEqual(1790165100, fields["next_queue_wake_epoch"])
        self.assertEqual(0, fields["eligible_pending_jobs"])
        self.assertEqual(0, fields["running_jobs"])
        result = decide(observation(**fields), now_epoch=1790164801)
        self.assertEqual({
            "decision": "WAIT_FOR_RETRY",
            "reason": "retry_not_due",
            "wake_at_epoch": 1790165100,
        }, result)

    def test_readiness_adapter_maps_due_and_expired_retryable_leases_as_runnable(self):
        fields = self.readiness_fields([self.readiness_row(runnable_now_count=2)])
        self.assertEqual(2, fields["eligible_pending_jobs"])
        self.assertEqual(0, fields["active_leases"])
        result = decide(observation(**fields), now_epoch=1790164800)
        self.assertEqual("START", result["decision"])

    def test_readiness_adapter_maps_unexpired_leases_as_active_work(self):
        fields = self.readiness_fields([self.readiness_row(active_leases_count=2)])
        self.assertEqual(2, fields["running_jobs"])
        self.assertEqual(2, fields["active_leases"])
        result = decide(observation(vpc_status="running", **fields), now_epoch=1790164800)
        self.assertEqual("KEEP_RUNNING", result["decision"])

    def test_exhausted_blocked_and_unknown_aggregates_never_prove_idle(self):
        cases = (
            self.readiness_row(expired_attempts_exhausted_count=1),
            self.readiness_row(batch_status="CANARY_REVIEW", blocked_nonterminal_count=1),
            self.readiness_row(unknown_job_status_count=1),
            self.readiness_row(unknown_batch_status_count=1),
            self.readiness_row(invalid_lease_shape_count=1),
        )
        for row in cases:
            with self.subTest(row=row):
                fields = self.readiness_fields([row])
                self.assertTrue(fields["video_queue_snapshot_complete"])
                self.assertTrue(fields["video_queue_readiness_blocked"])
                result = decide(observation(**fields), now_epoch=1790164800)
                self.assertEqual("HOLD", result["decision"])

    def test_canary_waiting_is_visible_but_does_not_wake_or_block_idle(self):
        fields = self.readiness_fields([self.readiness_row(pending_canary_count=1)])
        self.assertEqual(1, fields["video_queue_pending_canary_jobs"])
        self.assertEqual(0, fields["eligible_pending_jobs"])
        result = decide(observation(**fields), now_epoch=1790164800)
        self.assertEqual("IDLE_STOPPED", result["decision"])

    def test_unavailable_empty_or_incomplete_readiness_snapshot_holds(self):
        for rows, complete in ((None, True), ([], True), ([self.readiness_row()], False)):
            with self.subTest(rows=rows, complete=complete):
                fields = self.readiness_fields(rows, complete=complete)
                self.assertFalse(fields["video_queue_snapshot_complete"])
                result = decide(observation(**fields), now_epoch=1790164800)
                self.assertEqual("HOLD", result["decision"])

    def test_oldest_source_timestamp_controls_freshness(self):
        old = self.readiness_row(observed_at="2026-09-23T11:59:00+00:00")
        fields = self.readiness_fields([old])
        self.assertEqual(1790164740, fields["observed_at_epoch"])
        result = decide(observation(**fields), now_epoch=1790164800)
        self.assertEqual("HOLD", result["decision"])
        self.assertEqual("observation_stale", result["reason"])

    def test_malformed_or_inconsistent_aggregate_never_claims_idle(self):
        bad_rows = (
            self.readiness_row(runnable_now_count=-1),
            self.readiness_row(active_leases_count=float("inf")),
            self.readiness_row(retry_waiting_count=True),
            self.readiness_row(retry_waiting_count=1, next_retry_at=None),
            self.readiness_row(retry_waiting_count=0, next_retry_at="2026-09-23T12:05:00+00:00"),
            self.readiness_row(retry_waiting_count=1, next_retry_at="2026-09-23T12:00:00+00:00"),
            self.readiness_row(batch_status="UNRECOGNIZED"),
            self.readiness_row(batch_status=[]),
            {"observed_at": "2026-09-23T12:00:00+00:00"},
        )
        for row in bad_rows:
            with self.subTest(row=row):
                fields = self.readiness_fields([row])
                self.assertFalse(fields["video_queue_snapshot_complete"])
                result = decide(observation(vpc_status="running", idle_since_epoch=1, **fields), now_epoch=1790164800)
                self.assertEqual("HOLD", result["decision"])

        duplicate_rows = [self.readiness_row(), self.readiness_row()]
        duplicate_fields = self.readiness_fields(duplicate_rows)
        self.assertFalse(duplicate_fields["video_queue_snapshot_complete"])
        self.assertEqual("HOLD", decide(observation(**duplicate_fields), now_epoch=1790164800)["decision"])

        disagreeing_rows = [
            self.readiness_row(),
            self.readiness_row(batch_status="RUNNING", observed_at="2026-09-23T12:00:01+00:00"),
        ]
        disagreeing_fields = self.readiness_fields(disagreeing_rows)
        self.assertFalse(disagreeing_fields["video_queue_snapshot_complete"])
        self.assertEqual("HOLD", decide(observation(**disagreeing_fields), now_epoch=1790164800)["decision"])

    def test_active_work_for_unapproved_profile_holds(self):
        fields = self.readiness_fields([self.readiness_row(
            processing_profile="unknown_profile",
            runnable_now_count=1,
        )])
        self.assertEqual(1, fields["video_queue_unsupported_worker_rows"])
        self.assertTrue(fields["video_queue_readiness_blocked"])
        self.assertEqual("HOLD", decide(observation(**fields), now_epoch=1790164800)["decision"])

    def test_retry_wake_inconsistent_with_snapshot_holds(self):
        result = decide(
            observation(video_queue_retry_waiting_jobs=1, next_queue_wake_epoch=1001),
            now_epoch=1001,
        )
        self.assertEqual("HOLD", result["decision"])
        self.assertEqual("video_queue_retry_due_without_runnable_count", result["reason"])

    def test_admitted_job_starts_stopped_vm(self):
        result = decide(observation(eligible_pending_jobs=1), now_epoch=1000)
        self.assertEqual({"decision": "START", "reason": "admitted_heavy_work"}, result)

    def test_active_work_keeps_running_vm_on(self):
        result = decide(
            observation(vpc_status="running", running_jobs=1),
            now_epoch=1000,
        )
        self.assertEqual("KEEP_RUNNING", result["decision"])

    def test_zero_work_must_wait_for_idle_grace(self):
        result = decide(
            observation(vpc_status="running", idle_since_epoch=500),
            now_epoch=1000,
        )
        self.assertEqual("KEEP_RUNNING", result["decision"])
        self.assertEqual("idle_grace_not_elapsed", result["reason"])

    def test_complete_idle_proof_after_grace_allows_stop_intent(self):
        result = decide(
            observation(vpc_status="running", idle_since_epoch=100),
            now_epoch=1000,
        )
        self.assertEqual({"decision": "STOP", "reason": "complete_idle_proof_and_grace"}, result)

    def test_unknown_source_blocks_even_when_other_source_reports_work(self):
        result = decide(
            observation(vpc_status="stopped", eligible_pending_jobs=1,
                        storage_snapshot_complete=False),
            now_epoch=1000,
        )
        self.assertEqual("HOLD", result["decision"])
        self.assertEqual("storage_snapshot_complete_not_proven", result["reason"])

    def test_stale_or_future_observation_fails_closed(self):
        self.assertEqual("HOLD", decide(observation(observed_at_epoch=900), now_epoch=1000)["decision"])
        self.assertEqual("HOLD", decide(observation(observed_at_epoch=1006), now_epoch=1000)["decision"])

    def test_orphan_lease_never_causes_start_or_stop(self):
        result = decide(
            observation(vpc_status="stopped", active_leases=1),
            now_epoch=1000,
        )
        self.assertEqual("HOLD", result["decision"])
        self.assertEqual("orphan_lease_or_host_work", result["reason"])

    def test_starting_vm_waits_and_stopping_vm_is_reconciled(self):
        start = decide(observation(vpc_status="starting", eligible_pending_jobs=1), now_epoch=1000)
        stop = decide(observation(vpc_status="stopping", eligible_pending_jobs=1), now_epoch=1000)
        self.assertEqual("WAIT_FOR_READY", start["decision"])
        self.assertEqual("WAIT_THEN_RECONCILE", stop["decision"])

    def test_failed_or_disk_unknown_never_authorizes_mutation(self):
        self.assertEqual("HOLD", decide(observation(vpc_status="failed"), now_epoch=1000)["decision"])
        self.assertEqual("HOLD", decide(observation(eligible_pending_jobs=1, disk_headroom_safe=False), now_epoch=1000)["decision"])
        idle = decide(observation(vpc_status="running", idle_since_epoch=100), now_epoch=1000)
        self.assertEqual("STOP", idle["decision"])

    def test_module_is_decision_only_and_has_no_power_api_calls(self):
        import inspect
        from ops import ibm_vpc_lifecycle
        source = inspect.getsource(ibm_vpc_lifecycle)
        self.assertNotIn("create_action(", source)
        self.assertNotIn("urlopen(", source)


if __name__ == "__main__":
    unittest.main()
