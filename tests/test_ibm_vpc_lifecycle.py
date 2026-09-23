import unittest

from ops.ibm_vpc_lifecycle import ObservationError, SCHEMA, decide, video_queue_work_counts


def observation(**overrides):
    value = {
        "schema": SCHEMA,
        "observed_at_epoch": 1000,
        "vpc_status": "stopped",
        "queue_snapshot_complete": True,
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
        "idle_since_epoch": 1000,
    }
    value.update(overrides)
    return value


class IBMVPCLifecycleDecisionTests(unittest.TestCase):

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
