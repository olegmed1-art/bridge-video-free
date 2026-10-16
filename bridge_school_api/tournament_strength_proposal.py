"""Offline, opt-in inference experiment; not executable school canon.

Private callers supply freshly reviewed opening evidence and shape catalog.
Matching a pinned snapshot does not establish live activation. No API route,
database adapter, mastery update, canonical bid approval or DDS call is added.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Any
from uuid import UUID

from .tournament_shape_core import PROFILE, SHAPE_ONLY, VERSION, digest, evaluate
from .tournament_shape_exercises import (
    ShapeExercise, ShapeGenerationError, generate_shape_exercises, hand_fingerprint,
)

POLICY_VERSION = "one-nt-shape-game-strength-inference-v1"
POLICY_SHA256 = "8d4022b727db6255bf9b42b4e9dd6b9e1d241555a7f051dd367c5edf5943ace2"
GENERATOR_VERSION = "tournament-strength-proposal-exercises-v1"
POLICY_PATH = Path(__file__).with_name("policies") / "one_nt_game_strength_proposal_v1.json"


def load_policy():
    """Return a fresh proposal, never reinterpret a modified file as new canon."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if digest(policy) != POLICY_SHA256:
        raise ValueError("PROPOSAL_POLICY_HASH_MISMATCH")
    return policy


def derive_strength_requirement(premises):
    """Small deterministic proof, also testable with labelled synthetic facts.

    Arithmetic alone does not authorize any source: the live-school proposal
    wrapper separately pins the applicable agreement, opening and heuristic.
    """
    stop = {"status": "ABSTAIN", "responder_hcp_min": None, "steps": []}
    if type(premises) is not dict or set(premises) != {"agreement", "game_goal", "opening"}:
        return stop | {"reason": "MISSING_REASONING_PREMISE"}
    for name, premise in premises.items():
        if (type(premise) is not dict or premise.get("conflicts") != []
                or premise.get("status") != ("ACCEPTED_HEURISTIC" if name == "game_goal" else "CONFIRMED")
                or not isinstance(premise.get("source"), dict) or not premise["source"]
                or not isinstance(premise.get("version"), str) or not premise["version"]):
            return stop | {"reason": "UNKNOWN_OR_CONFLICTING_REASONING_PREMISE"}
    agreement, goal, opening = (premises[k] for k in ("agreement", "game_goal", "opening"))
    if (agreement.get("forcing_status") != "FG" or agreement.get("required_shape") != [1, 3, 4, 5]
            or goal.get("source_kind") != "heuristic" or opening.get("point_method") != "HCP"):
        return stop | {"reason": "UNSUPPORTED_REASONING_PREMISE"}
    target, minimum, maximum = goal.get("hcp"), opening.get("hcp_min"), opening.get("hcp_max")
    if (any(type(v) is not int for v in (target, minimum, maximum))
            or not 0 <= minimum <= maximum <= 37 or not minimum <= target <= 40
            or not 0 <= target - minimum <= 37):
        return stop | {"reason": "INVALID_NUMERIC_REASONING_PREMISE"}
    required = target - minimum
    return {
        "status": "DERIVED", "reasoning_version": "game-strength-subtraction-proof-v1",
        "source_kind": "inference", "responder_hcp_min": required,
        "premises": deepcopy(premises),
        "steps": [
            {"id": "agreement", "fact": "5431 convention", "source": agreement["source"]},
            {"id": "game_force", "depends_on": ["agreement"], "fact": "FG", "guarantees_tricks": False},
            {"id": "game_goal", "depends_on": ["game_force"], "hcp": target,
             "source_kind": "heuristic", "source": goal["source"]},
            {"id": "partner_min", "hcp": minimum, "opening_range": [minimum, maximum],
             "source": opening["source"]},
            {"id": "responder_min", "depends_on": ["game_goal", "partner_min"],
             "operation": "subtract", "operands": [target, minimum], "result": required, "unit": "HCP"},
            {"id": "shape_required", "depends_on": ["agreement"], "shape_sorted": [1, 3, 4, 5],
             "note": "Agreement is known from source, never inferred from the cards"},
        ],
    }


