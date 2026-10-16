"""Pure offline projection of one pinned candidate; no API, learner state or I/O writes.

Follows ShapeExercise's student_view/exercise_version_fields boundary, without
importing tournament semantics or its live canon. This is a fixture adapter,
not an arbitrary lesson validator or a free-text bridge assessment engine.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

VERSION = "low-card-teaching-step-adapter-v1"
FIXTURE = Path(__file__).resolve().parents[1] / "data/research/low_card_teaching_step_candidate_v1.json"
# Set from the reviewed local fixture. Any editorial change requires review.
FIXTURE_SHA256 = "b9984a9d4a8e2930a20f02a448c8507ef2ad8c7c1946e3f8e8a7a3eb269d649e"
COUNTEREX_FIXTURE = FIXTURE.with_name("low_card_counterexamples_candidate_v1.json")
COUNTEREX_SHA256 = "6eb06b33991dcb5183428d29f0c500ceb03ca623eb70a1332fe6e064259e1e2d"
_CARD = re.compile(r"^[SHDC][AKQJT98765432]$")
_BOUNDARY = {"persisted": False, "mastery_event_created": False,
             "student_profile_written": False, "curriculum_activated": False,
             "canon_written": False, "actual_mastery": None}


class TeachingStepError(ValueError):
    pass


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _read_fixture():
    raw = FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise TeachingStepError("candidate fixture changed; review and repin required")
    return json.loads(raw)


def _step(step_id):
    spec = _read_fixture()
    for item in spec["steps"]:
        if item["step_id"] == step_id:
            return spec, item
    if isinstance(step_id, str) and step_id.startswith("counter."):
        raw = COUNTEREX_FIXTURE.read_bytes()
        if hashlib.sha256(raw).hexdigest() != COUNTEREX_SHA256:
            raise TeachingStepError("counterexample fixture changed; review and repin required")
        extra = json.loads(raw)
        for item in extra["steps"]:
            if item["step_id"] == step_id:
                # Reuse authority, dependencies and mastery proposal verbatim.
                # Only content/provenance changes; there is no second skill graph.
                spec["version"] = "counterexamples." + extra["version"]
                spec["source_bindings"] += extra["source_bindings"]
                spec["provenance"] = extra["provenance"]
                spec["_composed_fixture_sha256"] = _hash({"base": FIXTURE_SHA256, "counterexamples": COUNTEREX_SHA256})
                return spec, item
    raise TeachingStepError("unknown candidate step")


def candidate_dependencies():
    """The already-authored candidate graph; no catalog registration or states."""
    return deepcopy(_read_fixture()["candidate_dependencies"])


def student_view(step_id):
    """Explicit answer-free allowlist; never serialize the full private fixture."""
    spec, item = _step(step_id)
    public = {"exercise_id": step_id, "task": "LOW_CARD_ENTRY_CANDIDATE",
              "method_version": VERSION, "candidate_version": spec["version"],
              "fixture_sha256": spec.get("_composed_fixture_sha256", FIXTURE_SHA256),
              "response_schema": "low-card-structured-response-v1",
              "response_fields": ["schema", "exercise_id", "prompt_sha256", "answers", "explanation"],
              "presentation": deepcopy(item["presentation"])}
    public["prompt_sha256"] = _hash(public)
    return public


def exercise_version_fields(step_id):
    """Private teacher-side shape compatible with existing exercise_version fields.

No exercise/attempt UUID is invented, no SQL is invoked, and the projection
must not be sent to a learner. Only its prompt is the learner view.
"""
    spec, item = _step(step_id)
    return {"status": "PREPARED_LOCAL_CANDIDATE", **_BOUNDARY,
            "exercise_version": {
                "prompt": student_view(step_id),
                "expected_solution": deepcopy(item["private"]),
                "rubric": {"scope": "FORMATIVE_TYPED_FIELDS_ONLY",
                           "human_review": deepcopy(item["private"]["human_review"]),
                           "mastery_criterion_proposal": deepcopy(spec["mastery_criterion_proposal"]),
                           "candidate_dependencies": deepcopy(spec["candidate_dependencies"]),
                           "authority": deepcopy(spec["authority"])},
                "generated_by": VERSION, "method_version": VERSION,
                "provenance": {"fixture_sha256": spec.get("_composed_fixture_sha256", FIXTURE_SHA256),
                               "source_bindings": deepcopy(spec["source_bindings"]),
                               **deepcopy(spec["provenance"])},
                "status": "candidate"}}


def _valid_field(value, question):
    kind = question["type"]
    if kind == "integer":
        return type(value) is int and question["minimum"] <= value <= question["maximum"]
    if kind == "boolean":
        return type(value) is bool
    if kind == "choice":
        return type(value) is str and value in {o["id"] for o in question["options"]}
    if kind == "card_pair":
        return (type(value) is list and len(value) == 2
                and all(type(c) is str and _CARD.fullmatch(c) for c in value))
    return False


def assess_response(step_id, response):
    """Check typed formative fields only; Russian prose always needs a human.

