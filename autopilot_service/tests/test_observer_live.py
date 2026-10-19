from __future__ import annotations

import base64
import copy
import io
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch

from autopilot_app.observer import assess_sources, build_notification_preview, evaluate_snapshot
_spec = importlib.util.spec_from_file_location(
    "run_observer_canary", Path(__file__).resolve().parents[1] / "tools/run_observer_canary.py",
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
REPO = "olegmed1-art/bridge-video-free"
BASE = f"https://api.github.com/repos/{REPO}"
SHA = "a" * 40


def fixture() -> dict:
    """Retained audit facts from main/issue reads on 2026-10-19; no credentials."""
    payloads = {
        "commits/main": {"sha": SHA},
        "issues/620": {"state": "open", "updated_at": "2026-08-26T09:29:32Z"},
        "issues/1157": {"state": "open", "updated_at": "2026-09-11T20:40:12Z"},
    }
    documents = {
        "ops/governance/portfolio.json": {"last_verified_at": "2026-08-30"},
        "ops/autopilot/project-state.json": {"verification": {"verified_at": "2026-09-01"}},
        "ops/governance/governance-state.json": {"reviews": {"first_operational_audit_due": "2026-09-25"}},
    }
    for i, (path, value) in enumerate(documents.items(), 1):
        payloads[f"contents/{path}?ref={SHA}"] = {
            "encoding": "base64", "sha": str(i) * 40,
            "content": base64.b64encode(json.dumps(value).encode()).decode(),
        }
    return payloads


class ObserverLiveTests(unittest.TestCase):
    def receipt(self, payloads=None, now=NOW):
        payloads = payloads if payloads is not None else fixture()

        def get(url, token):
            self.assertEqual(token, "fixture-token")
            self.assertTrue(url.startswith(BASE + "/"))
            value = payloads[url.removeprefix(BASE + "/")]
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)

        with patch.object(runner, "_github_get", side_effect=get) as reads, \
                patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            result = runner.build_receipt(REPO, "fixture-token", now=now)
        self.assertLessEqual(reads.call_count, 6)
        return result

    def test_audit_regression_produces_four_actionable_findings(self):
        receipt = self.receipt()
        states = {s["subject"]: s["state"] for s in receipt["source_assessments"]}
        self.assertEqual(states, {
            "current-main": "CONFIRMED", "governance-portfolio": "STALE",
            "autopilot-project-state": "STALE", "governance-audit-tracker": "BLOCKED",
            "observer-acceptance-tracker": "BLOCKED",
        })
        self.assertEqual(receipt["assessment"], "ATTENTION_REQUIRED")
        self.assertEqual(receipt["finding_count"], 4)
        self.assertEqual(receipt["notification_preview"]["formed_count"], 4)
        self.assertFalse(receipt["notifications_enabled"])
        self.assertFalse(receipt["external_mutation_allowed"])
        self.assertEqual(receipt["notification_quality"], "NOT_ACCEPTED_BY_THIS_OBSERVATION")
        self.assertEqual(receipt["notification_preview"]["delivery_status"], "NOT_ATTEMPTED")
        serialized = json.dumps(receipt)
        self.assertNotIn("/issues/881", serialized)
        self.assertNotIn("/pull/1158", serialized)
        self.assertIn("runtime_services", receipt["source_state"]["unobserved_scopes"])

    def test_completed_tracker_is_not_notification_or_school_readiness(self):
        payloads = fixture()
        for number in (620, 1157):
            payloads[f"issues/{number}"] = {"state": "closed", "state_reason": "completed"}
        receipt = self.receipt(payloads)
        states = {s["subject"]: s["state"] for s in receipt["source_assessments"]}
        self.assertEqual(states["governance-audit-tracker"], "COMPLETED")
        self.assertEqual(states["observer-acceptance-tracker"], "COMPLETED")
        self.assertEqual(receipt["finding_count"], 2)  # old indexes remain stale
        self.assertFalse(receipt["notifications_enabled"])

    def test_cancelled_tracker_is_not_completed(self):
        payloads = fixture()
        payloads["issues/1157"] = {"state": "closed", "state_reason": "not_planned"}
        receipt = self.receipt(payloads)
        self.assertEqual(receipt["source_assessments"][-1]["state"], "CONFIRMED")

    def test_fetch_failure_is_unknown_not_empty_success_and_other_reads_continue(self):
        payloads = fixture()
        payloads["issues/1157"] = OSError("private error details must not be emitted")
        receipt = self.receipt(payloads)
        self.assertEqual(receipt["source_assessments"][-1]["state"], "UNKNOWN")
        self.assertEqual(receipt["source_state"]["collection_status"], "PARTIAL")
        self.assertEqual(receipt["finding_count"], 4)
        self.assertNotIn("private error", json.dumps(receipt))

    def test_main_failure_does_not_fall_back_to_unpinned_file_reads(self):
        payloads = fixture()
        payloads["commits/main"] = OSError("unavailable")
        receipt = self.receipt(payloads)
        self.assertIsNone(receipt["source_state"]["main_sha"])
        self.assertEqual(receipt["finding_count"], 5)
        self.assertEqual(receipt["source_assessments"][0]["state"], "UNKNOWN")

    def test_truncated_http_response_is_unknown_and_remaining_sources_are_observed(self):
        payloads = fixture()
        payloads[f"contents/ops/governance/portfolio.json?ref={SHA}"] = IncompleteRead(b"{", 42)
        receipt = self.receipt(payloads)
        self.assertEqual(receipt["source_assessments"][1]["state"], "UNKNOWN")
        self.assertEqual(receipt["source_assessments"][-1]["state"], "BLOCKED")
        self.assertEqual(receipt["notification_preview"]["formed_count"], 4)
        self.assertIn({"source": f"contents/ops/governance/portfolio.json?ref={SHA}",
                       "error_type": "IncompleteRead"}, receipt["source_state"]["read_errors"])

    def test_malformed_index_fails_closed(self):
        for bad_value in ({}, {"verification": None}, {"verification": {"verified_at": "invalid"}}):
            with self.subTest(value=bad_value):
                payloads = fixture()
                doc = payloads[f"contents/ops/autopilot/project-state.json?ref={SHA}"]
                doc["content"] = base64.b64encode(json.dumps(bad_value).encode()).decode()
                receipt = self.receipt(payloads)
                self.assertEqual(receipt["source_assessments"][2]["state"], "UNKNOWN")

    def test_invalid_audit_due_date_is_unknown(self):
        payloads = fixture()
        doc = payloads[f"contents/ops/governance/governance-state.json?ref={SHA}"]
        doc["content"] = base64.b64encode(b'{"reviews":{"first_operational_audit_due":"bad"}}').decode()
        receipt = self.receipt(payloads)
        self.assertEqual(receipt["source_assessments"][3]["state"], "UNKNOWN")

    def test_closed_tracker_does_not_hide_missing_governance_evidence(self):
        payloads = fixture()
        payloads["issues/620"] = {"state": "closed", "state_reason": "completed"}
        payloads[f"contents/ops/governance/governance-state.json?ref={SHA}"] = OSError("unavailable")
        receipt = self.receipt(payloads)
        self.assertEqual(receipt["source_assessments"][3]["state"], "UNKNOWN")
        self.assertEqual(receipt["assessment"], "ATTENTION_REQUIRED")

    def test_repeated_observation_and_unrelated_main_commit_keep_finding_keys(self):
        first = self.receipt()
        payloads = {key.replace(SHA, "b" * 40): value for key, value in fixture().items()}
        payloads["commits/main"]["sha"] = "b" * 40
        second = self.receipt(payloads, now=NOW + timedelta(hours=1))
        self.assertEqual([f["dedupe_key"] for f in first["findings"]],
                         [f["dedupe_key"] for f in second["findings"]])
        preview = build_notification_preview(second["findings"],
                    seen_dedupe_keys=[f["dedupe_key"] for f in first["findings"]])
        self.assertEqual(preview["formed_count"], 0)
        self.assertEqual(preview["suppressed_count"], 4)

    def test_notification_text_preserves_evidence_time_action_owner_and_dedupes(self):
        findings = self.receipt()["findings"]
        preview = build_notification_preview(findings + findings)
        self.assertEqual(preview["formed_count"], 4)
        self.assertEqual(preview["suppressed_count"], 4)
        for finding, item in zip(findings, preview["items"]):
            self.assertEqual(item["dedupe_key"], finding["dedupe_key"])
            for field in ("subject", "observed_at", "current_state", "minimal_next_action", "owner_or_lane"):
                self.assertIn(finding[field], item["message"])
            for ref in finding["evidence_refs"]:
                self.assertIn(ref, item["message"])

    def test_error_and_loop_notifications_are_formed_without_network(self):
        findings = evaluate_snapshot({"tasks": [
            {"task_id": "no-result", "status": "DONE"},
            {"task_id": "repeat", "status": "OPEN", "repeated_without_new_evidence": True},
        ]}, now=NOW)
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            preview = build_notification_preview(findings)
        self.assertEqual(preview["formed_count"], 2)
        self.assertIn("[P1 ERROR]", preview["items"][0]["message"])
        self.assertIn("[P2 LOOP_DUPLICATION]", preview["items"][1]["message"])

    def test_transport_is_get_only(self):
        response = io.BytesIO(b'{"ok":true}')
        response.status = 200
        with patch.object(runner, "urlopen", return_value=response) as send:
            self.assertEqual(runner._github_get(BASE + "/commits/main", "fixture-token"), {"ok": True})
        self.assertEqual(send.call_args.args[0].get_method(), "GET")
        self.assertIsNone(send.call_args.args[0].data)

    def test_main_writes_receipt_and_preview_to_local_file_only(self):
        receipt = self.receipt()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            with patch.dict("os.environ", {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "fixture-token",
                                          "OBSERVER_CANARY_OUTPUT": str(output)}), \
                    patch.object(runner, "build_receipt", return_value=receipt), patch("builtins.print"):
                self.assertEqual(runner.main(), 0)
            self.assertEqual(json.loads(output.read_text()), receipt)


