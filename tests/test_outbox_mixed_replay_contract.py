"""Cross-layer mixed-batch replay contract, reusing the offline guard harness."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

import test_outbox_publisher as guard_test


source = Path(__file__).resolve().parents[1] / "database/outbox_publication_decision.py"
spec = importlib.util.spec_from_file_location("mixed_replay_decision", source)
decision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decision)


class MixedReplayContractTests(unittest.TestCase):
    def test_prepositioned_unapproved_event_cannot_be_replayed_by_allowed_summary(self):
        for payload, error in (
            ({"publication_authorization_status": "unknown"}, "UNKNOWN"),
            (guard_test.BLOCKED, "BLOCKED"),
        ):
            with self.subTest(error=error):
                harness = guard_test.OutboxPublisherTests("test_explicit_allowed_event_keeps_success_contract")
                harness.setUp()
                try:
                    harness.connection.seed(1, {"publication_authorization_status": "allowed"})
                    harness.connection.seed(2, payload, position=17)
                    before = harness.connection.snapshot()
                    for _ in range(2):
                        with self.assertRaisesRegex(
                            RuntimeError, f"^OUTBOX_PUBLICATION_AUTHORIZATION_{error}$"
                        ):
                            decision.publish_if_authorized(
                                "offline-test-only",
                                {"changeset_id": str(guard_test.CHANGESET),
                                 "publication_authorization_status": "allowed"},
                                harness.publisher.publish_changeset_outbox,
                            )
                        self.assertEqual(harness.connection.publication_calls, [])
                        self.assertEqual(harness.connection.snapshot(), before)
                finally:
                    harness.doCleanups()

    def test_fully_allowed_mixed_position_batch_replays_same_positions(self):
        harness = guard_test.OutboxPublisherTests("test_explicit_allowed_event_keeps_success_contract")
        harness.setUp()
        try:
            first_key = harness.connection.seed(
                1, {"publication_authorization_status": "allowed"}, position=17
            )
            second_key = harness.connection.seed(
                2, {"publication_authorization_status": "allowed"}
            )
            results = [decision.publish_if_authorized(
                "offline-test-only",
                {"changeset_id": str(guard_test.CHANGESET),
                 "publication_authorization_status": "allowed"},
                harness.publisher.publish_changeset_outbox,
            ) for _ in range(2)]
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0]["publication_state"], "PUBLISHED")
            self.assertEqual(results[0]["event_positions"], [17, 18])
            self.assertEqual(harness.connection.publication_calls,
                             [first_key, second_key, first_key, second_key])
        finally:
            harness.doCleanups()


if __name__ == "__main__":
    unittest.main()
