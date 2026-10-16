"""Pinned data-only import of the generator's four candidate exercises.

Uses the existing student_view/exercise_version_fields shapes. These IDs are
available for offline review; TeacherLesson routing and automatic assessment
are deliberately not registered by this fixture import.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import teaching_step

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "data/research/spot_card_candidate_v2"
PINS = {
    "candidate.json": "e3f0710d1bba8dc17b136395197cf2dc4b7a6c24d00be4c78c4d883e69897090",
    "routing-handoff/manifest.json": "cf2d3460c0ea7427eebfdc39a8f392c636a82a4b6abc8d77bfee10e0b08f91fd",
    "routing-handoff/student-prompts.json": "c4f2feb91897077c41c204855e8904033b0636df490e2193497fa1f5d9db0d64",
    "routing-handoff/teacher-versions.json": "b16a2dd1ef3d58f7ded62cec917a851c6028edb01a696df5e642757008bc7d96",
    "routing-handoff/SPOT_TRICK_COUNT_V1.json": "c5a512261c4a40d30b74c381f9a7b8302cfc46d4462eeb264ab0046252d02edd",
    "routing-handoff/RULE_BASIS.md": "6a115bb118f99327690716a7876e613521386f08dc472c8acfe0e2826b05ddc3",
    "independent_i2_results.json": "376d5ab98b163bbc99ceb19831776eaaf927a456529381c2a010f510b1e62478",
    "independent_i2_report.md": "65303de937a5fb6c152d34c2bfc005307c1556b66740acfdf02a7ad93188656e",
    "source-manifest.json": "4b8ee25b77387bed8491bbec8a8ad5ce9bb6bb80723da79d30f241bf1a25335f",
}
PUBLIC_KEYS = {"exercise_id", "task", "method_version", "candidate_version", "fixture_sha256",
               "response_schema", "response_fields", "presentation", "prompt_sha256"}
PRESENTATION_KEYS = {"title", "author_label", "strain", "partial_deal", "position_kind",
                     "trick_index", "cards_per_hand", "declarer", "dummy_seat", "lead",
                     "hand", "dummy", "target_suit", "card_encoding", "given_information",
                     "task_text", "questions", "explanation_prompt"}


class SpotCandidateError(ValueError):
    pass


def _require(value, reason):
    if not value:
        raise SpotCandidateError(reason)


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _pointer(document, pointer, collection):
    parts = pointer.split("/")
    _require(len(parts) == 3 and parts[:2] == ["", collection] and parts[2] in ("0", "1", "2", "3"),
             "unsupported candidate pointer")
    return document[collection][int(parts[2])]


def _load():
    blobs = {}
    for relative, expected in PINS.items():
        raw = (ROOT / relative).read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == expected, "candidate import changed; review and repin")
        blobs[relative] = raw
    manifest = json.loads(blobs["routing-handoff/manifest.json"])
    candidate = json.loads(blobs["candidate.json"])
    public = json.loads(blobs["routing-handoff/student-prompts.json"])
    private = json.loads(blobs["routing-handoff/teacher-versions.json"])
    metric = json.loads(blobs["routing-handoff/SPOT_TRICK_COUNT_V1.json"])
    _require(manifest["schema"] == "local-teacher-routing-candidate-manifest-v2"
             and manifest["handoff_revision"] == 2
             and manifest["status"] == "PREPARED_LOCAL_CANDIDATE"
             and manifest["runtime_registration"] == "NOT_REGISTERED_PINNED_FIXTURES_ONLY",
             "unsupported candidate state")
    _require(manifest["answer_metric"] == {
        "metric_id": "guaranteed-spot-tricks-from-position-v1",
        "answer_field": "guaranteed_spot_tricks_from_position",
        "contract_file": "SPOT_TRICK_COUNT_V1.json",
        "contract_sha256": PINS["routing-handoff/SPOT_TRICK_COUNT_V1.json"],
        "legacy_low_tricks_alias_allowed": False}, "answer metric binding")
    _require(metric["legacy_compatibility"]["alias_allowed"] is False
             and metric["legacy_compatibility"]["silent_reinterpretation_allowed"] is False,
             "legacy metric reinterpretation")
    _require(manifest["source_candidate"] == {"path": "../candidate.json", "sha256": PINS["candidate.json"]}
             and manifest["public_prompt_file"] == "student-prompts.json"
             and manifest["private_version_file"] == "teacher-versions.json"
             and manifest["public_prompt_sha256"] == PINS["routing-handoff/student-prompts.json"]
             and manifest["private_version_sha256"] == PINS["routing-handoff/teacher-versions.json"],
             "manifest file bindings")
    for binding in manifest["target_contract_sources"]:
        # Resolve repository-relative paths only; producer absolute paths are provenance.
        path = (REPO / binding["path"]).resolve()
        _require(path.is_relative_to(REPO.resolve()), "contract path outside repository")
        _require(hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"],
                 "consumer contract changed; reconcile import")
    authority = manifest["authority"]
    _require(authority["authority_class"] == "CANDIDATE_RESEARCH" and authority["review_state"] == "REVIEW_REQUIRED"
             and all(authority[k] is False for k in ("publication_allowed", "curriculum_activation_allowed",
                 "student_profile_write_allowed", "canon_write_allowed", "mastery_write_allowed"))
             and candidate["authority"]["automatic_student_scoring_allowed"] is False, "candidate authority")
    nodes = {n["id"] for n in teaching_step.candidate_dependencies()["nodes"]}
    _require(set(manifest["candidate_routing_mapping"]["criterion_skill"].values()) <= nodes,
             "mapping outside existing graph")
    _require(len(manifest["entries"]) == len(public["prompts"]) == len(private["versions"]) == len(candidate["exercises"]) == 4,
             "candidate cardinality")
    rows = {}
    for entry in manifest["entries"]:
        prompt = _pointer(public, entry["prompt_pointer"], "prompts")
        version = _pointer(private, entry["teacher_version_pointer"], "versions")
        source = _pointer(candidate, entry["candidate_pointer"], "exercises")
        exercise_id = entry["exercise_id"]
        _require(exercise_id not in rows and prompt["exercise_id"] == exercise_id,
                 "duplicate or mismatched public id")
        _require(set(prompt) == PUBLIC_KEYS and set(prompt["presentation"]) == PRESENTATION_KEYS,
                 "public projection allowlist")
        unsigned = {k: v for k, v in prompt.items() if k != "prompt_sha256"}
        _require(_hash(unsigned) == prompt["prompt_sha256"] == entry["prompt_sha256"], "prompt hash")
        _require(version["exercise_version"]["prompt"] == prompt
                 and version["exercise_version"]["status"] == "candidate"
                 and version["exercise_version"]["rubric"]["automatic_assessment_enabled"] is False,
                 "private/public version binding")
        question_ids = {q["id"] for q in prompt["presentation"]["questions"]}
        expected = version["exercise_version"]["expected_solution"]["expected_answers"]
        _require(question_ids == set(expected) == {"visible_count", "missing_count", "guaranteed_spot_tricks_from_position"}
                 and entry["answer_field"] == metric["answer_field"]
                 and entry["answer_metric_id"] == metric["metric_id"], "question metric mismatch")
        p, original = prompt["presentation"], source["prompt"]
        _require(p["cards_per_hand"] == 5 and p["trick_index"] == 9
                 and p["given_information"] == original["given_information"]
                 and p["given_information"]["defenders_remaining_lengths"] == [2, 2]
                 and p["target_suit"] == original["target_suit"], "position conditions")
        for role, field in (("hand", "hand_pbn"), ("dummy", "dummy_pbn")):
            expected = {s: list(ranks) for s, ranks in zip("SHDC", original[field].split("."))}
            _require(p[role] == expected and sum(map(len, p[role].values())) == 5, "five-card projection")
        rows[exercise_id] = {"prompt": prompt, "teacher_version": version}
    return manifest, rows


def candidate_ids():
    """Opaque IDs for local review, not registered TeacherLesson activities."""
    return list(_load()[1])


def student_view(exercise_id):
    """Only the already-pinned neutral projection; no full deals or answer keys."""
    rows = _load()[1]
    _require(type(exercise_id) is str and exercise_id in rows, "unknown candidate fixture")
    return deepcopy(rows[exercise_id]["prompt"])


def exercise_version_fields(exercise_id):
    """Private review-only shape; never send this return value to a learner."""
    rows = _load()[1]
    _require(type(exercise_id) is str and exercise_id in rows, "unknown candidate fixture")
    return deepcopy(rows[exercise_id]["teacher_version"])


def integration_status():
    """Teacher-only mapping and gates. No fictitious canon approval needed."""
    manifest, _ = _load()
    return {"audience": "TEACHER_ONLY", "status": "IMPORTED_LOCAL_CANDIDATE_FIXTURES",
            "runtime_registration": manifest["runtime_registration"],
            "automatic_assessment_enabled": False, "free_text": "HUMAN_REVIEW_REQUIRED",
            "answer_metric": deepcopy(manifest["answer_metric"]),
            "authority": deepcopy(manifest["authority"]),
            "candidate_routing_mapping": deepcopy(manifest["candidate_routing_mapping"]),
            "canon_contract": deepcopy(manifest["canon_contract"])}
