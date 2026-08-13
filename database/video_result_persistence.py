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

    report_asset_id = _stable_uuid("asset", "sha256", report_sha)
    report_location_id = _stable_uuid("asset-location", "google-drive", report_drive_id)
    artifact_id = _stable_uuid("artifact", job_id, algorithm_revision, report_sha)
    artifact_version_id = _stable_uuid("artifact-version", artifact_id, 1, report_sha)
    analysis_output_id = _stable_uuid("analysis-output", analysis_run_id, artifact_version_id)

    counts = {
        "transcript_segments": len(transcript),
        "semantic_episodes": len(master.get("episodes") or []),
        "analysis_outputs": 1,
    }
    checkpoint = {
        "job_id": job_id,
        "source_drive_id": source_drive_id,
        "report_drive_id": report_drive_id,
        "database_persistence": "complete",
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
                     completed_at, run_status, parameters_snapshot, qc_summary, checkpoint)
                VALUES (%s, %s, 'bridge-video-master-analysis', %s,
                        now(), 'success', %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    analysis_run_id,
                    school_id,
                    algorithm_revision,
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
                VALUES (%s, %s, 'lesson_master_analysis_pdf', %s, 'active')
                ON CONFLICT DO NOTHING
                """,
                (artifact_id, school_id, report_name),
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
        "artifact_version_id": str(artifact_version_id),
    }
