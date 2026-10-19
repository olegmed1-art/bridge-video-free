"""Integration specifications for the prepared-reader -> real teacher seam.

All native tests are NOT_RUN in the currently blocked Windows environment.
The previous adapter's 40 tests remain separate, unmodified dependencies.
"""
from copy import deepcopy
from io import BytesIO
from unittest.mock import Mock, patch
import unittest

from bridge_school_api import teacher_entry_blocking_adapter as adapter
from bridge_school_api import teacher_entry_blocking_reader_path as path


def observation(*, entries="NONE", concrete=False, known=False):
    return {
        "schema": "teacher-symbolic-entry-v1", "profile": adapter.SYSTEM_VERSION,
        "role": "declarer", "phase": "suit_timing", "trump": "NT",
        "declarer": "N", "dummy": "S", "dummy_visible": True,
        "target_suit": "S" if known else None,
        "hand_a": {"seat": "N", "honors": ["A"], "length": 3,
                   "low_cards": ["3", "2"] if known else None},
        "hand_b": {"seat": "S", "honors": ["Q", "J"], "length": 4,
                   "low_cards": ["5", "4"] if known else None},
        "side_entries_to_a": entries, "leader": "N" if known else None,
        "current_trick": [] if known else None,
        "request": "CONCRETE_CARD" if concrete else "SYMBOLIC_PLAN",
    }


