from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from .db import connect

router = APIRouter(prefix="/v1/ai", tags=["bridge-ai-teacher"])


class TeacherEvidence(BaseModel):
    teacher_key: str
    teacher_version: str | None = None
    teacher_system: str | None = None
    action: str | None = None
    confidence: float | None = None
    candidate_scores: dict = Field(default_factory=dict)
    explanation: str | None = None
    raw_output: dict = Field(default_factory=dict)


@router.post("/positions/{position_id}/teacher-evidence")
def record_teacher_evidence(position_id: UUID, evidence: TeacherEvidence) -> dict:
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
