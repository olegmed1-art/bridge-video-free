"""Offline adapter for the existing teacher-evidence route; never a DB writer.

No environment variable enables this target. A local harness must explicitly
install an OfflineTournamentTeacherTarget on the ASGI app state. Deployment
entrypoints never do so. Imports of the experiment are lazy and fail closed.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException

TEACHER_KEY = "tournament-canon-test"
TEACHER_VERSION = "tournament-teacher-test-v2"
PROFILE = "SCHOOL_TOURNAMENT_CURRENT_V1"
RULES_SHA256 = "6f3489ce26bacfe6ce858a343519fe1e20179c857f9f25e5cafa0b937e2441e3"
SOURCE_VERSION = "tour-canon-test-20261004:" + RULES_SHA256
CONTRACT = "teacher-evidence-tournament-test-v1"
TARGET = "local-asgi:app:app:POST:/v1/ai/positions/{position_id}/teacher-evidence"
NAMESPACE = uuid5(NAMESPACE_URL, TARGET)


def position_uuid(context: dict) -> UUID:
    """Synthetic request identity; never an ai.decision_position database UUID."""
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return uuid5(NAMESPACE, SOURCE_VERSION + ":" + canonical)


def rule_binding_uuid(rule_id: str) -> UUID:
    return uuid5(NAMESPACE, SOURCE_VERSION + ":" + rule_id)


@dataclass(frozen=True)
class OfflineTournamentTeacherTarget:
    """Explicit in-process opt-in, pinned to this local source target/version."""
    source_version: str = SOURCE_VERSION

    def answer(self, position_id: UUID, evidence) -> dict:
        if evidence.model_extra:
            raise HTTPException(422, detail={"code":"TEST_UNRECOGNIZED_FIELDS"})
        if (self.source_version != SOURCE_VERSION or evidence.test_source_version != SOURCE_VERSION
                or evidence.teacher_key != TEACHER_KEY or evidence.teacher_version != TEACHER_VERSION
                or evidence.teacher_system != PROFILE):
            raise HTTPException(409, detail={"code":"TEST_SOURCE_OR_PROFILE_MISMATCH"})
        context = evidence.test_request
        if context is None:
            raise HTTPException(422, detail={"code":"TEST_REQUEST_REQUIRED"})
        # Callers supply inputs only; generated scores/actions cannot be injected.
        if (evidence.action is not None or evidence.confidence is not None
                or evidence.explanation is not None or evidence.candidate_scores or evidence.raw_output):
            raise HTTPException(422, detail={"code":"TEST_OUTPUT_FIELDS_FORBIDDEN"})
        if position_id != position_uuid(context):
            raise HTTPException(409, detail={"code":"SYNTHETIC_POSITION_BINDING_MISMATCH"})
        try:
            from experiments.tournament_teacher.consumer import RULES_PATH, decide
            if sha256(RULES_PATH.read_bytes()).hexdigest() != RULES_SHA256:
                raise HTTPException(409, detail={"code":"TEST_RULE_SNAPSHOT_MISMATCH"})
        except (ImportError, OSError) as exc:
            raise HTTPException(503, detail={"code":"TEST_RULE_SNAPSHOT_UNAVAILABLE"}) from exc
        result = decide(context, enabled=True)
        explanation = "\n".join(c["explanation"] for c in result["checks"])
        if not explanation:
            explanation = result["reason"]
        sources = [dict(rule_id=c["rule_id"], source_id=c["source_id"],
                        decision_ids=c["decision_ids"], rule_status=c["rule_status"],
                        sync_status=c["sync_status"], binding_uuid=str(rule_binding_uuid(c["rule_id"])),
                        database_rule_uuid=None, source_url=c["source_url"])
                   for c in result["checks"]]
        return dict(position_id=str(position_id), teacher_key=TEACHER_KEY,
                    teacher_version=TEACHER_VERSION, teacher_system=PROFILE,
                    action=result["action"], confidence=None, candidate_scores_json={},
                    explanation=explanation, status=result["status"],
                    source_version=SOURCE_VERSION, contract_version=CONTRACT,
                    target=TARGET, source_bindings=sources, raw_output_json=result,
                    persisted=False, queued=False, finalized=False,
                    formal_db_activation_asserted=False)
