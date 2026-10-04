from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb

from .db import connect

router = APIRouter(prefix="/v1/ai", tags=["bridge-ai-teacher"])


class TeacherEvidence(BaseModel):
    # Retain unknown fields for dispatch inspection. Legacy SQL still ignores
    # them, preserving its previous behavior; test envelopes reject extras.
    model_config = ConfigDict(extra="allow")
    teacher_key: str
    teacher_version: str | None = None
    teacher_system: str | None = None
    action: str | None = None
    confidence: float | None = None
    candidate_scores: dict = Field(default_factory=dict)
    explanation: str | None = None
    raw_output: dict = Field(default_factory=dict)
    # Optional, explicit offline request. Never forwarded to the legacy writer.
    test_request: dict | None = None
    test_source_version: str | None = None
    canon_request: dict | None = None


@router.post("/positions/{position_id}/teacher-evidence")
def record_teacher_evidence(position_id: UUID, evidence: TeacherEvidence, request: Request) -> dict:
    if ("canon_request" in evidence.model_fields_set
            or evidence.teacher_key.startswith("school-tournament-shape")
            or (evidence.teacher_version or "").startswith("tour-1nt-shape-")
            or any(key.lower().replace("_", "").startswith("canon") for key in (evidence.model_extra or {}))):
        from .tournament_teacher import answer
        return answer(position_id, evidence)
    if ({"test_request", "test_source_version"} & evidence.model_fields_set
            or evidence.teacher_key.startswith("tournament-canon-test")
            or (evidence.teacher_version or "").startswith("tournament-teacher-test-")
            or any(key.lower().replace("_", "").startswith("test")
                   for key in (evidence.model_extra or {}))):
        from .tournament_teacher_test_adapter import OfflineTournamentTeacherTarget
        target = getattr(request.app.state, "tournament_teacher_test_target", None)
        if type(target) is not OfflineTournamentTeacherTarget:
            raise HTTPException(404, detail={"code":"TOURNAMENT_TEACHER_TEST_DISABLED"})
        return target.answer(position_id, evidence)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM ai.decision_position WHERE position_id=%s", (position_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="decision position not found")
        cur.execute(
            """
            INSERT INTO ai.teacher_output (
                position_id, teacher_key, teacher_version, teacher_system,
                action, confidence, candidate_scores_json, explanation, raw_output_json
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
            """,
            (
                position_id,
                evidence.teacher_key,
                evidence.teacher_version,
                evidence.teacher_system,
                evidence.action,
                evidence.confidence,
                Jsonb(evidence.candidate_scores),
                evidence.explanation,
                Jsonb(evidence.raw_output),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row
