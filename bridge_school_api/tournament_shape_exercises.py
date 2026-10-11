"""Generate new learner hands for the pinned tournament shape meaning only.

Pure, bounded generation. Catalog rows must come from the authoritative adapter
for live use; an offline fixture never establishes live canon activation.
Only student_view() is an exercise delivery contract. The batch and exercise
objects contain private generation/assessment evidence and stay server-side.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from itertools import product
import random
from typing import Any, Mapping, Sequence

from .ai_worlds import RANKS, SUITS, WorldConstraints, WorldGenerationError, parse_hand_pbn
from .tournament_shape_core import PROFILE, VERSION, evaluate

GENERATOR_VERSION = "tournament-shape-exercises-v2"
MAX_EXCLUDED_HANDS = 10_000
MAX_HISTORY_HAND_CHARS = 32
# Reserve history parsing and candidate sampling together before either starts.
# Each unit handles one bounded hand; shape enumeration is fixed at 14**3.
MAX_HAND_WORK_UNITS = 12_000
LIMITATION = "Оценивается только соответствие формы руки смыслу соглашения. Сила руки и лучший выбор заявки здесь не оцениваются."
_CONTEXT_FIELDS = {
    "school_id", "school_status", "input_status", "decision_type", "system_us",
    "dealer", "seat", "auction_json", "cards_played_json", "dummy_pbn",
}
_SEATS = "NESW"
_SUIT_NAMES = {"S": "пик", "H": "червей", "D": "бубен", "C": "треф"}
_ERROR_TARGETS = {"ANY", "NAMED_MAJOR_NOT_SINGLETON", "OTHER_MAJOR_NOT_THREE", "MINORS_NOT_FIVE_FOUR"}


class ShapeGenerationError(ValueError):
    """Malformed generation input, never evidence of a learner error."""


@dataclass(frozen=True)
class ShapeExercise:
    """Private exercise plus an explicit, answer-free learner projection."""

    exercise_id: str
    hand_pbn: str
    proposed_call: str
    _context: dict[str, Any] = field(repr=False)
    _expected: bool = field(repr=False)
    _violations: tuple[str, ...] = field(repr=False)
    _binding: dict[str, Any] = field(repr=False)
    _seed: int = field(repr=False)
    _difficulty: str = field(repr=False)
    _target_error: str = field(repr=False)
    _database_source_id: str | None = field(default=None, repr=False)

    def student_view(self) -> dict[str, Any]:
        return {
            "exercise_id": self.exercise_id,
            "task": "SHAPE_MEANING_ONLY",
            "hand_pbn": self.hand_pbn,
            "system_profile": self._context["system_us"],
            "dealer": self._context["dealer"],
            "seat": self._context["seat"],
            "auction": list(self._context["auction_json"]),
            "proposed_call": self.proposed_call,
            "question": "Соответствует ли заявка форме руки, предусмотренной соглашением?",
            "response_options": ["Да", "Нет"],
            "limitation": LIMITATION,
        }

    def assess_response(self, response: bool, *, catalog: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Recheck canon before feedback; caller supplies freshly read catalog.

        This is an assessment value for the existing exercise-attempt flow. It
        does not write an attempt, a profile, or mastery, and does not diagnose
        the cause of an incorrect response from the response alone.
        """
        if type(response) is not bool:
            raise ShapeGenerationError("response must be a boolean")
        result = _assessment(self._context, self.hand_pbn, self.proposed_call, catalog)
        if result.get("status") not in {"SUPPORTED", "CONTRADICTED"}:
            return {"status": "ABSTAIN", "learner_error": None, "reason": result.get("reason")}
        if result["source_bindings"][0] != self._binding:
            return {"status": "ABSTAIN", "learner_error": None, "reason": "CANON_BINDING_CHANGED"}
        expected, violations = inspect_shape(self.hand_pbn, self.proposed_call)
        if expected != self._expected or (result["status"] == "SUPPORTED") != expected:
            return {"status": "ABSTAIN", "learner_error": None, "reason": "ASSESSOR_DISAGREEMENT"}
        if expected:
            explanation = "В названном мажоре синглет, в другом мажоре три карты, в минорах пять и четыре карты."
        else:
            explanation = " ".join(violations)
        return {
            "status": "CORRECT" if response == expected else "INCORRECT",
            "learner_error": response != expected,
            "expected_response": expected,
            "explanation": explanation + " " + LIMITATION,
            "violated_conditions": list(violations),
            "assessment_scope": "shape_meaning_only",
            "source_binding": deepcopy(self._binding),
            "database_source_id": self._database_source_id,
            "persisted": False,
        }

    def exercise_version_fields(self, *, catalog: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Prepare existing exercise_version columns, never write or publish.

        expected_solution, rubric and provenance are private. A delivery layer
        must project prompt only. Caller supplies exercise_id/version_no when
        inserting through the existing authorized persistence path.
        """
        expected, _ = inspect_shape(self.hand_pbn, self.proposed_call)
        checked = self.assess_response(expected, catalog=catalog)
        if checked["status"] != "CORRECT":
            return {"status": "ABSTAIN", "reason": checked.get("reason", "ASSESSMENT_UNAVAILABLE")}
        return {
            "status": "PREPARED",
            "exercise_version": {
                "prompt": self.student_view(),
                "expected_solution": {"response": expected, "explanation": checked["explanation"]},
                "rubric": {"assessment_scope": "shape_meaning_only", "response_type": "boolean",
                           "conditions": ["named_major_singleton", "other_major_three", "minors_five_four"],
                           "hcp_assessed": False, "best_call_assessed": False},
                "generated_by": GENERATOR_VERSION,
                "method_version": VERSION,
                "provenance": {"derived": True, "seed": self._seed, "difficulty": self._difficulty,
                               "requested_error_focus": self._target_error,
                               "hand_fingerprint": hand_fingerprint(self.hand_pbn),
                               "database_source_id": self._database_source_id,
                               "canon_binding": deepcopy(self._binding)},
                "status": "candidate",
            },
        }

    def assessment_fields(self, response: bool, *, catalog: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Prepare append-only exercise_attempt_assessment columns, without I/O.

        The caller retains response/stated_reasoning in exercise_attempt and
        supplies the attempt foreign key. Unknown scope/canon is not an error.
        """
        result = self.assess_response(response, catalog=catalog)
        if result["status"] == "ABSTAIN":
            return result
        return {"status": "PREPARED", "exercise_attempt_assessment": {
            "authority_class": "ai", "assessment_purpose": "learning",
            "method_version": GENERATOR_VERSION, "result": result,
        }}


@dataclass(frozen=True)
class ShapeBatch:
    status: str
    reason: str | None = None
    exercises: tuple[ShapeExercise, ...] = ()
    generator_version: str = GENERATOR_VERSION
    attempts: int = 0

    def student_exercises(self) -> list[dict[str, Any]]:
        return [exercise.student_view() for exercise in self.exercises]


def hand_fingerprint(hand_pbn: str) -> str:
    """Card-set identity, independent of rank display order, call or seed."""
    cards = parse_hand_pbn(hand_pbn)
    return sha256(" ".join(sorted(cards)).encode("ascii")).hexdigest()


def inspect_shape(hand_pbn: str, call: str) -> tuple[bool, tuple[str, ...]]:
    """Independently check all named conditions from actual unique cards.

    The generator selects length vectors; this checker parses physical cards.
    The existing canonical evaluator is an additional independent check.
    """
    if call not in {"3H", "3S"}:
        raise ShapeGenerationError("unsupported proposed call")
    cards = parse_hand_pbn(hand_pbn)
    lengths = {suit: sum(card.startswith(suit) for card in cards) for suit in SUITS}
    named = call[-1]
    other = "S" if named == "H" else "H"
    violations = []
    if lengths[named] != 1:
        violations.append(f"В названном мажоре ({_SUIT_NAMES[named]}) {lengths[named]} карт; требуется синглет.")
    if lengths[other] != 3:
        violations.append(f"В другом мажоре ({_SUIT_NAMES[other]}) {lengths[other]} карт; требуется три.")
    if {lengths["D"], lengths["C"]} != {4, 5}:
        violations.append(f"В минорах {lengths['D']} бубен и {lengths['C']} треф; требуется 5–4 в любом порядке.")
    return not violations, tuple(violations)


def _in_scope(context: Mapping[str, Any], call: str, canon_version: str) -> bool:
    if not isinstance(context, Mapping) or set(context) != _CONTEXT_FIELDS:
        return False
    dealer, seat = context["dealer"], context["seat"]
    return (
        isinstance(call, str) and call in {"3H", "3S"} and canon_version == VERSION
        and bool(context["school_id"]) and context["school_status"] == "active"
        and context["system_us"] == PROFILE and context["decision_type"] == "BIDDING"
        and context["input_status"] == "COMPLETE"
        and dealer in tuple(_SEATS) and seat in tuple(_SEATS)
        and seat == _SEATS[(_SEATS.index(dealer) + 2) % 4]
        and context["auction_json"] == ["1NT", "PASS"]
        and context["cards_played_json"] == [] and context["dummy_pbn"] in (None, "")
    )


def _assessment(context, hand, call, catalog) -> dict[str, Any]:
    try:
        result = evaluate(dict(context, hand_pbn=hand), call, catalog)
        if result.get("status") in {"SUPPORTED", "CONTRADICTED"} and len(result.get("source_bindings", [])) != 1:
            return {"status": "ABSTAIN", "reason": "NO_UNIQUE_CANON_BINDING"}
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        return {"status": "ABSTAIN", "reason": "INVALID_CANON_CATALOG"}


def _length_pools(call: str, difficulty: str, constraints: Mapping[str, Any] | None, target_error: str):
    try:
        rules = WorldConstraints.parse({"seats": {"N": {"suits": constraints or {}}}})
    except WorldGenerationError as exc:
        raise ShapeGenerationError(str(exc)) from exc
    ranges = rules.seats["N"].suits
    named_index = SUITS.index(call[-1])
    allowed = []
    for minors in ((4, 5), (5, 4)):
        majors = (3, 1) if call == "3H" else (1, 3)
        allowed.append((*majors, *minors))
    positive, negative = [], []
    for s, h, d in product(range(14), repeat=3):
        c = 13 - s - h - d
        if c < 0:
            continue
        shape = (s, h, d, c)
        if any(not interval.contains(shape[SUITS.index(suit)]) for suit, interval in ranges.items()):
            continue
        if shape in allowed:
            positive.append(shape)
        elif min(sum(abs(a-b) for a, b in zip(shape, target)) for target in allowed) == 2:
            # Both levels are nearby shapes (one card changes suit length).
            # CLOSE preserves the obvious singleton cue, requiring a full check.
            singleton_matches = shape[named_index] == 1
            other_index = 1 - named_index
            target_matches = {
                "ANY": True,
                "NAMED_MAJOR_NOT_SINGLETON": not singleton_matches,
                "OTHER_MAJOR_NOT_THREE": shape[other_index] != 3,
                "MINORS_NOT_FIVE_FOUR": set(shape[2:]) != {4, 5},
            }[target_error]
            if singleton_matches == (difficulty == "CLOSE") and target_matches:
                negative.append(shape)
    return positive, negative


def _excluded_fingerprints(excluded_hands: Sequence[str], sampling_limit: int) -> set[str]:
    """Bound history and total hand work without dropping any exclusions.

    Only concrete built-in containers/strings are accepted: custom sequences or
    coercions could do unbounded work before our length checks. Validation is
    a bounded first pass, so a malformed tail cannot trigger partial parsing.
    These are deterministic volume limits, not a wall-clock latency guarantee.
    """
    if type(excluded_hands) not in (list, tuple):
        raise ShapeGenerationError("excluded_hands must be a list or tuple of PBN strings")
    if len(excluded_hands) > MAX_EXCLUDED_HANDS:
        raise ShapeGenerationError(f"excluded_hands exceeds {MAX_EXCLUDED_HANDS} hands")
    if len(excluded_hands) + sampling_limit > MAX_HAND_WORK_UNITS:
        raise ShapeGenerationError(f"history plus max_attempts exceeds {MAX_HAND_WORK_UNITS} hand work units")
    for hand in excluded_hands:
        if type(hand) is not str or not 1 <= len(hand) <= MAX_HISTORY_HAND_CHARS or not hand.isascii():
            raise ShapeGenerationError(f"history hands must be ASCII PBN strings of 1..{MAX_HISTORY_HAND_CHARS} characters")
    try:
        return {hand_fingerprint(hand) for hand in excluded_hands}
    except WorldGenerationError as exc:
        raise ShapeGenerationError(f"invalid excluded hand: {exc}") from exc


def generate_shape_exercises(
    *, context: Mapping[str, Any], call: str, canon_version: str,
    catalog: Sequence[Mapping[str, Any]], seed: int, count: int = 6,
    positive_count: int | None = None, difficulty: str = "CLOSE",
    target_error: str = "ANY",
    suit_length_constraints: Mapping[str, Any] | None = None,
    excluded_hands: Sequence[str] = (), max_attempts: int | None = None,
) -> ShapeBatch:
    """Generate a balanced, shuffled mix of fresh positive and nearby hands.

    count/positive_count, seed, difficulty and labels are private controls, not
    learner prompt fields. Exclude all previously shown hands to prevent repeats
    across sessions; a different seed alone cannot guarantee global uniqueness.
    An unsatisfiable request returns no exercises, never an unconstrained hand.
    target_error is a caller-selected teaching focus, not a new diagnosis; every
    negative hand violates that condition but may also violate another one.
    For odd default counts, the extra positive/negative case is selected by seed;
    an explicit positive_count is exact. History is never truncated: at most
    10000 ASCII PBN strings of 32 characters and 12000 combined history entries
    plus reserved sampling attempts are accepted. Larger requests fail closed.
    """
    if not _in_scope(context, call, canon_version):
        return ShapeBatch("OUT_OF_SCOPE", "PROFILE_VERSION_OR_PUBLIC_CONTEXT_MISMATCH")
    if type(seed) is not int or type(count) is not int or not 1 <= count <= 100:
        raise ShapeGenerationError("integer seed and count 1..100 required")
    if positive_count is not None and (type(positive_count) is not int or not 0 <= positive_count <= count):
        raise ShapeGenerationError("positive_count must be an integer in 0..count")
    if not isinstance(difficulty, str) or difficulty not in {"BASIC", "CLOSE"}:
        raise ShapeGenerationError("difficulty must be BASIC or CLOSE")
    if not isinstance(target_error, str) or target_error not in _ERROR_TARGETS:
        raise ShapeGenerationError("unknown target_error")
    limit = max_attempts if max_attempts is not None else count * 20
    if type(limit) is not int or not count <= limit <= 10_000:
        raise ShapeGenerationError("max_attempts must be in count..10000")
    if suit_length_constraints is not None and not isinstance(suit_length_constraints, dict):
        raise ShapeGenerationError("suit_length_constraints must be an object")
    if suit_length_constraints is not None and set(suit_length_constraints) - set(SUITS):
        raise ShapeGenerationError("constraint keys must be exactly S/H/D/C")
    seen = _excluded_fingerprints(excluded_hands, limit)
    rng = random.Random(seed)
    if positive_count is None:
        positive_count = count // 2 + (rng.getrandbits(1) if count % 2 else 0)
    positive, negative = _length_pools(call, difficulty, suit_length_constraints, target_error)
    if (positive_count and not positive) or (count - positive_count and not negative):
        return ShapeBatch("UNSATISFIABLE_CONSTRAINTS", "NO_SHAPE_FOR_REQUESTED_CASE_MIX")
    labels = [True] * positive_count + [False] * (count - positive_count)
    rng.shuffle(labels)
    pools = {True: positive.copy(), False: negative.copy()}
    for pool in pools.values():
        rng.shuffle(pool)
    indexes = {True: 0, False: 0}
    exercises = []
    attempts = 0
    for label in labels:
        pool = pools[label]
        shape = pool[indexes[label] % len(pool)]
        indexes[label] += 1
        while attempts < limit:
            attempts += 1
            hand = ".".join("".join(rank for rank in RANKS if rank in set(chosen))
                            for chosen in (rng.sample(RANKS, length) for length in shape))
            fingerprint = hand_fingerprint(hand)
            if fingerprint in seen:
                continue
            agrees, violations = inspect_shape(hand, call)
            result = _assessment(context, hand, call, catalog)
            if result.get("status") not in {"SUPPORTED", "CONTRADICTED"}:
                return ShapeBatch("ABSTAIN", result.get("reason", "CANON_ASSESSMENT_UNAVAILABLE"), attempts=attempts)
            if agrees != label or (result["status"] == "SUPPORTED") != agrees:
                return ShapeBatch("ABSTAIN", "ASSESSOR_DISAGREEMENT", attempts=attempts)
            seen.add(fingerprint)
            # No seed or case label is encoded in the public identity.
            identity = sha256(f"{GENERATOR_VERSION}:{VERSION}:{PROFILE}:{call}:{fingerprint}".encode()).hexdigest()
            exercises.append(ShapeExercise(identity, hand, call, deepcopy(dict(context)),
                             agrees, violations, deepcopy(result["source_bindings"][0]), seed, difficulty, target_error))
            break
        else:
            return ShapeBatch("GENERATION_EXHAUSTED", "NO_FRESH_HAND_WITHIN_BUDGET", attempts=attempts)
    return ShapeBatch("GENERATED", exercises=tuple(exercises), attempts=attempts)
