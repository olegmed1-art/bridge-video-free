from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .research_pipeline import (
    ResearchKind,
    ResearchStage,
    build_artifact_manifest,
    build_methodical_result,
    canonical_research_key,
    plan_execution,
    validate_compute_result,
)


_EXECUTABLE_KINDS = {ResearchKind.DDS3, ResearchKind.BEN, ResearchKind.WORLDS}
_TERMINAL_STAGES = {ResearchStage.COMPLETED, ResearchStage.FAILED, ResearchStage.CANCELLED}


def enqueue(
    conn: psycopg.Connection,
    *,
    kind: ResearchKind | str,
    payload: dict[str, Any],
    source: str = "CHAT",
    priority: int = 20,
) -> dict[str, Any]:
    normalized = ResearchKind(kind)
    if normalized not in _EXECUTABLE_KINDS:
        raise ValueError("durable executable enqueue supports DDS3/BEN/WORLDS")
    plan = plan_execution(normalized, payload)
    normalized_payload = dict(plan.assistant_lab_payload or {})
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM assistant_lab.enqueue_research_job(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                normalized.value,
                Jsonb(normalized_payload),
                canonical_research_key(normalized, normalized_payload),
                plan.assistant_lab_kind,
                Jsonb(normalized_payload),
                plan.idempotency_key,
                priority,
                source,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row or {})


def _set_stage(cur: psycopg.Cursor, research_id: str, stage: ResearchStage) -> None:
    cur.execute(
        "UPDATE assistant_lab.research_job SET stage=%s WHERE research_id=%s::uuid",
        (stage.value, research_id),
    )


def finalize(conn: psycopg.Connection, research_id: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT r.*, j.status child_status, j.result_json,
                      j.provenance_json child_provenance, j.error_text child_error,
                      j.completed_at child_completed_at
                 FROM assistant_lab.research_job r
                 LEFT JOIN assistant_lab.job j ON j.job_id=r.child_job_id
                WHERE r.research_id=%s::uuid
                FOR UPDATE OF r""",
            (research_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError("research job not found")
        current = ResearchStage(row["stage"])
        if current in _TERMINAL_STAGES:
            conn.commit()
            return dict(row)

        child_status = row["child_status"]
        if child_status in {"FAILED", "CANCELLED"}:
            terminal = ResearchStage.FAILED if child_status == "FAILED" else ResearchStage.CANCELLED
            cur.execute(
                """UPDATE assistant_lab.research_job
                      SET stage=%s, error_text=%s, completed_at=now()
                    WHERE research_id=%s::uuid
                    RETURNING *""",
                (
                    terminal.value,
                    row["child_error"] or f"child job ended with {child_status}",
                    research_id,
                ),
            )
            final = cur.fetchone()
            conn.commit()
            return dict(final or {})
        if child_status in {"QUEUED", "RUNNING"}:
            if child_status == "RUNNING" and current is ResearchStage.ACCEPTED:
                _set_stage(cur, research_id, ResearchStage.RUNNING)
                current = ResearchStage.RUNNING
            conn.commit()
            return {**dict(row), "stage": current.value}
        if child_status != "COMPLETED":
            raise RuntimeError(f"ResearchJob child status is unavailable or invalid: {child_status!r}")

        if current is ResearchStage.QUEUED:
            _set_stage(cur, research_id, ResearchStage.ACCEPTED)
            current = ResearchStage.ACCEPTED
        if current is ResearchStage.ACCEPTED:
            _set_stage(cur, research_id, ResearchStage.RUNNING)
            current = ResearchStage.RUNNING
        if current in {ResearchStage.RUNNING, ResearchStage.CHECKPOINTED}:
            _set_stage(cur, research_id, ResearchStage.VALIDATING)
            current = ResearchStage.VALIDATING
        if current is not ResearchStage.VALIDATING:
            raise RuntimeError(f"ResearchJob cannot validate from stage {current.value}")

        kind = ResearchKind(row["kind"])
        verified = validate_compute_result(kind, row["result_json"], row["payload_json"])
        provenance = dict(row["child_provenance"] or {})
        provenance.update(
            {
                "child_job_id": str(row["child_job_id"]),
                "child_completed_at": str(row["child_completed_at"] or ""),
            }
        )
        artifact = build_artifact_manifest(
            research_id=research_id,
            compute_result=verified,
            provenance=provenance,
        )
        methodical = build_methodical_result(
            research_id=research_id,
            artifact_manifest=artifact,
        )
        evidence_class = {
            ResearchKind.DDS3: "DDS",
            ResearchKind.BEN: verified.get("evidence_class"),
            ResearchKind.WORLDS: "WORLD_SAMPLE",
        }[kind]
        validation = {
            "validated": True,
            "kind": kind.value,
            "engine": verified.get("engine"),
            "fallback_used": verified.get("fallback_used"),
            "evidence_class": evidence_class,
        }
        cur.execute(
            """UPDATE assistant_lab.research_job
                  SET stage='COMPLETED', validation_json=%s,
                      provenance_json=provenance_json||%s, artifact_json=%s,
                      artifact_sha256=%s, methodical_json=%s,
                      completed_at=now(), error_text=NULL
                WHERE research_id=%s::uuid
                RETURNING *""",
            (
                Jsonb(validation),
                Jsonb(provenance),
                Jsonb(artifact),
                artifact["sha256"],
                Jsonb(methodical),
                research_id,
            ),
        )
        final = cur.fetchone()
    conn.commit()
    return dict(final or {})