def _opening_matches(evidence, context, policy):
    # This is a private source-reader boundary, not a client assertion of trust.
    # The live adapter deliberately does not accept this proposal or evidence.
    if type(context) is not dict or not isinstance(context.get("school_id"), UUID):
        return False
    expected = {
        "school_id": str(context.get("school_id")), "system_profile": PROFILE,
        "rule_key": policy["opening_rule_key"],
        "hcp_min": policy["opener_hcp_min"], "hcp_max": policy["opener_hcp_max"],
        "point_method": "HCP", "source_kind": "school_canon_snapshot",
        "source_url": policy["opening_source_url"],
        "source_version": policy["opening_source_version"],
        "evidence_scope": "REVIEWED_SNAPSHOT_NOT_RUNTIME_ACTIVATION",
    }
    return type(evidence) is dict and digest(evidence) == digest(expected)


def _reasoning_premises(binding, forcing_status, opening_evidence, policy):
    return {
        "agreement": {"status": "CONFIRMED", "conflicts": [], "version": VERSION,
                      "forcing_status": forcing_status, "required_shape": [1, 3, 4, 5],
                      "source": deepcopy(binding)},
        "game_goal": {"status": "ACCEPTED_HEURISTIC", "conflicts": [],
                      "version": policy["goal_premise_version"], "source_kind": "heuristic",
                      "hcp": policy["partnership_target_hcp"],
                      "source": {"url": policy["world_reference_url"], "role": policy["world_reference_role"],
                                 "policy_sha256": POLICY_SHA256}},
        "opening": {"status": "CONFIRMED", "conflicts": [],
                    "version": opening_evidence["source_version"], "point_method": opening_evidence["point_method"],
                    "hcp_min": opening_evidence["hcp_min"], "hcp_max": opening_evidence["hcp_max"],
                    "source": deepcopy(opening_evidence)},
    }


def evaluate_proposal(context, hand, call, catalog, *, opening_evidence, policy_version):
    """Check shape AND inferred strength under an explicitly named proposal.

    A fail means a known condition fails. Unknown sources/ranges/versions always
    abstain; they must never become negative learner examples.
    """
    result = {
        "status": "ABSTAIN", "assessment_scope": "derived_policy_conditions",
        "source_kind": "inference", "policy_status": "PROPOSAL",
        "policy_version": POLICY_VERSION, "policy_sha256": POLICY_SHA256,
        "assessment_complete": False, "proposal_conditions_complete": False,
        "policy_conditions_met": None, "call_approved": None,
        "learner_error": None, "game_guaranteed": None, "best_bid_assessed": False,
        "persisted": False, "source_bindings": [],
    }
    if policy_version != POLICY_VERSION:
        return result | {"reason": "PROPOSAL_VERSION_MISMATCH"}
    try:
        policy = load_policy()
    except (OSError, ValueError, TypeError):
        return result | {"reason": "PROPOSAL_POLICY_UNAVAILABLE_OR_CHANGED"}
    try:
        if not _opening_matches(opening_evidence, context, policy):
            return result | {"reason": "OPENING_EVIDENCE_MISSING_OR_CHANGED"}
        shape = evaluate(dict(context, hand_pbn=hand), call, catalog, assessment_mode=SHAPE_ONLY)
    except (TypeError, ValueError, KeyError, AttributeError):
        return result | {"reason": "INVALID_PROPOSAL_EVIDENCE"}
    if shape["status"] != "PARTIAL" or len(shape["source_bindings"]) != 1:
        return result | {"reason": shape["reason"]}
    premises = _reasoning_premises(shape["source_bindings"][0], shape["forcing_status"], opening_evidence, policy)
    proof = derive_strength_requirement(premises)
    if proof["status"] != "DERIVED":
        return result | {"reason": proof["reason"]}
    threshold = proof["responder_hcp_min"]
    target = premises["game_goal"]["hcp"]
    partner_min = premises["opening"]["hcp_min"]
    shape_ok = shape["shape_status"] == "MATCH"
    hcp = shape["observed_hcp"]
    strength_ok = hcp >= threshold
    passed = shape_ok and strength_ok
    violations = []
    if not shape_ok:
        violations.append("Форма не соответствует соглашению: нужны 5431, синглет в названном мажоре и 5–4 в минорах.")
    if not strength_ok:
        violations.append(f"В руке {hcp} HCP, меньше выведенного порога {threshold} HCP.")
    explanation = (
        f"Предложенная модель: целевой ориентир пары {target} HCP минус нижняя граница открытия {partner_min} HCP "
        f"даёт порог отвечающего {threshold} HCP. Это вывод, а не числовое правило из SRC-0096. "
        f"В руке {hcp} HCP; форма " + ("соответствует" if shape_ok else "не соответствует") +
        "; условие силы " + ("выполнено" if strength_ok else "не выполнено") + ". " +
        " ".join(violations) +
        " Прохождение этих условий не одобряет лучший выбор заявки и не гарантирует взятки. "
        "Форсирование до гейма задаёт обязательство продолжать торговлю."
    )
    return result | {
        "status": "PROPOSAL_PASS" if passed else "PROPOSAL_FAIL",
        "reason": "DERIVED_CONDITIONS_MET" if passed else "DERIVED_CONDITION_FAILED",
        "proposal_conditions_complete": True, "policy_conditions_met": passed,
        "observed_hcp": hcp, "observed_shape": shape["observed_shape"],
        "required_conditions": {"shape": shape["shape_status"],
                                "responder_hcp_min": "PASS" if strength_ok else "FAIL",
                                "opening_15_17_hcp": "VERIFIED_SNAPSHOT"},
        "responder_hcp_min": threshold,
        "partnership_hcp_floor": hcp + partner_min,
        "violated_conditions": violations, "explanation": explanation,
        "source_bindings": deepcopy(shape["source_bindings"]),
        "opening_binding": deepcopy(opening_evidence),
        "derivation": proof, "policy": deepcopy(policy),
    }