Missing/unsupported evidence is not diagnosed as a learner misconception.
The returned response copy is a proposed local record, not saved history.
Calls are stateless: session identity, ordering, authenticity and assistance
history must be supplied by a future authorized delivery layer.
"""
    _, item = _step(step_id)
    result = {"status": "UNASSESSED", "exercise_id": step_id, **_BOUNDARY,
              "assessment_scope": "FORMATIVE_TYPED_FIELDS_ONLY",
              "explanation_review": "HUMAN_REVIEW_REQUIRED", "learning_effect_observed": False,
              "criterion_met": None, "learner_error": None, "issues": [], "criteria": [],
              "feedback": [], "retry_step_id": None, "retry_support_level": None}
    required = {"schema", "exercise_id", "prompt_sha256", "answers", "explanation"}
    if type(response) is not dict or set(response) != required:
        result["issues"].append("INCOMPLETE_OR_UNSUPPORTED_RESPONSE")
        return result
    if (response["schema"] != "low-card-structured-response-v1"
            or response["exercise_id"] != step_id
            or response["prompt_sha256"] != student_view(step_id)["prompt_sha256"]):
        result["issues"].append("STALE_OR_WRONG_PROMPT_BINDING")
        return result
    questions = item["presentation"]["questions"]
    answers = response["answers"]
    if type(answers) is not dict or set(answers) != {q["id"] for q in questions}:
        result["status"] = "NEEDS_CLARIFICATION"
        result["issues"].append("COMPLETE_TYPED_ANSWERS_REQUIRED")
        return result
    if any(not _valid_field(answers[q["id"]], q) for q in questions):
        result["issues"].append("UNSUPPORTED_ANSWER_TYPE_OR_CHOICE")
        return result
    if type(response["explanation"]) is not str or not 1 <= len(response["explanation"].strip()) <= 4000:
        result["status"] = "NEEDS_CLARIFICATION"
        result["issues"].append("EXPLANATION_REQUIRED_MAX_4000_CHARACTERS")
        return result
    result["raw_response"] = deepcopy(response)
    result["response_sha256"] = _hash(response)
    expected = item["private"]["expected_answers"]
    for question in questions:
        key = question["id"]
        passed = answers[key] == expected[key]  # types checked above (bool != integer domain)
        result["criteria"].append({"criterion_id": key, "status": "PASS" if passed else "FAIL",
                                   "response_pointer": "/answers/" + key})
        if not passed:
            result["feedback"].append({"criterion_id": key, **deepcopy(item["private"]["feedback"][key])})
    result["status"] = "TYPED_FIELDS_NEED_WORK" if result["feedback"] else "TYPED_FIELDS_MATCH_REVIEW_EXPLANATION"
    if not result["feedback"]:
        result["feedback"] = [{"criterion_id": "explanation", "code": "HUMAN_REVIEW_REQUIRED",
                               "text": "Структурированные ответы совпали. Преподавателю нужно проверить причинное объяснение; освоение этим не установлено."}]
    result["retry_step_id"] = item["next"]["after_feedback"]
    result["retry_support_level"] = "GUIDED" if result["retry_step_id"] else None
    result["next_action"] = "REVIEW_EXPLANATION_THEN_AUTHORED_RETRY" if result["retry_step_id"] else "REVIEW_EXPLANATION_NO_FURTHER_RETRY_AUTHORED"
    return result


def assessment_fields(step_id, response):
    """Return existing assessment-column shape, still unsaved and candidate-only."""
    result = assess_response(step_id, response)
    if result["status"] in {"UNASSESSED", "NEEDS_CLARIFICATION"}:
        return result
    return {"status": "PREPARED_LOCAL_CANDIDATE", **_BOUNDARY,
            "exercise_attempt_assessment": {"authority_class": "ai", "assessment_purpose": "learning",
                                            "method_version": VERSION, "result": result}}