class SourceAssessmentTests(unittest.TestCase):
    def test_freshness_evidence_and_completion_decision_table(self):
        source = {"subject": "source", "status": "CONFIRMED", "verified_at": NOW.isoformat(),
                  "evidence_refs": ["receipt:1"], "detail": "scope", "minimal_next_action": "reconcile",
                  "owner_or_lane": "TEST"}
        cases = [
            ({}, "CONFIRMED"), ({"status": "BLOCKED"}, "BLOCKED"),
            ({"status": "COMPLETED"}, "COMPLETED"),
            ({"status": "COMPLETED", "verified_at": "2026-09-01"}, "STALE"),
            ({"verified_at": None}, "UNKNOWN"), ({"verified_at": "invalid"}, "UNKNOWN"),
            ({"verified_at": (NOW + timedelta(seconds=1)).isoformat()}, "UNKNOWN"),
            ({"verified_at": (NOW - timedelta(days=1)).isoformat()}, "CONFIRMED"),
            ({"verified_at": (NOW - timedelta(days=1, seconds=1)).isoformat()}, "STALE"),
            ({"evidence_refs": []}, "UNKNOWN"), ({"status": "DONE"}, "UNKNOWN"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.assertEqual(assess_sources([{**source, **changes}], now=NOW)[0]["state"], expected)


if __name__ == "__main__":
    unittest.main()