class TeacherPreparedReaderPathTests(unittest.TestCase):
    def setUp(self):
        # Normal native execution would read the real pinned prepared fixture.
        self.reader = path.load_prepared_test_reader(test_only=True)

    def decide(self, o=None, reader=None):
        return path.decide_from_reader(
            observation() if o is None else o,
            self.reader.read_eligible_skills if reader is None else reader,
            test_only=True,
        )

    def test_real_file_reader_returns_only_typed_skill_records(self):
        records = self.reader.read_eligible_skills(path._request())
        self.assertEqual({r["content"]["skill_contract"]["skill_id"] for r in records},
                         {"SKILL-0003", "SKILL-0004"})
        self.assertTrue(all(set(r) == adapter.RECORD_FIELDS for r in records))
        self.assertTrue(all(set(r["content"]) == {"skill_contract"} for r in records))
        self.assertTrue(all(r["dependency_generation"] == 1 for r in records))
        self.assertTrue(all("prepared_case_scope" not in r for r in records))

    def test_ex0002_file_reader_to_joint_decision_has_conditional_low_preference(self):
        result = self.decide()
        self.assertEqual(result["status"], "CONDITIONAL_RECOMMENDATION")
        self.assertEqual(result["selected_plan"], "LOW_TO_QJ")
        self.assertEqual(len(result["alternatives"]), 2)
        self.assertIn("SIDE_ENTRIES_TO_A_NONE", result["conditional_premises"])
        self.assertIsNone(result["success_probability"])

    def test_ex0001_file_reader_to_joint_decision_does_not_import_ace_answer(self):
        result = self.decide(observation(entries="SUFFICIENT"))
        self.assertEqual(result["status"], "ADMISSIBLE_ALTERNATIVES")
        self.assertIsNone(result["selected_plan"])
        self.assertIsNone(result["immediate_action"])
        self.assertEqual(len(result["alternatives"]), 2)

    def test_partial_concrete_position_reports_missing_fields_through_reader(self):
        result = self.decide(observation(concrete=True))
        self.assertEqual(result["status"], "NEEDS_CONTEXT")
        self.assertEqual(set(result["missing_context"]),
                         {"target_suit", "hand_a.low_cards", "hand_b.low_cards",
                          "leader", "current_trick"})
        self.assertIsNone(result["immediate_action"])

    def test_concrete_action_fetches_reader_again_before_real_recomputation(self):
        o = observation(concrete=True, known=True)
        fetch = Mock(wraps=self.reader.read_eligible_skills)
        decision = self.decide(o, fetch)
        self.assertEqual(fetch.call_count, 1)
        action = path.next_action_from_reader(decision, o, fetch, test_only=True)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(action["status"], "ACTION")
        self.assertEqual(action["action"]["card"], "S2")
        self.assertEqual(action["action"]["seat"], "N")

    def test_revoke_either_skill_excludes_action_with_fresh_generation(self):
        for skill in ("SKILL-0003", "SKILL-0004"):
            with self.subTest(skill=skill):
                reader = path.load_prepared_test_reader(test_only=True)
                o = observation(concrete=True, known=True)
                decision = self.decide(o, reader.read_eligible_skills)
                self.assertEqual(reader.revoke(skill), 2)
                records = reader.read_eligible_skills(path._request())
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["dependency_generation"], 2)
                action = path.next_action_from_reader(
                    decision, o, reader.read_eligible_skills, test_only=True)
                self.assertEqual(action["reason"], "MISSING_ELIGIBLE_SKILL")
                self.assertIsNone(action["action"])

    def test_revoke_both_skills_leaves_no_fallback_or_revive(self):
        o = observation(concrete=True, known=True)
        decision = self.decide(o)
        self.reader.revoke("SKILL-0003")
        self.reader.revoke("SKILL-0004")
        self.assertEqual(self.reader.dependency_generation, 3)
        self.assertEqual(self.reader.read_eligible_skills(path._request()), [])
        self.assertEqual(self.reader.revoke("SKILL-0004"), 3)
        action = path.next_action_from_reader(
            decision, o, self.reader.read_eligible_skills, test_only=True)
        self.assertEqual(action["reason"], "MISSING_ELIGIBLE_SKILL")
        self.assertIsNone(action["action"])

    def test_fresh_external_reader_generation_invalidates_old_binding(self):
        o = observation(concrete=True, known=True)
        decision = self.decide(o)
        def next_generation(request):
            records = self.reader.read_eligible_skills(request)
            for record in records:
                record["dependency_generation"] = 2
            return records
        action = path.next_action_from_reader(
            decision, o, next_generation, test_only=True)
        self.assertEqual(action["reason"], "STALE_DECISION_RECOMPUTE")
        self.assertIsNone(action["action"])

    def test_reader_failure_cannot_fall_back_to_old_card_or_echo_service_text(self):
        o = observation(concrete=True, known=True)
        decision = self.decide(o)
        broken = Mock(side_effect=RuntimeError("private-error-sentinel"))
        action = path.next_action_from_reader(decision, o, broken, test_only=True)
        self.assertEqual(action["reason"], "READER_FETCH_FAILED")
        self.assertIsNone(action["action"])
        self.assertNotIn("private-error-sentinel", str(action))
        result = self.decide(o, broken)
        self.assertEqual(result["status"], "ABSTAIN")

    def test_raw_gated_kb_metadata_cannot_be_promoted_to_eligible_records(self):
        metadata = {
            "contract_version": "CANON_KB_READ_V1",
            "authority_lane": "SOURCE",
            "items": self.reader.read_eligible_skills(path._request()),
            "candidate_activation_performed": False,
        }
        result = self.decide(reader=lambda request: metadata)
        self.assertEqual(result["reason"], "BOUNDED_ELIGIBLE_SHORTLIST_REQUIRED")
        self.assertIsNone(result["selected_plan"])
        self.assertEqual(result["trace"], [])

    def test_hidden_and_answer_payload_is_rejected_before_reader_call(self):
        for key in ("exercise_id", "answer_key", "opponent_cards", "full_deal"):
            with self.subTest(key=key):
                o = observation()
                o[key] = "private-input-sentinel"
                fetch = Mock()
                result = self.decide(o, fetch)
                self.assertEqual(result["reason"], "OBSERVATION_FIELDS")
                fetch.assert_not_called()
                self.assertNotIn("private-input-sentinel", str(result))

    def test_reader_request_is_bounded_and_excludes_visible_hand_or_exercise(self):
        fetch = Mock(wraps=self.reader.read_eligible_skills)
        self.decide(observation(concrete=True, known=True), fetch)
        request = fetch.call_args.args[0]
        self.assertEqual(set(request), {
            "system_profile", "skill_ids", "role", "phase", "trump",
            "task_class", "adapter_ref",
        })
        self.assertEqual(request["skill_ids"], ["SKILL-0003", "SKILL-0004"])
        self.assertNotIn("S2", str(request))
        self.assertNotIn("exercise_id", request)
        self.assertNotIn("hand_a", request)

    def test_observation_change_during_fetch_excludes_decision(self):
        o = observation(known=True)
        def changing_reader(request):
            o["leader"] = "S"
            return self.reader.read_eligible_skills(request)
        result = self.decide(o, changing_reader)
        self.assertEqual(result["status"], "NEEDS_CONTEXT")
        self.assertEqual(result["reason"], "OBSERVATION_CHANGED_DURING_READ")
        self.assertEqual(result["missing_context"], ["observation"])
        self.assertIsNone(result["immediate_action"])

    def test_observation_change_during_card_fetch_excludes_old_action(self):
        o = observation(concrete=True, known=True)
        decision = self.decide(o)
        def changing_reader(request):
            o["current_trick"] = [{"seat": "N", "card": "S2"}]
            return self.reader.read_eligible_skills(request)
        action = path.next_action_from_reader(
            decision, o, changing_reader, test_only=True)
        self.assertEqual(action["reason"], "OBSERVATION_CHANGED_DURING_READ")
        self.assertIsNone(action["action"])

    def test_valid_entry_context_drift_excludes_decision_and_card(self):
        for card_request in (False, True):
            with self.subTest(card_request=card_request):
                o = observation(concrete=True, known=True)
                decision = self.decide(o)
                def changed_context(request):
                    o["side_entries_to_a"] = "SUFFICIENT"
                    return self.reader.read_eligible_skills(request)
                if card_request:
                    result = path.next_action_from_reader(
                        decision, o, changed_context, test_only=True)
                    self.assertIsNone(result["action"])
                else:
                    result = self.decide(o, changed_context)
                    self.assertIsNone(result["immediate_action"])
                self.assertEqual(result["status"], "NEEDS_CONTEXT")
                self.assertEqual(result["reason"], "OBSERVATION_CHANGED_DURING_READ")
                self.assertEqual(result["missing_context"], ["observation"])

    def test_returned_record_mutation_does_not_modify_reader_or_input(self):
        records = self.reader.read_eligible_skills(path._request())
        records[0]["version_status"] = "retired"
        fresh = self.reader.read_eligible_skills(path._request())
        self.assertTrue(all(r["version_status"] == "active" for r in fresh))
        o = observation()
        before = deepcopy(o)
        self.decide(o)
        self.assertEqual(o, before)

    def test_missing_reader_fails_closed(self):
        result = path.decide_from_reader(observation(), None, test_only=True)
        self.assertEqual(result["reason"], "ELIGIBLE_READER_REQUIRED")
        self.assertIsNone(result["selected_plan"])

    def test_default_opt_in_guards_reader_and_fixed_file_before_io(self):
        fetch = Mock()
        result = path.decide_from_reader(observation(), fetch)
        self.assertEqual(result["reason"], "TEST_ONLY_OPT_IN_REQUIRED")
        action = path.next_action_from_reader({}, observation(), fetch)
        self.assertEqual(action["reason"], "TEST_ONLY_OPT_IN_REQUIRED")
        fetch.assert_not_called()
        with patch.object(path, "REGISTRY_PATH") as filename:
            with self.assertRaisesRegex(adapter.OutsideScope, "TEST_ONLY_OPT_IN_REQUIRED"):
                path.load_prepared_test_reader()
            filename.open.assert_not_called()

    def test_corrupt_prepared_file_is_not_read_as_ready_registry(self):
        with patch.object(path, "REGISTRY_PATH") as filename:
            filename.open.return_value = BytesIO(b'{"answer_key":"synthetic-sentinel"}')
            with self.assertRaisesRegex(adapter.OutsideScope, "PREPARED_REGISTRY_PIN"):
                path.load_prepared_test_reader(test_only=True)

    def test_oversized_catalog_is_rejected_without_whole_registry_read(self):
        with patch.object(path, "REGISTRY_PATH") as filename:
            filename.open.return_value = BytesIO(b"x" * (path.MAX_REGISTRY_BYTES + 2))
            with self.assertRaisesRegex(adapter.OutsideScope, "PREPARED_REGISTRY_SIZE"):
                path.load_prepared_test_reader(test_only=True)

    def test_prepared_reader_cannot_be_labelled_as_live_uuid_records(self):
        records = self.reader.read_eligible_skills(path._request())
        records[0]["item_id"] = "00000000-0000-0000-0000-000000000003"
        with self.assertRaisesRegex(adapter.OutsideScope, "ONLY_SYNTHETIC_PREPARED_RECORDS"):
            path.PreparedTestReader(records, test_only=True)

    def test_unknown_skill_or_widened_reader_scope_is_rejected(self):
        with self.assertRaisesRegex(adapter.OutsideScope, "SKILL_SCOPE"):
            self.reader.revoke("SKILL-0099")
        request = path._request()
        request["system_profile"] = "SCHOOL_TOURNAMENT_CURRENT_V1"
        with self.assertRaisesRegex(adapter.OutsideScope, "READER_REQUEST_SCOPE"):
            self.reader.read_eligible_skills(request)

    def test_flow_uses_existing_joint_decision_and_l1_handlers_without_mock_answers(self):
        real_decide, real_evaluate = adapter.decide, adapter.evaluate_l1
        with patch.object(adapter, "decide", wraps=real_decide) as decision_call:
            with patch.object(adapter, "evaluate_l1", wraps=real_evaluate) as l1_call:
                result = self.decide()
        self.assertEqual(decision_call.call_count, 1)
        self.assertEqual(l1_call.call_count, 2)
        self.assertEqual(result["selected_plan"], "LOW_TO_QJ")
        self.assertEqual(result["explanation"], adapter.render_explanation(result["trace"]))


if __name__ == "__main__":
    unittest.main()