@dataclass(frozen=True)
class ProposalExercise:
    """Server-side exercise; source/answer details stay out of student_view."""
    exercise_id: str
    _shape_exercise: ShapeExercise = field(repr=False)
    _expected: bool = field(repr=False)
    _opening_binding: dict[str, Any] = field(repr=False)
    _policy_version: str = field(repr=False)
    _policy_sha256: str = field(repr=False)
    _derivation: dict[str, Any] = field(repr=False)

    @property
    def hand_pbn(self):
        return self._shape_exercise.hand_pbn

    @property
    def proposed_call(self):
        return self._shape_exercise.proposed_call

    def student_view(self):
        try:
            policy = load_policy()
            if (self._policy_version != POLICY_VERSION or self._policy_sha256 != POLICY_SHA256
                    or not _opening_matches(self._opening_binding, self._shape_exercise._context, policy)):
                raise ValueError("EXERCISE_PROPOSAL_VERSION_OR_SOURCE_CHANGED")
            expected_proof = derive_strength_requirement(_reasoning_premises(
                self._shape_exercise._binding, "FG", self._opening_binding, policy))
            if digest(self._derivation) != digest(expected_proof):
                raise ValueError("EXERCISE_PROOF_CHANGED")
        except (OSError, ValueError, TypeError, KeyError):
            return {"status": "ABSTAIN", "reason": "EXERCISE_PROOF_OR_POLICY_CHANGED", "learner_error": None}
        view = self._shape_exercise.student_view()
        goal = self._derivation["premises"]["game_goal"]["hcp"]
        opening = self._derivation["premises"]["opening"]
        view.update(exercise_id=self.exercise_id, task="DERIVED_POLICY_CONDITIONS",
                    assessment_mode="PROPOSAL", assessment_version=self._policy_version,
                    policy_status="PROPOSAL", source_kind="inference",
                    question="Соответствует ли рука форме соглашения и силовому условию предложенной модели?",
                    limitation=f"Порог силы выводится из ориентира пары {goal} HCP и открытия 1БК {opening['hcp_min']}–{opening['hcp_max']} HCP. Это предложение, не активированный канон и не выбор лучшей заявки.")
        return view

    def assess_response(self, response, *, catalog, opening_evidence, policy_version):
        if type(response) is not bool:
            raise ShapeGenerationError("response must be a boolean")
        stop = {"status": "ABSTAIN", "learner_error": None, "call_approved": None}
        if (self._policy_version != policy_version or self._policy_sha256 != POLICY_SHA256):
            return stop | {"reason": "EXERCISE_PROPOSAL_VERSION_MISMATCH"}
        shape = self._shape_exercise
        checked = evaluate_proposal(shape._context, self.hand_pbn, self.proposed_call, catalog,
                                    opening_evidence=opening_evidence, policy_version=policy_version)
        if checked["status"] == "ABSTAIN":
            return checked
        if (checked["opening_binding"] != self._opening_binding
                or checked["source_bindings"][0] != shape._binding):
            return stop | {"reason": "EXERCISE_SOURCE_BINDING_CHANGED"}
        if digest(checked["derivation"]) != digest(self._derivation):
            return stop | {"reason": "EXERCISE_PROOF_CHANGED"}
        if checked["policy_conditions_met"] != self._expected:
            return stop | {"reason": "EXERCISE_ASSESSOR_DISAGREEMENT"}
        return checked | {
            "status": "PROPOSAL_CORRECT" if response == self._expected else "PROPOSAL_INCORRECT",
            "proposal_response_error": response != self._expected,
            "expected_response": self._expected,
        }


