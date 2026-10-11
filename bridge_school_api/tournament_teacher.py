"""Read-only, SQL-gated assessment of the two formalized 1NT response meanings.

No scheduler, worker, activation side effect, L1 change, or recommendation policy.
The stored position supplies school and hand; callers cannot override either.
"""
from fastapi import APIRouter, HTTPException
from pydantic import ConfigDict
from uuid import UUID

from .bidding_catalog_reader import read_school_catalog
from .db import connect
from .ai_teacher import TeacherEvidence as LegacyTeacherEvidence, record_teacher_evidence
from .tournament_shape_core import (
    KEY, VERSION, PROFILE, SCOPE, CANARY_KEY, CANARY_HAND,
    PAYLOAD_HASHES as PAYLOAD_HASHES, digest as digest, evaluate,
)

router = APIRouter(prefix="/v1/ai",tags=["bridge-ai-teacher"])


class TeacherEvidence(LegacyTeacherEvidence):
    model_config = ConfigDict(extra="allow")
    canon_request: dict | None = None


@router.post("/positions/{position_id}/teacher-evidence")
def assess_or_record(position_id: UUID, evidence: TeacherEvidence):
    if ("canon_request" in evidence.model_fields_set
            or evidence.teacher_key.startswith("school-tournament-shape")
            or (evidence.teacher_version or "").startswith("tour-1nt-shape-")
            or any(key.lower().replace("_", "").startswith("canon") for key in (evidence.model_extra or {}))):
        return answer(position_id,evidence)
    return record_teacher_evidence(position_id,evidence)


def answer(position_id, evidence):
    request = evidence.canon_request
    if (evidence.model_extra or not isinstance(request,dict)
            or set(request) != {"task","scope_key","version","proposed_call"}
            or request.get("task") != "assess_call"
            or {"test_request","test_source_version"} & evidence.model_fields_set
            or evidence.action is not None or evidence.confidence is not None
            or evidence.explanation is not None or evidence.candidate_scores or evidence.raw_output):
        raise HTTPException(422,detail={"code":"CANON_REQUEST_INPUT_ONLY"})
    if (evidence.teacher_key != KEY or evidence.teacher_version != VERSION
            or evidence.teacher_system != PROFILE or request["scope_key"] != SCOPE
            or request["version"] != VERSION):
        raise HTTPException(409,detail={"code":"CANON_SCOPE_OR_VERSION_MISMATCH"})
    if not isinstance(request["proposed_call"],str):
        raise HTTPException(422,detail={"code":"CANON_CALL_REQUIRED"})
    with connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        position = conn.execute("""SELECT p.position_id,p.school_id,p.stable_key,p.source_id,p.input_status,p.decision_type,p.system_us,
            p.dealer,p.seat,p.auction_json,p.hand_pbn,p.cards_played_json,p.dummy_pbn,s.status AS school_status
            FROM ai.decision_position p JOIN public.school s USING(school_id)
            WHERE p.position_id=%s""",(position_id,)).fetchone()
        if position is None:
            raise HTTPException(404,detail={"code":"POSITION_NOT_FOUND"})
        if (position["stable_key"] != CANARY_KEY or position["hand_pbn"] != CANARY_HAND
                or position["dealer"] != "N" or position["seat"] != "S"
                or position["source_id"] is None):
            result = evaluate(position,request["proposed_call"],[])
            result["reason"] = "PILOT_POSITION_ONLY"
            return dict(position_id=str(position_id),teacher_key=KEY,**result)
        rows = read_school_catalog(conn,position["school_id"],SCOPE)
        result = evaluate(position,request["proposed_call"],rows)
        if result["source_bindings"]:
            binding = result["source_bindings"][0]
            sources = conn.execute("""SELECT s.source_id,s.canonical_locator,kvs.source_locator
                FROM public.knowledge_version_source kvs JOIN public.source s USING(source_id)
                WHERE kvs.knowledge_version_id=%s AND s.school_id=%s AND s.status='active'""",
                (binding["knowledge_version_id"],position["school_id"])).fetchall()
            matches = [s for s in sources if s["source_id"]==position["source_id"]
                and s["canonical_locator"]==binding["source_url"]
                and isinstance(s["source_locator"],dict)
                and s["source_locator"].get("original_excerpt")==binding["original_excerpt"]
                and s["source_locator"].get("teacher_excerpt")==binding["teacher_excerpt"]]
            if len(matches) != 1:
                result.update(status="ABSTAIN",reason="SOURCE_BINDING_MISMATCH",source_bindings=[])
                result.pop("explanation",None)
                result.pop("forcing_status",None)
            else:
                binding["database_source_id"] = str(matches[0]["source_id"])
    return dict(position_id=str(position_id),teacher_key=KEY,**result)
