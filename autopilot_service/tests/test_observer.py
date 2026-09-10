from __future__ import annotations

import unittest
from datetime import datetime, timezone

from autopilot_app.observer import evaluate_snapshot


NOW = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)


class GlobalSchoolObserverTests(unittest.TestCase):
    def test_clean_snapshot_has_no_findings(self) -> None:
        findings = evaluate_snapshot(
            {
                "tasks": [
                    {
                        "task_id": "task-ok",
                        "status": "DONE",
                        "owner": "VIDEO",
                        "updated_at": "2026-09-10T19:59:00+00:00",
                        "evidence_refs": ["run:123"],
                    }
                ],
                "operations": [],
            },
            now=NOW,
        )
        self.assertEqual(findings, [])

    def test_success_without_evidence_is_error_with_task_evidence(self) -> None:
        findings = evaluate_snapshot(
            {"tasks": [{"task_id": "task-no-receipt", "status": "PASS", "owner": "BOOKS"}]},
            now=NOW,
        )
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["class"], "ERROR")
        self.assertEqual(finding["severity"], "P1")
        self.assertEqual(finding["evidence_refs"], ["task:task-no-receipt"])

    def test_stale_active_task_is_reported(self) -> None:
        findings = evaluate_snapshot(
            {
                "tasks": [
                    {
                        "task_id": "task-stale",
                        "status": "RUNNING",
                        "owner": "AUTOPILOT",
                        "updated_at": "2026-09-10T17:00:00+00:00",
                        "evidence_refs": ["receipt:old-heartbeat"],
                    }
                ]
            },
            now=NOW,
            stale_after_seconds=3600,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["class"], "STALE")

    def test_superseded_active_task_is_stale(self) -> None:
        findings = evaluate_snapshot(
            {
                "tasks": [
                    {
                        "task_id": "task-old",
                        "status": "ACTIVE",
                        "owner": "VIDEO",
                        "updated_at": "2026-09-10T19:59:00+00:00",
                        "superseded": True,
                        "evidence_refs": ["cycle:newer-cycle"],
                    }
                ]
            },
            now=NOW,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["class"], "STALE")
        self.assertIn("superseded", findings[0]["why_it_matters"].lower())

    def test_two_active_owners_on_one_resource_is_loop_duplication(self) -> None:
        findings = evaluate_snapshot(
            {
                "tasks": [
                    {
                        "task_id": "task-a",
                        "status": "ACTIVE",
                        "owner": "A",
                        "resource": "pr:1157",
                        "updated_at": "2026-09-10T19:59:00+00:00",
                        "evidence_refs": ["task:a"],
                    },
                    {
                        "task_id": "task-b",
                        "status": "RUNNING",
                        "owner": "B",
                        "resource": "pr:1157",
                        "updated_at": "2026-09-10T19:59:00+00:00",
                        "evidence_refs": ["task:b"],
                    },
                ]
            },
            now=NOW,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["class"], "LOOP_DUPLICATION")
        self.assertEqual(findings[0]["subject"], "pr:1157")

    def test_repeated_unchanged_check_is_loop_duplication(self) -> None:
        findings = evaluate_snapshot(
            {
                "tasks": [
                    {
                        "task_id": "review-loop",
                        "status": "ACTIVE",
                        "owner": "DISPATCHER",
                        "updated_at": "2026-09-10T19:59:00+00:00",
                        "evidence_refs": ["head:abc"],
                        "repeated_without_new_evidence": True,
                    }
                ]
            },
            now=NOW,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["class"], "LOOP_DUPLICATION")

    def test_two_heavy_operations_on_same_host_is_p0_risk(self) -> None:
        findings = evaluate_snapshot(
            {
                "operations": [
                    {
                        "operation_id": "heavy-1",
                        "status": "RUNNING",
                        "owner": "VIDEO",
                        "host": "oracle-heavy",
                        "heavy": True,
                        "evidence_refs": ["run:1"],
                    },
                    {
                        "operation_id": "heavy-2",
                        "status": "ACTIVE",
                        "owner": "BOOKS",
                        "host": "oracle-heavy",
                        "heavy": True,
                        "evidence_refs": ["run:2"],
                    },
                ]
            },
            now=NOW,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["class"], "RISK")
        self.assertEqual(findings[0]["severity"], "P0")
        self.assertEqual(findings[0]["subject"], "oracle-heavy")

    def test_operation_outside_authority_is_p0_risk(self) -> None:
        findings = evaluate_snapshot(
            {
                "operations": [
                    {
                        "operation_id": "canon-promotion-1",
                        "status": "ACTIVE",
                        "owner": "WORKER",
                        "authority_allowed": False,
                        "evidence_refs": ["task:promotion-1"],
                    }
                ]
            },
            now=NOW,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["class"], "RISK")
        self.assertEqual(findings[0]["severity"], "P0")

    def test_same_evidence_dedupes_deterministically(self) -> None:
        task = {
            "task_id": "same-task",
            "status": "ACTIVE",
            "owner": "DISPATCHER",
            "updated_at": "2026-09-10T19:59:00+00:00",
            "evidence_refs": ["receipt:1"],
            "repeated_without_new_evidence": True,
        }
        first = evaluate_snapshot({"tasks": [task, dict(task)]}, now=NOW)
        second = evaluate_snapshot({"tasks": [dict(task)]}, now=NOW)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["dedupe_key"], second[0]["dedupe_key"])
        self.assertEqual(first[0]["observer_finding_id"], second[0]["observer_finding_id"])

    def test_improvement_requires_explicit_measurable_duplicate_signal(self) -> None:
        findings = evaluate_snapshot(
            {
                "tasks": [
                    {
                        "task_id": "path-choice",
                        "status": "ACTIVE",
                        "owner": "DISPATCHER",
                        "updated_at": "2026-09-10T19:59:00+00:00",
                        "evidence_refs": ["benchmark:7"],
                        "measurably_duplicative": True,
                        "simpler_existing_path": "existing-read-only-path",
                    }
                ]
            },
            now=NOW,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["class"], "IMPROVEMENT")
        self.assertIn("existing-read-only-path", findings[0]["minimal_next_action"])

    def test_output_contract_has_all_required_fields(self) -> None:
        finding = evaluate_snapshot(
            {"tasks": [{"task_id": "contract", "status": "DONE", "owner": "TEST"}]},
            now=NOW,
        )[0]
        self.assertEqual(
            set(finding),
            {
                "observer_finding_id",
                "class",
                "severity",
                "subject",
                "observed_at",
                "evidence_refs",
                "current_state",
                "why_it_matters",
                "minimal_next_action",
                "owner_or_lane",
                "dedupe_key",
                "status",
            },
        )
        self.assertEqual(finding["status"], "OPEN")

    def test_invalid_stale_window_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_snapshot({}, now=NOW, stale_after_seconds=0)


if __name__ == "__main__":
    unittest.main()