@dataclass(frozen=True)
class ProposalBatch:
    status: str
    reason: str | None = None
    exercises: tuple[ProposalExercise, ...] = ()
    attempts: int = 0
    generator_version: str = GENERATOR_VERSION

    def student_exercises(self):
        return [item.student_view() for item in self.exercises]


def generate_proposal_exercises(*, context, call, catalog, opening_evidence, policy_version,
                                seed, count=6, positive_count=None, max_attempts=None):
    """Bounded, seeded rejection sampling; no unknown case is a negative case.

    Only local proposal exercises are returned. Underlying hand generation and
    both assessments parse actual cards; no partner hand is generated or seen.
    """
    if type(seed) is not int or type(count) is not int or not 1 <= count <= 100:
        raise ShapeGenerationError("integer seed and count 1..100 required")
    if positive_count is None:
        positive_count = (count + 1) // 2
    if type(positive_count) is not int or not 0 <= positive_count <= count:
        raise ShapeGenerationError("positive_count must be in 0..count")
    limit = count * 40 if max_attempts is None else max_attempts
    if type(limit) is not int or not count <= limit <= 10000:
        raise ShapeGenerationError("max_attempts must be in count..10000")
    rng = random.Random(seed)
    labels = [True] * positive_count + [False] * (count - positive_count)
    rng.shuffle(labels)
    results, seen, attempts = [], set(), 0
    for target in labels:
        while attempts < limit:
            attempts += 1
            # Negative candidates include wrong shape and insufficient strength.
            shape_match = target or bool(rng.getrandbits(1))
            candidates = generate_shape_exercises(context=context, call=call,
                canon_version=VERSION, catalog=catalog, seed=rng.getrandbits(64),
                count=1, positive_count=int(shape_match), assessment_mode=SHAPE_ONLY)
            if candidates.status != "GENERATED":
                return ProposalBatch("ABSTAIN", candidates.reason, attempts=attempts)
            shape = candidates.exercises[0]
            checked = evaluate_proposal(context, shape.hand_pbn, call, catalog,
                                        opening_evidence=opening_evidence, policy_version=policy_version)
            if checked["status"] == "ABSTAIN":
                return ProposalBatch("ABSTAIN", checked["reason"], attempts=attempts)
            fingerprint = hand_fingerprint(shape.hand_pbn)
            if checked["policy_conditions_met"] != target or fingerprint in seen:
                continue
            seen.add(fingerprint)
            identity = sha256(f"{GENERATOR_VERSION}:{POLICY_SHA256}:{shape.exercise_id}:{digest(opening_evidence)}".encode()).hexdigest()
            results.append(ProposalExercise(identity, shape, target, deepcopy(opening_evidence),
                                           POLICY_VERSION, POLICY_SHA256, deepcopy(checked["derivation"])))
            break
        else:
            return ProposalBatch("GENERATION_EXHAUSTED", "NO_PROPOSAL_CASE_WITHIN_BUDGET", attempts=attempts)
    return ProposalBatch("PROPOSAL_GENERATED", exercises=tuple(results), attempts=attempts)
