"""Stdlib-only generation tests; catalog fixture is not live activation evidence."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID

from bridge_school_api import tournament_shape_adapter as adapter
from bridge_school_api import tournament_shape_exercises as generator
from bridge_school_api.ai_worlds import parse_hand_pbn
from bridge_school_api.tournament_shape_core import PAYLOAD_HASHES, PROFILE, VERSION, SCOPE, digest, evaluate

SCHOOL = UUID("00000000-0000-4000-8000-000000000101")
ROOT = Path(__file__).resolve().parents[1]


def catalog(call="3H"):
    package = json.loads((ROOT / "tools/tournament_pilot/package.json").read_text(encoding="utf-8"))
    item = next(row for row in package["rules"] if row["payload"]["source_rule"]["call"] == call)
    return [{"school_id": SCHOOL, "scope_key": SCOPE, "rule_key": item["rule_key"],
             "rule_id": UUID(int=201), "knowledge_version_id": UUID(int=202),
             "runtime_activation_id": UUID(int=203), "compiled_payload": item["payload"],
             **item["payload"]["catalog"]}]


def context(**changes):
    return {"school_id": SCHOOL, "school_status": "active", "input_status": "COMPLETE",
            "decision_type": "BIDDING", "system_us": PROFILE, "dealer": "N", "seat": "S",
            "auction_json": ["1NT", "PASS"], "cards_played_json": [], "dummy_pbn": None, **changes}


def generate(**changes):
    args = {"context": context(), "call": "3H", "canon_version": VERSION,
            "catalog": catalog(), "seed": 7, "count": 12}
    args.update(changes)
    return generator.generate_shape_exercises(**args)


def oracle(hand, call):
    """Independent explicit reference set, without production helper calls."""
    vector = tuple(map(len, hand.split(".")))
    permitted = {(3, 1, 4, 5), (3, 1, 5, 4)} if call == "3H" else {(1, 3, 4, 5), (1, 3, 5, 4)}
    return vector in permitted


class ShapeGenerationTests(unittest.TestCase):
    def test_single_default_exercise_has_both_classes_and_is_reproducible(self):
        for call in ("3H", "3S"):
            positives = 0
            rows = catalog(call)
            for seed in range(200):
                args = dict(call=call, catalog=rows, count=1, seed=seed)
                batch = generate(**args)
                self.assertEqual(batch.status, "GENERATED")
                self.assertEqual(batch, generate(**args))
                positives += oracle(batch.exercises[0].hand_pbn, call)
            # Fixed seeds make this deterministic; catch a strongly biased mix.
            self.assertTrue(70 <= positives <= 130, (call, positives))

    def test_odd_default_counts_round_both_ways_and_explicit_counts_remain_exact(self):
        for count in (3, 5):
            observed = set()
            for seed in range(32):
                batch = generate(count=count, seed=seed)
                self.assertEqual(batch, generate(count=count, seed=seed))
                observed.add(sum(oracle(x.hand_pbn, "3H") for x in batch.exercises))
            self.assertEqual(observed, {count // 2, count // 2 + 1})
        for count in (1, 3, 6):
            for positive_count in range(count + 1):
                batch = generate(count=count, positive_count=positive_count)
                self.assertEqual(sum(oracle(x.hand_pbn, "3H") for x in batch.exercises), positive_count)
        for count in (2, 6, 100):
            self.assertEqual(generate(count=count), generate(count=count, positive_count=count // 2))

    def test_history_overflow_and_shared_work_overflow_fail_before_parsing(self):
        hand = "AKQ.J.9876.T9876"
        cases = [dict(excluded_hands=[hand] * 10_001, max_attempts=1),
                 dict(excluded_hands=[hand] * 2_001, max_attempts=10_000)]
        for args in cases:
            with self.subTest(args_size=len(args["excluded_hands"])):
                with patch.object(generator, "hand_fingerprint") as fingerprint:
                    with self.assertRaises(generator.ShapeGenerationError):
                        generate(count=1, **args)
                    fingerprint.assert_not_called()

    def test_history_strict_types_and_bounded_strings_reject_before_parsing(self):
        from collections import UserList
        class Coercible:
            def __str__(self):
                raise AssertionError("history must never coerce arbitrary objects")
        class CustomList(list):
            def __len__(self):
                raise AssertionError("history must not call custom container methods")
        hand = "AKQ.J.9876.T9876"
        for history in (UserList([hand]), CustomList([hand]), iter([hand]), {hand},
                        [hand, None], [hand, Coercible()], [hand, b"AKQ.J.9876.T9876"],
                        [hand, " " * 33], [hand, "\u2660"], [hand, ""]):
            with patch.object(generator, "hand_fingerprint") as fingerprint:
                with self.assertRaises(generator.ShapeGenerationError):
                    generate(count=1, excluded_hands=history)
                fingerprint.assert_not_called()
        with self.assertRaisesRegex(generator.ShapeGenerationError, "invalid excluded hand"):
            generate(count=1, excluded_hands=["AAA.J.9876.T9876"])

    def test_history_at_limits_is_complete_and_never_truncated(self):
        first = generate(count=1, positive_count=1, seed=41).exercises[0].hand_pbn
        other = "AKQ.J.9876.T9876"
        self.assertNotEqual(generator.hand_fingerprint(first), generator.hand_fingerprint(other))
        history = [other] * 9_999 + [first]
        with patch.object(generator, "hand_fingerprint", wraps=generator.hand_fingerprint) as fingerprint:
            batch = generate(count=1, positive_count=1, seed=41, excluded_hands=history, max_attempts=2_000)
        self.assertEqual(batch.status, "GENERATED")
        self.assertGreater(batch.attempts, 1)
        self.assertEqual(fingerprint.call_count, len(history) + batch.attempts)
        self.assertFalse({generator.hand_fingerprint(x.hand_pbn) for x in batch.exercises}
                         & {generator.hand_fingerprint(x) for x in (first, other)})
        self.assertEqual(history[-1], first)
        # The joint budget also admits the maximum sampling limit with history.
        self.assertEqual(generate(count=1, excluded_hands=(other,) * 2_000,
                                  max_attempts=10_000).status, "GENERATED")

    def test_pinned_catalog_and_all_1120_shape_evaluations(self):
        for call in ("3H", "3S"):
            rows = catalog(call)
            self.assertEqual(digest(rows[0]["compiled_payload"]), PAYLOAD_HASHES[call])
            total = 0
            for s in range(14):
                for h in range(14-s):
                    for d in range(14-s-h):
                        shape = (s, h, d, 13-s-h-d)
                        hand = ".".join("AKQJT98765432"[:length] for length in shape)
                        expected = oracle(hand, call)
                        self.assertEqual(generator.inspect_shape(hand, call)[0], expected)
                        answer = evaluate(context() | {"hand_pbn": hand}, call, rows)
                        self.assertEqual(answer["status"], "SUPPORTED" if expected else "CONTRADICTED")
                        self.assertIsNone(answer["action"])
                        self.assertIsNone(answer["point_threshold"])
                        total += 1
            self.assertEqual(total, 560)

    def test_reproducible_new_unique_hands_and_both_minor_orders(self):
        for call in ("3H", "3S"):
            for difficulty in ("BASIC", "CLOSE"):
                for seed in range(64):
                    args = dict(call=call, catalog=catalog(call), difficulty=difficulty, seed=seed, count=20)
                    first, second = generate(**args), generate(**args)
                    self.assertEqual(first, second)
                    self.assertEqual(first.status, "GENERATED")
                    fingerprints = {generator.hand_fingerprint(x.hand_pbn) for x in first.exercises}
                    self.assertEqual(len(fingerprints), 20)
                    self.assertEqual(sum(oracle(x.hand_pbn, call) for x in first.exercises), 10)
                    matching_minor_orders = set()
                    for exercise in first.exercises:
                        self.assertEqual(len(set(parse_hand_pbn(exercise.hand_pbn))), 13)
                        if oracle(exercise.hand_pbn, call):
                            matching_minor_orders.add(tuple(map(len, exercise.hand_pbn.split(".")[2:])))
                        else:
                            v = tuple(map(len, exercise.hand_pbn.split(".")))
                            positives = [(3,1,4,5),(3,1,5,4)] if call == "3H" else [(1,3,4,5),(1,3,5,4)]
                            self.assertEqual(min(sum(abs(a-b) for a,b in zip(v,p)) for p in positives), 2)
                    self.assertEqual(matching_minor_orders, {(4,5),(5,4)})
        self.assertNotEqual(generate(seed=1).student_exercises(), generate(seed=2).student_exercises())

    def test_card_reordering_is_not_a_new_hand(self):
        first = generate(seed=22)
        reordered = [".".join(part[::-1] for part in x.hand_pbn.split(".")) for x in first.exercises]
        again = generate(seed=22, excluded_hands=reordered)
        self.assertEqual(again.status, "GENERATED")
        before = {generator.hand_fingerprint(hand) for hand in reordered}
        after = {generator.hand_fingerprint(x.hand_pbn) for x in again.exercises}
        self.assertFalse(before & after)

    def test_only_safe_projection_is_delivered(self):
        required = {"exercise_id", "task", "hand_pbn", "system_profile", "dealer", "seat", "auction",
                    "proposed_call", "question", "response_options", "limitation"}
        for task in generate().student_exercises():
            self.assertEqual(set(task), required)
            self.assertEqual(task["response_options"], ["Да", "Нет"])
            self.assertIn("Сила руки", task["limitation"])
            for forbidden in ("seed", "expected", "positive", "negative", "binding", "singleton", "5431", "FG"):
                self.assertNotIn(forbidden, json.dumps(task, ensure_ascii=False))

    def test_other_profiles_interference_second_round_and_invalid_scope_are_not_errors(self):
        cases = [context(system_us="SCHOOL_L1_DB_V1"), context(auction_json=["1NT", "X"]),
                 context(auction_json=["1NT", "PASS", "2C", "PASS", "2D", "PASS"]),
                 context(seat="E"), context(dealer=""), context(school_status="inactive"),
                 context(cards_played_json=["SA"]), context(dummy_pbn="AKQJ.T98.765.432"),
                 context(decision_type="PLAY"), context(input_status="INCOMPLETE")]
        for candidate in cases:
            result = generate(context=candidate)
            self.assertEqual(result.status, "OUT_OF_SCOPE")
            self.assertEqual(result.exercises, ())
        for changes in ({"call": "3NT"}, {"call": []}, {"canon_version": "other"}):
            self.assertEqual(generate(**changes).status, "OUT_OF_SCOPE")

    def test_all_four_dealers_preserve_responder_seat(self):
        for dealer, seat in zip("NESW", "SWNE"):
            batch = generate(context=context(dealer=dealer, seat=seat))
            self.assertEqual(batch.status, "GENERATED")
            self.assertEqual(batch.student_exercises()[0]["seat"], seat)

    def test_unsatisfiable_constraints_never_fallback_or_partial_batch(self):
        for changes in [dict(suit_length_constraints={"H": [13,13]}),
                        dict(suit_length_constraints={"S": [13,13], "H": [1,1]}),
                        dict(difficulty="CLOSE", target_error="NAMED_MAJOR_NOT_SINGLETON")]:
            batch = generate(**changes)
            self.assertEqual(batch.status, "UNSATISFIABLE_CONSTRAINTS")
            self.assertEqual(batch.student_exercises(), [])

    def test_bounded_exhaustion_does_not_return_partial_set(self):
        first = generate(seed=4, count=4)
        batch = generate(seed=4, count=4, max_attempts=4,
                         excluded_hands=[x.hand_pbn for x in first.exercises])
        self.assertEqual(batch.status, "GENERATION_EXHAUSTED")
        self.assertEqual(batch.exercises, ())
        self.assertEqual(batch.attempts, 4)

    def test_constraints_and_named_error_focus_are_respected(self):
        for target, difficulty in [("NAMED_MAJOR_NOT_SINGLETON", "BASIC"),
                                   ("OTHER_MAJOR_NOT_THREE", "CLOSE"),
                                   ("MINORS_NOT_FIVE_FOUR", "CLOSE")]:
            result = generate(target_error=target, difficulty=difficulty, suit_length_constraints={"D": [3,6]})
            self.assertEqual(result.status, "GENERATED")
            for task in result.exercises:
                s,h,d,c = map(len, task.hand_pbn.split("."))
                self.assertTrue(3 <= d <= 6)
                if not oracle(task.hand_pbn, "3H"):
                    violation = {"NAMED_MAJOR_NOT_SINGLETON": h != 1,
                                 "OTHER_MAJOR_NOT_THREE": s != 3,
                                 "MINORS_NOT_FIVE_FOUR": {d,c} != {4,5}}[target]
                    self.assertTrue(violation)

    def test_malformed_controls_rejected_without_invented_hcp(self):
        for change in [{"seed": True}, {"count": 0}, {"count": 101}, {"positive_count": True},
                       {"positive_count": 13}, {"max_attempts": 10001}, {"difficulty": "unknown"},
                       {"target_error": "weak_hand"}, {"suit_length_constraints": {"HCP": [10,12]}},
                       {"suit_length_constraints": {"H": [1,1], "h": [13,13]}},
                       {"suit_length_constraints": {"H": [True,1]}}, {"suit_length_constraints": []},
                       {"excluded_hands": "AKQJ.T98.765.432"}]:
            with self.subTest(change=change), self.assertRaises(generator.ShapeGenerationError):
                generate(**change)

    def test_missing_duplicate_or_changed_canon_abstains(self):
        candidates = [[], [catalog()[0], catalog()[0]], [{}]]
        for field, value in [("scope_key", "other"), ("method_version", "stale"),
                             ("school_id", UUID(int=999)), ("hand_constraints", {"singleton": "S"})]:
            rows = catalog(); rows[0][field] = value; candidates.append(rows)
        for key, value in [("recommend_supported", True), ("contract", "other")]:
            rows = catalog(); rows[0]["compiled_payload"][key] = value; candidates.append(rows)
        for rows in candidates:
            result = generate(catalog=rows)
            self.assertEqual(result.status, "ABSTAIN")
            self.assertEqual(result.exercises, ())

    def test_independent_evaluator_disagreement_blocks_generation(self):
        def disagrees(position, call, rows):
            result = evaluate(position, call, rows)
            result["status"] = "SUPPORTED" if result["status"] == "CONTRADICTED" else "CONTRADICTED"
            return result
        with patch.object(generator, "evaluate", disagrees):
            batch = generate()
        self.assertEqual((batch.status, batch.reason), ("ABSTAIN", "ASSESSOR_DISAGREEMENT"))
        self.assertEqual(batch.exercises, ())

    def test_negative_feedback_names_all_actual_violations(self):
        for difficulty in ("BASIC", "CLOSE"):
            batch = generate(count=30, positive_count=0, difficulty=difficulty)
            for task in batch.exercises:
                s,h,d,c = map(len, task.hand_pbn.split("."))
                response = task.assess_response(True, catalog=catalog())
                self.assertEqual(response["status"], "INCORRECT")
                self.assertEqual(len(response["violated_conditions"]), int(h != 1)+int(s != 3)+int({d,c}!={4,5}))
                self.assertIn("Сила руки", response["explanation"])
                self.assertEqual(task.assess_response(False, catalog=catalog())["status"], "CORRECT")
                self.assertFalse(response["persisted"])

    def test_feedback_rechecks_binding_and_never_grades_unknown(self):
        task = generate(count=1, positive_count=1).exercises[0]
        self.assertEqual(task.assess_response(True, catalog=catalog())["status"], "CORRECT")
        for rows in ([], catalog()):
            if rows:
                rows[0]["runtime_activation_id"] = UUID(int=999)
            response = task.assess_response(True, catalog=rows)
            self.assertEqual(response["status"], "ABSTAIN")
            self.assertIsNone(response["learner_error"])
        with self.assertRaises(generator.ShapeGenerationError):
            task.assess_response("yes", catalog=catalog())

    def test_invalid_physical_hands_rejected(self):
        for hand in ("AAA.2.2345.23456", "AKQJ.T98.765.43", "AKQJ.T98.765.43Z"):
            with self.assertRaises(ValueError):
                generator.inspect_shape(hand, "3H")

    def test_input_objects_are_not_mutated(self):
        ctx, rows = context(), catalog()
        before = deepcopy((ctx, rows))
        batch = generate(context=ctx, catalog=rows)
        batch.exercises[0].assess_response(True, catalog=rows)
        self.assertEqual((ctx, rows), before)

    def test_persistence_contract_uses_existing_columns_and_private_answer(self):
        task = generate(count=1, positive_count=1).exercises[0]
        prepared = task.exercise_version_fields(catalog=catalog())
        self.assertEqual(prepared["status"], "PREPARED")
        row = prepared["exercise_version"]
        self.assertEqual(set(row), {"prompt", "expected_solution", "rubric", "generated_by", "method_version", "provenance", "status"})
        self.assertEqual(row["prompt"], task.student_view())
        self.assertEqual(row["expected_solution"]["response"], oracle(task.hand_pbn, "3H"))
        self.assertEqual(row["provenance"]["seed"], 7)
        self.assertIsNone(row["provenance"]["database_source_id"])
        self.assertEqual(row["status"], "candidate")
        self.assertFalse(row["rubric"]["hcp_assessed"])
        assessed = task.assessment_fields(False, catalog=catalog())
        self.assertEqual(assessed["exercise_attempt_assessment"]["result"]["status"], "INCORRECT")
        self.assertEqual(assessed["exercise_attempt_assessment"]["assessment_purpose"], "learning")
        self.assertEqual(task.exercise_version_fields(catalog=[])["status"], "ABSTAIN")
        self.assertEqual(task.assessment_fields(True, catalog=[])["status"], "ABSTAIN")

    def test_generated_label_is_not_an_authority(self):
        from dataclasses import replace
        task = generate(count=1, positive_count=1).exercises[0]
        tampered = replace(task, _expected=False)
        self.assertEqual(tampered.assess_response(False, catalog=catalog())["status"], "ABSTAIN")
        self.assertEqual(tampered.exercise_version_fields(catalog=catalog())["status"], "ABSTAIN")


class FakeConnection:
    """SQL adapter contract double, not a database integration test."""
    def __init__(self, *, source_valid=True, active=True, source_id=UUID(int=900)):
        self.queries = []
        self.source_valid, self.active = source_valid, active
        self.source_id = source_id

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        return self

    def fetchone(self):
        return {"status": "active" if self.active else "inactive"}

    def fetchall(self):
        source = catalog()[0]["compiled_payload"]["source_rule"]
        if not self.source_valid:
            return []
        return [{"source_id": self.source_id, "canonical_locator": source["source_url"],
                 "source_locator": {"original_excerpt": source["original_excerpt"],
                                    "teacher_excerpt": source["teacher_excerpt"]}}]


class AdapterContractTests(unittest.TestCase):
    def request(self, connection, **changes):
        public = {k:v for k,v in context().items() if k not in {"school_id", "school_status"}}
        args = dict(school_id=SCHOOL, public_context=public, call="3H", canon_version=VERSION, seed=9)
        args.update(changes)
        return adapter.generate_from_school_catalog(connection, **args)

    def test_verified_database_source_uuid_is_preserved_privately(self):
        expected_id = str(UUID(int=900))
        for source_id in (UUID(int=900), expected_id):
            with patch.object(adapter, "_read_catalog", return_value=catalog()):
                task = self.request(FakeConnection(source_id=source_id)).exercises[0]
                self.assertEqual(task._database_source_id, expected_id)
                row = task.exercise_version_fields(catalog=catalog())["exercise_version"]
                self.assertEqual(row["provenance"]["database_source_id"], expected_id)
                self.assertNotIn("database_source_id", task.student_view())
                result = adapter.assess_from_school_catalog(FakeConnection(source_id=source_id),
                            school_id=SCHOOL, exercise=task, response=True)
                self.assertEqual(result["database_source_id"], expected_id)

    def test_unknown_database_source_uuid_is_not_invented(self):
        for source_id in (None, "unknown", 900):
            with patch.object(adapter, "_read_catalog", return_value=catalog()):
                task = self.request(FakeConnection(source_id=source_id)).exercises[0]
                self.assertIsNone(task._database_source_id)
                row = task.exercise_version_fields(catalog=catalog())["exercise_version"]
                self.assertIsNone(row["provenance"]["database_source_id"])

    def test_database_source_replacement_or_loss_cannot_grade_a_learner(self):
        with patch.object(adapter, "_read_catalog", return_value=catalog()):
            task = self.request(FakeConnection()).exercises[0]
            for source_id in (UUID(int=901), None):
                answer = adapter.assess_from_school_catalog(FakeConnection(source_id=source_id),
                            school_id=SCHOOL, exercise=task, response=True)
                self.assertEqual(answer["status"], "ABSTAIN")
                self.assertEqual(answer["reason"], "DATABASE_SOURCE_CHANGED")
                self.assertIsNone(answer["learner_error"])

    def test_readonly_authoritative_adapter_and_source_recheck(self):
        connection = FakeConnection()
        with patch.object(adapter, "_read_catalog", return_value=catalog()) as reader:
            batch = self.request(connection)
            self.assertEqual(batch.status, "GENERATED")
            reader.assert_called_once_with(connection, SCHOOL)
            result = adapter.assess_from_school_catalog(connection, school_id=SCHOOL,
                        exercise=batch.exercises[0], response=True)
            self.assertIn(result["status"], {"CORRECT", "INCORRECT"})
        self.assertEqual(connection.queries[0][0], "SET TRANSACTION READ ONLY")
        self.assertTrue(all(sql.lstrip().startswith(("SELECT", "SET TRANSACTION READ ONLY")) for sql,_ in connection.queries))

    def test_missing_source_cannot_produce_or_grade_exercises(self):
        with patch.object(adapter, "_read_catalog", return_value=catalog()):
            bad = self.request(FakeConnection(source_valid=False))
            self.assertEqual((bad.status, bad.reason, bad.exercises), ("ABSTAIN", "SOURCE_BINDING_MISMATCH", ()))
            task = self.request(FakeConnection()).exercises[0]
            answer = adapter.assess_from_school_catalog(FakeConnection(source_valid=False),
                        school_id=SCHOOL, exercise=task, response=True)
            self.assertEqual(answer["status"], "ABSTAIN")
            self.assertIsNone(answer["learner_error"])

    def test_inactive_school_and_caller_catalog_override_are_rejected(self):
        with patch.object(adapter, "_read_catalog", side_effect=AssertionError("must not read catalog")):
            self.assertEqual(self.request(FakeConnection(active=False)).reason, "SCHOOL_NOT_ACTIVE")
            self.assertEqual(self.request(FakeConnection(), catalog=catalog()).reason, "UNSUPPORTED_GENERATION_INPUT")

    def test_database_failure_has_no_fixture_fallback(self):
        with patch.object(adapter, "_read_catalog", side_effect=ConnectionError("offline")):
            with self.assertRaises(ConnectionError):
                self.request(FakeConnection())

    def test_cross_school_cannot_be_graded(self):
        task = generate().exercises[0]
        connection = FakeConnection()
        answer = adapter.assess_from_school_catalog(connection, school_id=UUID(int=999), exercise=task, response=True)
        self.assertEqual(answer["status"], "ABSTAIN")
        self.assertIsNone(answer["learner_error"])
        self.assertFalse(connection.queries)

    def test_targets_require_current_executable_active_catalog_and_bound_source(self):
        for rows, expected in [(catalog("3H"), ["3H"]),
                               (catalog("3H") + catalog("3S"), ["3H", "3S"]), ([], [])]:
            with patch.object(adapter, "_read_catalog", return_value=rows):
                targets = adapter.available_targets_from_school_catalog(FakeConnection(), school_id=SCHOOL)
                self.assertEqual([t["proposed_call"] for t in targets], expected)
                self.assertFalse(any("hand_pbn" in t for t in targets))
        stale = catalog(); stale[0]["method_version"] = "expired"
        with patch.object(adapter, "_read_catalog", return_value=stale):
            self.assertEqual(adapter.available_targets_from_school_catalog(FakeConnection(), school_id=SCHOOL), [])
        with patch.object(adapter, "_read_catalog", return_value=catalog()):
            self.assertEqual(adapter.available_targets_from_school_catalog(FakeConnection(source_valid=False), school_id=SCHOOL), [])

    def test_revocation_between_generation_and_submission_abstains(self):
        with patch.object(adapter, "_read_catalog", side_effect=[catalog(), []]):
            task = self.request(FakeConnection()).exercises[0]
            answer = adapter.assess_from_school_catalog(FakeConnection(), school_id=SCHOOL, exercise=task, response=False)
            self.assertEqual(answer["status"], "ABSTAIN")
            self.assertIsNone(answer["learner_error"])


if __name__ == "__main__":
    unittest.main()
