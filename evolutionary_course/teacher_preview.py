"""Explicit local preview bridge: accepted E1 routing -> one pinned candidate.

The ordinary TeacherLesson is unchanged. The spot response is raw evidence for
human review, never an automatic score, mastery event or catalog registration.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import spot_candidate_fixture as spots
from .teacher_routing import TeacherLesson, TeacherRoutingError, BOUNDARY, VERSION, POLICY_SHA256, _digest

MODE = "SPOT_CANDIDATE_PREVIEW_V1"


class PreviewTeacherLesson(TeacherLesson):
    def __init__(self, session_id, preview_exercise_id):
        if type(preview_exercise_id) is not str or preview_exercise_id not in spots.candidate_ids():
            raise TeacherRoutingError("unknown preview candidate id")
        self._preview_exercise_id = preview_exercise_id
        super().__init__(session_id)
        # Pin all data/projection/metric inputs plus both adapter modules.
        for relative, digest in spots.PINS.items():
            self._source_hashes[str(spots.ROOT / relative)] = digest
        manifest = json.loads((spots.ROOT / "routing-handoff/manifest.json").read_bytes())
        for binding in manifest["target_contract_sources"]:
            self._source_hashes[str(spots.REPO / binding["path"])] = binding["sha256"]
        for path in (Path(__file__), Path(spots.__file__)):
            self._source_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        self._fresh()

    def _is_spot(self):
        return self._current is not None and self._current["public"]["prompt"]["exercise_id"] == self._preview_exercise_id

    def _issue(self, sid, reason):
        if sid != self._preview_exercise_id:
            super()._issue(sid, reason)
            self._current["public"]["session_mode"] = MODE
            self._journal[-1]["issuance"] = deepcopy(self._current["public"])
            return
        ordinal = len(self._runs) + 1
        public = {"session_id": self._session_id, "issuance_id": f"{self._session_id}:{ordinal}",
                  "ordinal": ordinal, "method_version": VERSION, "policy_sha256": POLICY_SHA256,
                  "session_mode": MODE, "prompt": spots.student_view(sid), "reason": reason,
                  "content_origin": "AUTHORED_EXERCISE_NOT_VIDEO_EPISODE",
                  "context_exposure": "NEW_IN_LOCAL_SESSION", "support_history": "PRIOR_LOCAL_SUPPORT",
                  "opportunity": "CANDIDATE_PREVIEW_MANUAL_REVIEW", "approved_catalog_member": False,
                  "automatic_assessment_enabled": False, "external_experience_and_help": "UNKNOWN", **BOUNDARY}
        self._current = {"public": public, "responses": [], "local_help": [], "closed": False}
        self._runs.append(self._current)
        self._journal.append({"kind": "ISSUED", "issuance": deepcopy(public)})

    def _choose(self, run, matched, focus):
        if run["public"]["prompt"]["exercise_id"] == "entry.e1" and matched:
            return self._preview_exercise_id, "EXPLICIT_CANDIDATE_PREVIEW_NOT_SKILL_ROUTING"
        return super()._choose(run, matched, focus)

    def current(self):
        result = super().current()
        result["session_mode"] = MODE
        return result

    def record_response(self, *, command_id, issuance_id, response):
        if not self._is_spot():
            return super().record_response(command_id=command_id, issuance_id=issuance_id, response=response)
        digest, old = self._begin("RESPONSE", command_id, issuance_id, response)
        if old is not None:
            return old
        run = self._current
        if len(run["responses"]) >= self._policy["max_responses_per_issuance"]:
            raise TeacherRoutingError("response bound reached; request feedback/review")
        prompt = run["public"]["prompt"]
        if type(response) is dict and (
                "exercise_id" in response and response["exercise_id"] != prompt["exercise_id"]
                or "prompt_sha256" in response and response["prompt_sha256"] != prompt["prompt_sha256"]):
            raise TeacherRoutingError("stale or wrong prompt response")
        record = {"command_id": command_id, "raw_response": deepcopy(response),
                  "response_sha256": _digest(response), "assessment_status": "ABSTAIN",
                  "assessment": {"status": "UNASSESSED", "criteria": [], "feedback": [],
                                 "issues": ["PREVIEW_MANUAL_REVIEW_ONLY"], "automatic_assessment_enabled": False},
                  "local_help_before_response": deepcopy(run["local_help"]),
                  "prior_session_help_count": len(self._help)}
        run["responses"].append(record)
        self._journal.append({"kind": "RESPONSE_RECORDED", "issuance_id": issuance_id, **deepcopy(record)})
        return self._save(command_id, digest, {
            "status": "RECORDED_PENDING_FEEDBACK", "issuance_id": issuance_id,
            "response_no": len(run["responses"]), "response_sha256": record["response_sha256"],
            "message": "Ответ сохранён для ручной проверки. Можно сохранить исправление.",
            "session_mode": MODE, "automatic_assessment_enabled": False, **BOUNDARY})

    def request_hint(self, *, command_id, issuance_id):
        if not self._is_spot():
            return super().request_hint(command_id=command_id, issuance_id=issuance_id)
        digest, old = self._begin("HINT", command_id, issuance_id, None)
        if old is not None:
            return old
        # No authored hint for this candidate: do not invent or log shown help.
        return self._save(command_id, digest, {
            "status": "PREVIEW_HINT_UNAVAILABLE", "assessment_status": "ABSTAIN", "hint": None,
            "message": "Для этого кандидата подсказка ещё не подготовлена. Ответ проверит преподаватель.",
            "session_mode": MODE, "automatic_assessment_enabled": False, **BOUNDARY})

    def advance(self, *, command_id, issuance_id):
        if not self._is_spot():
            return super().advance(command_id=command_id, issuance_id=issuance_id)
        digest, old = self._begin("ADVANCE", command_id, issuance_id, None)
        if old is not None:
            return old
        if not self._current["responses"]:
            return self._save(command_id, digest, {
                "status": "WAIT_FIRST_RESPONSE", "assessment_status": "ABSTAIN",
                "session_mode": MODE, **BOUNDARY})
        result = {"status": "AWAIT_TEACHER_REVIEW", "assessment_status": "ABSTAIN",
                  "assessment_basis": "PREVIEW_MANUAL_REVIEW_ONLY", "from_issuance_id": issuance_id,
                  "feedback": [], "next_assignment": None, "session_mode": MODE,
                  "automatic_assessment_enabled": False, "independent_mastery_evidence": False,
                  "message": "Ответы сохранены. Автоматическая оценка этого кандидата не включена; нужна проверка преподавателя.",
                  **BOUNDARY}
        self._current.update({"closed": True, "evidence_kind": "RAW_PREVIEW_RESPONSE_REQUIRES_REVIEW"})
        self._current = None
        self._journal.append({"kind": "PREVIEW_REVIEW_REQUESTED", "decision": deepcopy(result)})
        return self._save(command_id, digest, result)

    def teacher_snapshot(self):
        result = super().teacher_snapshot()
        result.update({"session_mode": MODE, "preview_exercise_id": self._preview_exercise_id,
                       "approved_catalog_used": False, "preview_assessment": "MANUAL_REVIEW_ONLY",
                       "candidate_mapping_applied": False})
        return result
