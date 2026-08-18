#!/usr/bin/env python3
"""Persist one completed Bridge Video result into the Neon school database."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from database.runtime_worker_preflight import normalize_dsn

SCHOOL_STABLE_NAME = "Школа спортивного бриджа"


def _stable_uuid(kind: str, *parts: object) -> uuid.UUID:
    seed = "|".join(str(x) for x in parts)
    return uuid.uuid5(uuid.NAMESPACE_URL, f"bridge-school:{kind}:{seed}")


def _canonical_json_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require(mapping: dict[str, Any], key: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "":
        raise ValueError(f"missing required result field: {key}")
    return value


def _transcript_kind(master: dict[str, Any]) -> tuple[str, str | None]:
    transcript_qc = ((master.get("technical_qc") or {}).get("transcript") or {})
    primary = str(transcript_qc.get("primarySource") or "").strip().lower()
    semantic = master.get("content_quality") or {}
    if int(semantic.get("semantic_auto_corrections") or 0) > 0:
        return "corrected", transcript_qc.get("language")
    if "zoom" in primary:
        return "zoom_vtt", transcript_qc.get("language")
    return "raw_asr", transcript_qc.get("language")



def _evidence_id(transcript_id: uuid.UUID, worker_ref: object) -> uuid.UUID:
    return _stable_uuid("evidence", transcript_id, str(worker_ref))


def _valid_evidence_refs(master: dict[str, Any]) -> set[str]:
    return {
        str(segment.get("segment_id"))
        for segment in (master.get("transcript") or [])
        if segment.get("segment_id")
    }


def _evidence_ids(master: dict[str, Any], transcript_id: uuid.UUID, refs) -> list[uuid.UUID]:
    valid = _valid_evidence_refs(master)
    return [
        _evidence_id(transcript_id, ref)
        for ref in dict.fromkeys(str(value) for value in (refs or []) if value)
        if ref in valid
    ]


def _evidence_rows(
    master: dict[str, Any],
    transcript_id: uuid.UUID,
    source_id: uuid.UUID,
    source_asset_id: uuid.UUID,
):
    """Create one immutable evidence entity for each referenced transcript segment."""
    requested = set()
    for item in master.get("episodes") or []:
        requested.update(str(ref) for ref in item.get("evidence") or [] if ref)
    for item in master.get("learning_interactions") or []:
        requested.update(str(ref) for ref in item.get("evidence") or [] if ref)
    for item in master.get("decisions") or []:
        requested.update(str(ref) for ref in item.get("evidence") or [] if ref)
    rows = []
    for segment in master.get("transcript") or []:
        worker_ref = str(segment.get("segment_id") or "")
        if not worker_ref or worker_ref not in requested:
            continue
        rows.append((
            _evidence_id(transcript_id, worker_ref),
            source_id,
            source_asset_id,
            {
                "transcript_id": str(transcript_id),
                "worker_segment_id": worker_ref,
                "source": segment.get("source"),
            },
            segment.get("start"),
            segment.get("end"),
            "LOW" if segment.get("unreliable") else "UNKNOWN",
            "unreliable" if segment.get("unreliable") else "accepted",
        ))
    return rows


def _domain_rows(
    master: dict[str, Any],
    analysis_run_id: uuid.UUID,
    transcript_id: uuid.UUID | None = None,
):
    """Build deterministic, role-neutral lesson rows without inventing identities."""
    interaction_id = _stable_uuid("learning-interaction", analysis_run_id)
    transcript_id = transcript_id or _stable_uuid("transcript-for-domain-test", analysis_run_id)
    episodes = list(master.get("episodes") or [])
    cycles = list(master.get("learning_interactions") or [])
    decisions = list(master.get("decisions") or [])
    episode_rows = []
    sequence_no = 0
    for item in episodes:
        sequence_no += 1
        worker_id = str(item.get("episode_id") or sequence_no)
        episode_rows.append((
            _stable_uuid("episode", interaction_id, "semantic", worker_id),
            interaction_id,
            sequence_no,
            str(item.get("type") or "semantic_episode"),
            item.get("start"),
            item.get("end"),
            str(item.get("summary_text") or "")[:4000] or None,
            _evidence_ids(master, transcript_id, item.get("evidence") or []),
            {
                "record_kind": "semantic_episode",
                "worker_episode_id": item.get("episode_id"),
                "terms": item.get("terms") or [],
                "confidence": item.get("confidence"),
                "evidence_refs": item.get("evidence") or [],
                "visual_evidence_refs": item.get("visual_evidence") or [],
                "statement_type": item.get("statement_type"),
            },
        ))
    for item in cycles:
        sequence_no += 1
        worker_id = str(item.get("cycle_id") or sequence_no)
        sequence = item.get("role_neutral_sequence") or {}
        summary = (
            sequence.get("trigger_context")
            or item.get("task_or_trigger")
            or item.get("student_action")
            or ""
        )
        episode_rows.append((
            _stable_uuid("episode", interaction_id, "learning-cycle", worker_id),
            interaction_id,
            sequence_no,
            (
                "learning_cycle"
                if item.get("verification_status") == "VERIFIED_ROLE_NEUTRAL_SEQUENCE"
                else "learning_cycle_candidate"
            ),
            None,
            None,
            str(summary)[:4000] or None,
            _evidence_ids(master, transcript_id, item.get("evidence") or []),
            {
                "record_kind": "learning_cycle",
                "worker_cycle_id": item.get("cycle_id"),
                "focus_episode_id": item.get("focus_episode_id"),
                "attribution_status": item.get("attribution_status"),
                "content_completeness": item.get("content_completeness"),
                "verification_status": item.get("verification_status"),
                "role_neutral_sequence": sequence,
                "student_action": item.get("student_action"),
                "teacher_intervention": item.get("teacher_intervention"),
                "student_response": item.get("student_response"),
                "outcome": item.get("outcome"),
                "evidence_refs": item.get("evidence") or [],
            },
        ))
    decision_rows = []
    for sequence_no, item in enumerate(decisions, 1):
        worker_id = str(item.get("decision_id") or sequence_no)
        action = item.get("action_taken")
        if not isinstance(action, dict):
            action = {"status": "text_only", "text": action}
        available = item.get("available_information")
        if not isinstance(available, dict):
            available = {"observed_context": item.get("observed_context")}
        cues = action.get("cues") or item.get("decision_cues") or []
        decision_rows.append((
            _stable_uuid("decision", interaction_id, worker_id),
            interaction_id,
            str(cues[0] if cues else "bridge_action_candidate"),
            sequence_no,
            {
                **action,
                "worker_decision_id": item.get("decision_id"),
                "actor_attribution_status": item.get("actor_attribution_status"),
                "content_completeness": item.get("content_completeness"),
                "alternatives": item.get("alternatives") or [],
            },
            available,
            item.get("reasoning"),
            _evidence_ids(master, transcript_id, item.get("evidence") or []),
        ))
    return interaction_id, episode_rows, decision_rows, len(episodes), len(cycles)

def persist_video_result(
    raw_dsn: str,
    master: dict[str, Any],
    done: dict[str, Any],
    *,
    rollback: bool = False,
) -> dict[str, str | int | bool]:
    """Persist a completed worker result. The operation is transactional and idempotent."""
    dsn = normalize_dsn(raw_dsn)
    if not dsn:
        raise ValueError("BRIDGE_WORKER_DATABASE_URL is not configured")

    job_id = str(_require(master, "job_id"))
    source = dict(_require(master, "source"))
    source_drive_id = str(_require(source, "driveId"))
    source_sha = str(_require(source, "sha256")).lower()
    source_name = str(_require(source, "name"))
    source_mime = str(source.get("mimeType") or "video/*")
    source_size = int(_require(source, "sizeBytes"))
    source_duration = float(source.get("durationSeconds") or 0)
    source_parent = str(source.get("parentFolderId") or "")

    master_pdf = dict(_require(done, "masterPdf"))
    report_drive_id = str(_require(master_pdf, "driveId"))
    report_sha = str(_require(master_pdf, "sha256")).lower()
    report_name = str(_require(master_pdf, "name"))
    report_size = int(master_pdf.get("sizeBytes") or 0)

    transcript = list(master.get("transcript") or [])
    transcript_type, language = _transcript_kind(master)
    algorithm_version = str(master.get("algorithmVersion") or "3.1 FREE")
    algorithm_revision = str(master.get("algorithmRevision") or algorithm_version)
    schema_version = str(master.get("schemaVersion") or "")
    transcript_digest = _canonical_json_digest(
        [
            {
                "start": s.get("start"),
                "end": s.get("end"),
                "speaker": s.get("speaker"),
                "text": s.get("text"),
                "source": s.get("source"),
                "unreliable": bool(s.get("unreliable")),
            }
            for s in transcript
        ]
    )

    ingestion_run_id = _stable_uuid("ingestion-run", job_id)
    ingestion_item_id = _stable_uuid("ingestion-item", job_id, source_drive_id, source_sha)
    source_id = _stable_uuid("source", "google-drive", source_drive_id)
    source_asset_id = _stable_uuid("asset", "sha256", source_sha)
    source_location_id = _stable_uuid("asset-location", "google-drive", source_drive_id)
    transcript_id = _stable_uuid(
        "transcript", source_drive_id, algorithm_revision, transcript_type, transcript_digest
    )
    analysis_run_id = _stable_uuid(
        "analysis-run", job_id, algorithm_revision, transcript_digest, report_sha
    )
    analysis_input_id = _stable_uuid("analysis-input", analysis_run_id, source_asset_id)
    (
        interaction_id,
        episode_rows,
        decision_rows,
        semantic_episode_count,
        learning_cycle_count,
    ) = _domain_rows(master, analysis_run_id, transcript_id)
    evidence_rows = _evidence_rows(master, transcript_id, source_id, source_asset_id)
    evidence_links = []
    for row in episode_rows:
        for evidence_id in row[-2]:
            evidence_links.append((
                _stable_uuid("evidence-link", evidence_id, row[0], "episode", "supports"),
                evidence_id,
                row[0],
                "episode",
                "supports",
            ))
    for row in decision_rows:
        for evidence_id in row[-1]:
            evidence_links.append((
                _stable_uuid("evidence-link", evidence_id, row[0], "decision", "supports"),
                evidence_id,
                row[0],
                "decision",
                "supports",
            ))

    report_asset_id = _stable_uuid("asset", "sha256", report_sha)
    report_location_id = _stable_uuid("asset-location", "google-drive", report_drive_id)
    artifact_id = _stable_uuid("artifact", job_id, algorithm_revision, report_sha)
    artifact_version_id = _stable_uuid("artifact-version", artifact_id, 1, report_sha)
    analysis_output_id = _stable_uuid("analysis-output", analysis_run_id, artifact_version_id)
    changeset_id = _stable_uuid("changeset", "video-result-recorded", analysis_run_id)
    command_id = _stable_uuid("command", "video-result-recorded", analysis_run_id)
    technical_event_id = _stable_uuid("event", "video-result-recorded", analysis_run_id)

    counts = {
        "transcript_segments": len(transcript),
        "semantic_episodes": semantic_episode_count,
        "learning_cycles": learning_cycle_count,
        "decisions": len(decision_rows),
        "evidence": len(evidence_rows),
        "evidence_links": len(evidence_links),
        "analysis_outputs": 1,
    }
    checkpoint = {
        "job_id": job_id,
        "source_drive_id": source_drive_id,
        "report_drive_id": report_drive_id,
        "database_persistence": "technically_recorded",
    }
    transcript_provenance = {
        "job_id": job_id,
        "source_drive_id": source_drive_id,
        "primary_source": ((master.get("technical_qc") or {}).get("transcript") or {}).get(
            "primarySource"
        ),
        "algorithm_version": algorithm_version,
        "algorithm_revision": algorithm_revision,
        "semantic_qc_revision": (master.get("content_quality") or {}).get(
            "semantic_qc_revision"
        ),
        "semantic_qc_status": (master.get("content_quality") or {}).get(
            "semantic_qc_status"
        ),
    }
    analysis_parameters = {
        "job_id": job_id,
        "source_drive_id": source_drive_id,
        "transcript_type": transcript_type,
        "schema_version": schema_version,
    }
    analysis_qc = {
        "content_quality": master.get("content_quality") or {},
        "transcript": ((master.get("technical_qc") or {}).get("transcript") or {}),
        "visual": ((master.get("technical_qc") or {}).get("visual") or {}),
    }
    artifact_provenance = {
        "job_id": job_id,
        "report_drive_id": report_drive_id,
        "report_sha256": report_sha,
        "master_json_embedded": bool(master_pdf.get("masterJsonEmbedded")),
        "master_json_sha256": master_pdf.get("masterJsonSha256"),
        "algorithm_version": algorithm_version,
        "algorithm_revision": algorithm_revision,
    }
    technical_event_payload = {
        "analysis_run_id": str(analysis_run_id),
        "job_id": job_id,
        "algorithm_revision": algorithm_revision,
        "source_sha256": source_sha,
        "report_sha256": report_sha,
        "transcript_sha256": transcript_digest,
        "technical_record_status": "rolled_back" if rollback else "recorded",
        "quality_confirmation_status": "pending",
        "publication_authorization_status": "blocked",
    }
    technical_event_hash = _canonical_json_digest(technical_event_payload)
    candidate_requires_meta = algorithm_revision == "3.1-free-r25.12-meta"

    with psycopg.connect(
        dsn,
        connect_timeout=10,
        application_name="bridge-video-result-persistence",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT school_id FROM public.school WHERE stable_name = %s",
                (SCHOOL_STABLE_NAME,),
            )
            rows = cur.fetchall()
            if len(rows) != 1:
                raise RuntimeError("expected exactly one bridge school registry row")
            school_id = rows[0][0]

            cur.execute(
                """
                SELECT av.algorithm_version_id
                  FROM public.algorithm a
                  JOIN public.algorithm_version av ON av.algorithm_id=a.algorithm_id
                 WHERE a.school_id=%s
                   AND a.stable_key='bridge-video-master-analysis'
                   AND av.version_label=%s
                """,
                (school_id, algorithm_revision),
            )
            version_rows = cur.fetchall()
            if len(version_rows) != 1 or version_rows[0][0] is None:
                raise RuntimeError(
                    f"registered algorithm_version_id required for {algorithm_revision}"
                )
            algorithm_version_id = version_rows[0][0]

            cur.execute(
                """
                INSERT INTO public.changeset
                    (changeset_id, command_id, school_id, status, correlation_id)
                VALUES (%s, %s, %s, 'started', %s)
                ON CONFLICT (school_id, command_id) DO NOTHING
                """,
                (changeset_id, command_id, school_id, changeset_id),
            )

            cur.execute(
                """
                INSERT INTO public.ingestion_run
                    (ingestion_run_id, school_id, source_system, completed_at, checkpoint,
                     schema_version, status, counts, error_manifest)
                VALUES (%s, %s, %s, now(), %s, %s, 'completed', %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    ingestion_run_id,
                    school_id,
                    "google_drive_bridge_video_3_1_free",
                    Jsonb(checkpoint),
                    schema_version or None,
                    Jsonb(counts),
                    Jsonb([]),
                ),
            )

            cur.execute(
                """
                INSERT INTO public.learning_interaction
                    (interaction_id, school_id, interaction_type, channel, status)
                VALUES (%s, %s, 'recorded_lesson_analysis', 'recorded_video', 'completed')
                ON CONFLICT DO NOTHING
                """,
                (interaction_id, school_id),
            )
            if episode_rows:
                cur.executemany(
                    """
                    INSERT INTO public.episode
                        (episode_id, interaction_id, sequence_no, episode_type,
                         start_offset_seconds, end_offset_seconds, summary,
                         evidence_ids, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        row[:-1] + (Jsonb(row[-1]),)
                        for row in episode_rows
                    ],
                )
            if decision_rows:
                cur.executemany(
                    """
                    INSERT INTO public.decision
                        (decision_id, school_id, interaction_id, decision_type,
                         sequence_no, action_taken, available_information,
                         stated_reasoning, evidence_ids)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            decision_id,
                            school_id,
                            row_interaction_id,
                            decision_type,
                            sequence_no,
                            Jsonb(action_taken),
                            Jsonb(available_information),
                            stated_reasoning,
                            row_evidence_ids,
                        )
                        for (
                            decision_id,
                            row_interaction_id,
                            decision_type,
                            sequence_no,
                            action_taken,
                            available_information,
                            stated_reasoning,
                            row_evidence_ids,
                        ) in decision_rows
                    ],
                )

            cur.execute(
                """
                INSERT INTO public.asset
                    (asset_id, school_id, asset_type, mime_type, byte_size,
                     checksum_algorithm, checksum_value, immutable_flag)
                VALUES (%s, %s, 'video_recording', %s, %s, 'sha256', %s, true)
                ON CONFLICT DO NOTHING
                """,
                (source_asset_id, school_id, source_mime, source_size, source_sha),
            )
            cur.execute(
                """
                INSERT INTO public.media_asset
                    (media_asset_id, school_id, duration_seconds, media_metadata)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    source_asset_id,
                    school_id,
                    source_duration,
                    Jsonb(
                        {
                            "drive_file_id": source_drive_id,
                            "parent_folder_id": source_parent or None,
                            "original_name": source_name,
                        }
                    ),
                ),
            )
            cur.execute(
                """
                INSERT INTO public.asset_location
                    (asset_location_id, asset_id, storage_provider, locator,
                     availability_status, last_verified_at, verification_method)
                VALUES (%s, %s, 'google_drive', %s, 'available', now(), 'bridge_video_worker')
                ON CONFLICT DO NOTHING
                """,
                (source_location_id, source_asset_id, f"gdrive:file:{source_drive_id}"),
            )

            cur.execute(
                """
                INSERT INTO public.source
                    (source_id, school_id, source_type, title, canonical_locator, trust_class)
                VALUES (%s, %s, 'lesson_video', %s, %s, 'primary_source')
                ON CONFLICT DO NOTHING
                """,
                (
                    source_id,
                    school_id,
                    source_name,
                    f"gdrive:file:{source_drive_id}",
                ),
            )
            cur.execute(
                """
                INSERT INTO public.source_asset (source_id, asset_id, relation_type)
                VALUES (%s, %s, 'embodies')
                ON CONFLICT DO NOTHING
                """,
                (source_id, source_asset_id),
            )
            cur.execute(
                """
                INSERT INTO public.ingestion_item
                    (ingestion_item_id, ingestion_run_id, native_namespace, native_key,
                     payload_hash, status, result_ref)
                VALUES (%s, %s, 'google_drive_file', %s, %s, 'processed', %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    ingestion_item_id,
                    ingestion_run_id,
                    source_drive_id,
                    source_sha,
                    source_asset_id,
                ),
            )

            cur.execute(
                """
                INSERT INTO public.transcript
                    (transcript_id, school_id, media_asset_id, transcript_type,
                     language, asr_model_version, source_id, provenance, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'staging')
                ON CONFLICT DO NOTHING
                """,
                (
                    transcript_id,
                    school_id,
                    source_asset_id,
                    transcript_type,
                    language,
                    "faster-whisper/" + str(master.get("whisperModel"))
                    if master.get("whisperModel")
                    else None,
                    source_id,
                    Jsonb(transcript_provenance),
                ),
            )

            segment_rows = []
            for sequence_no, segment in enumerate(transcript, 1):
                segment_id = _stable_uuid(
                    "transcript-segment",
                    transcript_id,
                    sequence_no,
                    segment.get("start"),
                    segment.get("end"),
                    segment.get("text"),
                )
                segment_rows.append(
                    (
                        segment_id,
                        transcript_id,
                        sequence_no,
                        segment.get("start"),
                        segment.get("end"),
                        segment.get("speaker"),
                        str(segment.get("text") or ""),
                        "LOW" if segment.get("unreliable") else "UNKNOWN",
                        Jsonb(
                            {
                                "worker_segment_id": segment.get("segment_id"),
                                "source": segment.get("source"),
                                "unreliable": bool(segment.get("unreliable")),
                                "semantic_corrections": segment.get("semantic_corrections") or [],
                                "raw_text": segment.get("raw_text"),
                            }
                        ),
                    )
                )
            if segment_rows:
                cur.executemany(
                    """
                    INSERT INTO public.transcript_segment
                        (transcript_segment_id, transcript_id, sequence_no,
                         start_seconds, end_seconds, speaker_label, text,
                         confidence_class, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    segment_rows,
                )

            cur.execute(
                """
                INSERT INTO public.analysis_run
                    (analysis_run_id, school_id, algorithm_key, algorithm_version,
                     algorithm_version_id, completed_at, run_status,
                     parameters_snapshot, qc_summary, checkpoint,
                     technical_record_status, quality_confirmation_status,
                     publication_authorization_status)
                VALUES (%s, %s, 'bridge-video-master-analysis', %s, %s,
                        CASE WHEN %s THEN NULL ELSE now() END,
                        CASE WHEN %s THEN 'running' ELSE 'success' END,
                        %s, %s, %s, 'recorded', 'pending', 'blocked')
                ON CONFLICT DO NOTHING
                """,
                (
                    analysis_run_id,
                    school_id,
                    algorithm_revision,
                    algorithm_version_id,
                    candidate_requires_meta,
                    candidate_requires_meta,
                    Jsonb(analysis_parameters),
                    Jsonb(analysis_qc),
                    Jsonb(checkpoint),
                ),
            )
            cur.execute(
                """
                INSERT INTO public.analysis_run_input
                    (analysis_run_input_id, analysis_run_id, asset_id, input_role, metadata)
                VALUES (%s, %s, %s, 'primary', %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    analysis_input_id,
                    analysis_run_id,
                    source_asset_id,
                    Jsonb({"source_id": str(source_id), "transcript_id": str(transcript_id)}),
                ),
            )
            if evidence_rows:
                cur.executemany(
                    """
                    INSERT INTO public.evidence
                        (evidence_id, school_id, evidence_type, source_id, asset_id,
                         locator, start_seconds, end_seconds, confidence_class,
                         quality_status)
                    VALUES (%s, %s, 'transcript_segment', %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            evidence_id,
                            school_id,
                            row_source_id,
                            row_asset_id,
                            Jsonb(locator),
                            start_seconds,
                            end_seconds,
                            confidence_class,
                            quality_status,
                        )
                        for (
                            evidence_id,
                            row_source_id,
                            row_asset_id,
                            locator,
                            start_seconds,
                            end_seconds,
                            confidence_class,
                            quality_status,
                        ) in evidence_rows
                    ],
                )
            if evidence_links:
                cur.executemany(
                    """
                    INSERT INTO public.evidence_link
                        (evidence_link_id, evidence_id, target_entity_id,
                         target_entity_type, relation_type, weight, analysis_run_id)
                    VALUES (%s, %s, %s, %s, %s, 1, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [row + (analysis_run_id,) for row in evidence_links],
                )

            cur.execute(
                """
                INSERT INTO public.asset
                    (asset_id, school_id, asset_type, mime_type, byte_size,
                     checksum_algorithm, checksum_value, immutable_flag)
                VALUES (%s, %s, 'analysis_pdf', 'application/pdf', %s,
                        'sha256', %s, true)
                ON CONFLICT DO NOTHING
                """,
                (report_asset_id, school_id, report_size, report_sha),
            )
            cur.execute(
                """
                INSERT INTO public.asset_location
                    (asset_location_id, asset_id, storage_provider, locator,
                     availability_status, last_verified_at, verification_method)
                VALUES (%s, %s, 'google_drive', %s, 'available', now(), 'bridge_video_worker')
                ON CONFLICT DO NOTHING
                """,
                (report_location_id, report_asset_id, f"gdrive:file:{report_drive_id}"),
            )
            cur.execute(
                """
                INSERT INTO public.artifact
                    (artifact_id, school_id, artifact_type, title, status)
                VALUES (%s, %s, 'lesson_master_analysis_pdf', %s,
                        CASE WHEN %s THEN 'staging' ELSE 'active' END)
                ON CONFLICT DO NOTHING
                """,
                (artifact_id, school_id, report_name, candidate_requires_meta),
            )
            cur.execute(
                """
                INSERT INTO public.artifact_version
                    (artifact_version_id, artifact_id, version_no, version_label,
                     asset_id, generated_by_analysis_run_id, generation_method,
                     provenance, status)
                VALUES (%s, %s, 1, %s, %s, %s, %s, %s, 'candidate')
                ON CONFLICT DO NOTHING
                """,
                (
                    artifact_version_id,
                    artifact_id,
                    algorithm_revision,
                    report_asset_id,
                    analysis_run_id,
                    "bridge-video-3.1-free",
                    Jsonb(artifact_provenance),
                ),
            )
            cur.execute(
                """
                INSERT INTO public.analysis_run_output
                    (analysis_run_output_id, analysis_run_id, output_entity_id,
                     output_entity_type, artifact_version_id, output_role, status)
                VALUES (%s, %s, %s, 'artifact', %s, 'derived', 'staging')
                ON CONFLICT DO NOTHING
                """,
                (
                    analysis_output_id,
                    analysis_run_id,
                    artifact_id,
                    artifact_version_id,
                ),
            )

            cur.execute(
                "SELECT payload_hash FROM public.domain_event WHERE event_id=%s",
                (technical_event_id,),
            )
            existing_event = cur.fetchone()
            if existing_event and existing_event[0] != technical_event_hash:
                raise RuntimeError(
                    "technical persistence idempotency key reused with different payload"
                )
            cur.execute(
                """
                INSERT INTO public.domain_event(
                    event_id, school_id, partition_key, event_type,
                    aggregate_id, aggregate_type, aggregate_version,
                    changeset_id, correlation_id, idempotency_namespace,
                    idempotency_key, payload_hash, payload)
                VALUES (
                    %s, %s, 'bridge-video-technical', 'BridgeVideoResultRecorded',
                    %s, 'analysis_run', 1, %s, %s,
                    'bridge-video-result-persistence', %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    technical_event_id,
                    school_id,
                    analysis_run_id,
                    changeset_id,
                    changeset_id,
                    str(analysis_run_id),
                    technical_event_hash,
                    Jsonb(technical_event_payload),
                ),
            )
            cur.execute(
                """
                INSERT INTO public.outbox_message(changeset_id,event_id)
                VALUES (%s,%s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (changeset_id, technical_event_id),
            )
            cur.execute(
                """
                UPDATE public.changeset
                   SET status='committed', committed_at=COALESCE(committed_at,now())
                 WHERE changeset_id=%s
                   AND status IN ('started','committed')
                """,
                (changeset_id,),
            )

        if rollback:
            conn.rollback()
        else:
            conn.commit()

    return {
        "persisted": not rollback,
        "rolled_back": rollback,
        "segments": len(transcript),
        "source_id": str(source_id),
        "source_asset_id": str(source_asset_id),
        "transcript_id": str(transcript_id),
        "analysis_run_id": str(analysis_run_id),
        "algorithm_version_id": str(algorithm_version_id),
        "changeset_id": str(changeset_id),
        "technical_record_status": "recorded",
        "quality_confirmation_status": "pending",
        "publication_authorization_status": "blocked",
        "artifact_version_id": str(artifact_version_id),
        "learning_interaction_id": str(interaction_id),
        "episodes": semantic_episode_count,
        "learning_cycles": learning_cycle_count,
        "decisions": len(decision_rows),
        "evidence": len(evidence_rows),
        "evidence_links": len(evidence_links),
    }
