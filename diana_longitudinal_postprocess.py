#!/usr/bin/env python3
"""Create quality-first longitudinal artifacts from one completed lesson video.

The postprocessor reuses the embedded master analysis.  It does not download or
transcribe the original video, does not alter MASTER media and does not activate
School canon, curriculum, methodology or a production student profile.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

import fitz
import requests

import run_drive_3_1_free as io
from diana_longitudinal_quality_v2 import (
    QUALITY_METHOD_VERSION,
    QUALITY_SCHEMA_VERSION,
    build_quality_layer,
)
from run_drive_3_1_free_oidc import user_oauth_token

DRIVE = "https://www.googleapis.com/drive/v3"
SCHEMA = "diana-longitudinal-extraction"
SCHEMA_VERSION = 2

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stable_id(kind: str, *parts: object) -> str:
    seed = "|".join(str(value) for value in parts)
    return f"{kind}_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _read_json_file(token: str, item: Mapping[str, Any]) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory(prefix="diana-longitudinal-json-") as td:
        path = Path(td) / "payload.json"
        io.download(token, item["id"], path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


def _latest_done(token: str, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    name = f"AI_DONE_{job_id}.json"
    candidates = io.search(token, f"trashed=false and name='{name}'")
    candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for item in candidates:
        payload = _read_json_file(token, item)
        if payload and payload.get("status") == "AI_DONE" and payload.get("job_id") == job_id:
            return item, payload
    raise RuntimeError("LONGITUDINAL_AI_DONE_NOT_FOUND")


def _load_master(token: str, done: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = done.get("masterPdf") or {}
    pdf_id = str(meta.get("driveId") or "")
    expected_pdf_sha = str(meta.get("sha256") or "").lower()
    expected_master_sha = str(meta.get("masterJsonSha256") or "").lower()
    if not pdf_id or not expected_pdf_sha:
        raise RuntimeError("LONGITUDINAL_MASTER_METADATA_MISSING")
    with tempfile.TemporaryDirectory(prefix="diana-longitudinal-pdf-") as td:
        path = Path(td) / "master.pdf"
        io.download(token, pdf_id, path)
        actual_pdf_sha = io.sha(path).lower()
        if actual_pdf_sha != expected_pdf_sha:
            raise RuntimeError("LONGITUDINAL_MASTER_PDF_SHA_MISMATCH")
        doc = fitz.open(path)
        try:
            if "master_analysis.json" not in set(doc.embfile_names()):
                raise RuntimeError("LONGITUDINAL_MASTER_JSON_NOT_EMBEDDED")
            raw = doc.embfile_get("master_analysis.json")
        finally:
            doc.close()
    actual_master_sha = _sha_bytes(raw)
    if expected_master_sha and actual_master_sha.lower() != expected_master_sha:
        raise RuntimeError("LONGITUDINAL_MASTER_JSON_SHA_MISMATCH")
    try:
        master = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("LONGITUDINAL_MASTER_JSON_INVALID") from exc
    return master, {
        "drive_id": pdf_id,
        "name": meta.get("name"),
        "size_bytes": int(meta.get("sizeBytes") or 0),
        "pdf_sha256": actual_pdf_sha,
        "master_json_sha256": actual_master_sha,
    }


def _drive_metadata(token: str, file_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{DRIVE}/files/{file_id}",
        headers=io.hdr(token),
        params={
            "fields": "id,name,mimeType,size,createdTime,modifiedTime,parents,md5Checksum",
            "supportsAllDrives": "true",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _iso_date(year: str, month: str | int, day: str) -> str | None:
    try:
        if len(str(year)) == 2:
            year = "20" + str(year)
        return datetime(int(year), int(month), int(day), tzinfo=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def _date_from_text(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in re.finditer(r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})[.\-/](20\d{2}|\d{2})(?!\d)", text or ""):
        value = _iso_date(match.group(3), match.group(2), match.group(1))
        if value:
            found.append((value, match.group(0)))
    for match in re.finditer(r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)", text or ""):
        value = _iso_date(match.group(1), match.group(2), match.group(3))
        if value:
            found.append((value, match.group(0)))
    month_names = "|".join(MONTHS)
    for match in re.finditer(rf"(?<!\d)(\d{{1,2}})\s+({month_names})\s+(20\d{{2}})(?!\d)", (text or "").casefold()):
        value = _iso_date(match.group(3), MONTHS[match.group(2)], match.group(1))
        if value:
            found.append((value, match.group(0)))
    return list(dict.fromkeys(found))


def _date_part(timestamp: object) -> str | None:
    value = str(timestamp or "")
    return value[:10] if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value[:10]) else None


def _spoken_date_evidence(master: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence = []
    for segment in master.get("transcript") or []:
        if not isinstance(segment, Mapping):
            continue
        for value, raw in _date_from_text(str(segment.get("text") or "")):
            evidence.append({
                "source_type": "spoken_explicit_date",
                "value": value,
                "strength": "HIGH",
                "independence_group": "transcript_spoken_date",
                "locator": {
                    "segment_id": segment.get("segment_id"),
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                    "raw": raw,
                },
            })
    return evidence


def _lesson_identity(
    master: Mapping[str, Any],
    source_meta: Mapping[str, Any],
    original_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source_name = str(source_meta.get("name") or (master.get("source") or {}).get("name") or "")
    evidence: list[dict[str, Any]] = []
    for value, raw in _date_from_text(source_name):
        evidence.append({
            "source_type": "explicit_source_filename",
            "value": value,
            "strength": "HIGH",
            "independence_group": "filename",
            "locator": {"file_id": source_meta.get("id"), "raw": raw},
        })
    evidence.extend(_spoken_date_evidence(master))

    env_date = os.getenv("BRIDGE_LESSON_DATE_CANDIDATE", "").strip()
    env_source = os.getenv("BRIDGE_LESSON_DATE_SOURCE", "").strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", env_date):
        evidence.append({
            "source_type": env_source or "externally_supplied_candidate",
            "value": env_date,
            "strength": "MEDIUM",
            "independence_group": "external_candidate",
            "locator": None,
        })

    # Orchestrators may supply independent Zoom, Calendar, email or chat
    # evidence without coupling this deterministic extractor to account APIs.
    # Example: [{"source_type":"zoom_metadata","value":"2021-02-22",
    #            "strength":"HIGH","locator":{"meeting_id":"..."}}]
    external_raw = os.getenv("BRIDGE_LESSON_DATE_EVIDENCE_JSON", "").strip()
    if external_raw:
        try:
            external_items = json.loads(external_raw)
        except json.JSONDecodeError:
            external_items = []
        if isinstance(external_items, list):
            for position, item in enumerate(external_items):
                if not isinstance(item, Mapping):
                    continue
                value = str(item.get("value") or "")
                if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
                    continue
                source_type = str(item.get("source_type") or "external_date_evidence")
                strength = str(item.get("strength") or "MEDIUM").upper()
                if strength not in {"HIGH", "MEDIUM", "LOW"}:
                    strength = "MEDIUM"
                evidence.append({
                    "source_type": source_type,
                    "value": value,
                    "strength": strength,
                    "independence_group": str(item.get("independence_group") or f"external:{source_type}:{position}"),
                    "locator": item.get("locator"),
                })

    original = dict(original_meta or {})
    original_dates = {
        value for value in (
            _date_part(original.get("createdTime")),
            _date_part(original.get("modifiedTime")),
        ) if value
    }
    for value in sorted(original_dates):
        evidence.append({
            "source_type": "original_drive_metadata",
            "value": value,
            "strength": "MEDIUM",
            "independence_group": "original_drive_metadata",
            "locator": {
                "file_id": original.get("id"),
                "createdTime": original.get("createdTime"),
                "modifiedTime": original.get("modifiedTime"),
            },
        })

    master_date = _date_part(source_meta.get("createdTime"))
    if master_date:
        evidence.append({
            "source_type": "master_copy_drive_metadata",
            "value": master_date,
            "strength": "LOW",
            "independence_group": "master_copy_metadata",
            "locator": {"file_id": source_meta.get("id"), "createdTime": source_meta.get("createdTime")},
        })

    by_value: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        by_value.setdefault(str(item["value"]), []).append(item)
    ranked = sorted(
        by_value.items(),
        key=lambda pair: (
            len({item["independence_group"] for item in pair[1] if item["strength"] in {"HIGH", "MEDIUM"}}),
            sum({"HIGH": 3, "MEDIUM": 2, "LOW": 1}[item["strength"]] for item in pair[1]),
        ),
        reverse=True,
    )
    lesson_date = ranked[0][0] if ranked else None
    supporting = ranked[0][1] if ranked else []
    independent_groups = {item["independence_group"] for item in supporting if item["strength"] in {"HIGH", "MEDIUM"}}
    strengths = {item["strength"] for item in supporting}
    if len(independent_groups) >= 2 and ("HIGH" in strengths or len(supporting) >= 2):
        date_status = "CONFIRMED"
    elif "HIGH" in strengths:
        date_status = "CANDIDATE_HIGH"
    elif "MEDIUM" in strengths:
        date_status = "CANDIDATE_MEDIUM"
    elif lesson_date:
        date_status = "CANDIDATE_LOW"
    else:
        date_status = "UNKNOWN"

    number_raw = os.getenv("BRIDGE_LESSON_NUMBER", "").strip()
    lesson_number = int(number_raw) if number_raw.isdigit() else None
    return {
        "lesson_id": _stable_id("lesson", master.get("job_id"), lesson_number or "unknown"),
        "lesson_number": lesson_number,
        "lesson_date": lesson_date,
        "lesson_date_status": date_status,
        "lesson_date_source": [item["source_type"] for item in supporting],
        "date_evidence": evidence,
        "chronology_position": lesson_number,
        "master_source_drive_id": source_meta.get("id"),
        "master_source_created_at": source_meta.get("createdTime"),
        "master_source_modified_at": source_meta.get("modifiedTime"),
        "original_source_drive_id": original.get("id"),
        "original_source_created_at": original.get("createdTime"),
        "original_source_modified_at": original.get("modifiedTime"),
        "date_warning": (
            None if date_status == "CONFIRMED"
            else "Дата занятия остаётся кандидатом; даты Drive хранятся отдельно и не подменяют фактическую дату занятия."
        ),
    }


def _curriculum(master: Mapping[str, Any], lesson: Mapping[str, Any], quality: Mapping[str, Any]) -> dict[str, Any]:
    session = master.get("session_summary") or {}
    topic_counts = session.get("top_topic_counts") or []
    if not topic_counts:
        topic_counts = Counter(
            term for episode in (master.get("episodes") or []) for term in (episode.get("terms") or [])
        ).most_common(50)
    modules = []
    for position, pair in enumerate(topic_counts, 1):
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            topic, count = pair[0], pair[1]
        else:
            topic, count = pair, None
        modules.append({
            "module_candidate_id": _stable_id("module", master.get("job_id"), topic),
            "topic": topic,
            "observed_episode_count": count,
            "historical_lesson_number": lesson.get("lesson_number"),
            "historical_lesson_date": lesson.get("lesson_date"),
            "proposed_school_stage": None,
            "status": "HISTORICAL_OBSERVATION_AND_CURRICULUM_CANDIDATE",
            "activation_allowed": False,
        })
    return {
        "historical_curriculum": {
            "status": "HISTORICAL_CANDIDATE",
            "lesson_number": lesson.get("lesson_number"),
            "lesson_date": lesson.get("lesson_date"),
            "topic_order": [item.get("topic") for item in modules],
            "section_candidates": (quality.get("hierarchy") or {}).get("sections") or [],
            "source_episode_count": len(master.get("episodes") or []),
        },
        "candidate_school_curriculum": {
            "activation_allowed": False,
            "modules": modules,
            "stage_boundary_proposal": None,
            "note": "Одно занятие не определяет год/этап общего курса; данные накапливаются продольно.",
        },
    }


def _safe_filename(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "_", value).strip()[:160]


def _upload_immutable(token: str, parent: str, path: Path, mime: str) -> dict[str, Any]:
    escaped = path.name.replace("'", "\\'")
    existing = io.search(token, f"'{parent}' in parents and trashed=false and name='{escaped}'")
    if existing:
        existing.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
        return {"id": existing[0]["id"], "name": existing[0].get("name"), "status": "already_exists"}
    uploaded = io.upload_file(token, parent, path, mime)
    return {"id": uploaded["id"], "name": uploaded.get("name"), "status": "uploaded"}


def _teacher_brief_markdown(payload: Mapping[str, Any]) -> str:
    lesson = payload["lesson_identity"]
    quality = payload["quality_v2"]
    brief = quality["teacher_brief"]
    readiness = quality["readiness"]
    lines = [
        f"# {brief.get('title')}",
        "",
        f"- Дата занятия: **{lesson.get('lesson_date') or 'не установлена'}** ({lesson.get('lesson_date_status')})",
        f"- Статус методического анализа: **{readiness.get('methodology_status')}**",
        f"- Полных доказательных учебных циклов: **{readiness.get('complete_learning_interactions', 0)}**",
        f"- Роли участников: **{(readiness.get('speaker_summary') or {}).get('status')}**",
        "",
        "## Темы-кандидаты",
        "",
    ]
    for topic in brief.get("topic_candidates") or []:
        lines.append(f"- {topic}")
    lines += ["", "## Проверенные наблюдения об обучении", ""]
    observations = brief.get("student_conclusions") or []
    if not observations:
        lines.append("Надёжных персональных выводов об ответах Дианы пока недостаточно.")
    for index, item in enumerate(observations, 1):
        lines += [
            f"### Наблюдение {index}",
            f"- Задача: {item.get('task') or 'не установлена'}",
            f"- Действие ученицы: {item.get('action') or 'не установлено'}",
            f"- Помощь: {item.get('help') or 'не установлена'}",
            f"- Наблюдаемый результат: {item.get('result') or 'не установлен'}",
            f"- Evidence: {', '.join(item.get('evidence_refs') or []) or 'нет'}",
            "",
        ]
    lines += ["## Лучшие переиспользуемые кандидаты", ""]
    assets = brief.get("reusable_asset_candidates") or []
    if not assets:
        lines.append("Активные переиспользуемые материалы не прошли Value Gate.")
    for item in assets:
        lines.append(
            f"- **{item.get('asset_type')}** — {', '.join(item.get('topics') or []) or 'тема не установлена'}; "
            f"{item.get('value_reason') or 'ценность требует проверки'}"
        )
    lines += ["", "## Следующие проверки", ""]
    for probe in brief.get("pending_probes") or []:
        lines.append(f"- **{probe.get('topic_candidate')}**: {probe.get('future_probe')}")
    lines += ["", "## Ограничения", ""]
    for issue in brief.get("limitations") or []:
        lines.append(f"- {issue}")
    lines += [
        "",
        f"> {brief.get('teacher_message')}",
        "",
        "Канон, нормативный курс и производственный профиль ученицы автоматически не изменялись.",
    ]
    return "\n".join(lines) + "\n"


def _cards_markdown(payload: Mapping[str, Any]) -> str:
    cards = (payload.get("quality_v2") or {}).get("learning_cards") or []
    lines = ["# Учебные карточки — Диана", ""]
    if not cards:
        lines.append("Карточки не созданы: данных недостаточно, и алгоритм не производит искусственные материалы.")
        return "\n".join(lines) + "\n"
    for index, card in enumerate(cards, 1):
        lines += [
            f"## Карточка {index}: {card.get('card_type')}",
            "",
            f"- Время: {card.get('time_range') or 'по Evidence'}",
            f"- Темы: {', '.join(card.get('topic_candidates') or []) or 'не установлены'}",
            f"- Ситуация/задача: {card.get('task') or 'не установлена'}",
            f"- Действие ученицы: {card.get('student_action') or 'не атрибутировано'}",
            f"- Вмешательство преподавателя: {card.get('teacher_intervention') or 'не атрибутировано'}",
            f"- Наблюдаемый результат: {card.get('observed_result') or 'не установлен'}",
            f"- Следующая проверка: {card.get('next_probe') or 'не назначена'}",
            f"- Evidence: {', '.join(card.get('evidence_refs') or []) or 'нет'}",
            f"- Статус: {card.get('status')}",
            "",
        ]
    return "\n".join(lines) + "\n"


def _summary_markdown(payload: Mapping[str, Any]) -> str:
    lesson = payload["lesson_identity"]
    counts = payload["quality_v2"]["counts"]
    readiness = payload["quality_v2"]["readiness"]
    return "\n".join([
        "# Диана — продольное извлечение v2",
        "",
        f"- Job ID: `{payload['job_id']}`",
        f"- Дата занятия: **{lesson.get('lesson_date') or 'не установлена'}** ({lesson.get('lesson_date_status')})",
        f"- TECHNICAL: **{readiness.get('technical_status')}**",
        f"- CONTENT: **{readiness.get('content_status')}**",
        f"- METHODOLOGY: **{readiness.get('methodology_status')}**",
        "- MASTER-источник использован только для чтения.",
        "- Тяжёлая повторная обработка видео не выполнялась этим слоем.",
        "",
        "## Quality-first counts",
        "",
        *(f"- {key}: **{value}**" for key, value in counts.items()),
        "",
        "## Cost Gate",
        "",
        "- Платные AI API: **0**.",
        f"- Повторное скачивание/ASR оригинального видео: **{payload['cost']['heavy_video_reprocessed']}**.",
        f"- Повторно использован master PDF размером {payload['provenance']['master_pdf'].get('size_bytes', 0)} байт.",
        "",
    ]) + "\n"


def _persist_staging_if_configured(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_dsn = os.getenv("BRIDGE_WORKER_DATABASE_URL", "").strip()
    requested = os.getenv("BRIDGE_PERSIST_DATABASE", "false").strip().casefold() == "true"
    if not requested:
        return {"status": "NOT_REQUESTED"}
    if not raw_dsn:
        return {"status": "SKIPPED_DATABASE_URL_MISSING"}
    try:
        from database.video_candidate_persistence import persist_quality_candidates
        return persist_quality_candidates(raw_dsn, payload)
    except Exception as exc:
        # Candidate persistence must not invalidate already completed immutable
        # media evidence.  The receipt records the real blocker for retry.
        return {
            "status": "SKIPPED_OR_FAILED",
            "error_class": type(exc).__name__,
            "detail": str(exc)[:400],
        }


def main() -> int:
    job_id = os.environ.get("BRIDGE_JOB_ID", "").strip()
    result_folder = os.environ.get("BRIDGE_OUTPUT_FOLDER_ID", "").strip()
    work_folder = os.environ.get("BRIDGE_WORK_FOLDER_ID", "").strip()
    if not job_id or not result_folder:
        raise RuntimeError("LONGITUDINAL_JOB_AND_OUTPUT_FOLDER_REQUIRED")
    token = user_oauth_token()
    if not token:
        raise RuntimeError("BLOCKED_ACCESS: Drive OAuth unavailable")

    done_item, done = _latest_done(token, job_id)
    master, master_pdf = _load_master(token, done)
    if master.get("job_id") != job_id:
        raise RuntimeError("LONGITUDINAL_JOB_ID_MISMATCH")

    source_id = str((master.get("source") or {}).get("driveId") or "")
    source_meta = _drive_metadata(token, source_id)
    original_id = os.environ.get("BRIDGE_ORIGINAL_SOURCE_DRIVE_ID", "").strip()
    original_meta = _drive_metadata(token, original_id) if original_id else None
    lesson = _lesson_identity(master, source_meta, original_meta)
    quality = build_quality_layer(master, lesson)
    curriculum = _curriculum(master, lesson, quality)
    gaps = list(master.get("knowledge_gaps") or [])
    if lesson.get("lesson_date_status") != "CONFIRMED":
        gaps.append({
            "gap_id": _stable_id("gap", job_id, "lesson-date-confirmation"),
            "gap_type": "SOURCE_WEAK",
            "status": "OPEN",
            "question": "Подтвердить фактическую дату занятия независимым источником.",
            "date_evidence": lesson.get("date_evidence") or [],
        })

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "quality_method_version": QUALITY_METHOD_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "algorithm_version": master.get("algorithmVersion"),
        "algorithm_revision": master.get("algorithmRevision"),
        "authority": {
            "canon_activation": "DENY",
            "curriculum_activation": "DENY",
            "student_profile_production_write": "DENY",
            "methodology_activation": "DENY",
            "artifact_status": "A1_CANDIDATE_EVIDENCE",
        },
        "provenance": {
            "ai_done_drive_id": done_item["id"],
            "master_pdf": master_pdf,
            "master_json_job_id_verified": True,
            "source_read_only": True,
        },
        "lesson_identity": lesson,
        "quality_v2": quality,
        "curriculum": curriculum,
        "knowledge_gaps": gaps,
        "technical_qc": master.get("technical_qc") or {},
        "warnings": master.get("warnings") or [],
        "cost": {
            "paid_ai_api_cost": 0,
            "paid_cloud_fallback_used": False,
            "source_size_bytes": int(source_meta.get("size") or 0),
            "heavy_video_reprocessed": False,
            "persistent_heavy_working_video_copy_created": False,
            "reused_master_pdf_bytes": master_pdf.get("size_bytes", 0),
            "cost_note": "Semantic-only refinement reuses embedded master analysis; provider plan accounting remains external.",
        },
    }
    payload["database_staging"] = _persist_staging_if_configured(payload)

    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    digest = _sha_bytes(raw)[:12]
    lesson_label = lesson.get("lesson_number") or "?"
    base = _safe_filename(f"Диана {lesson_label} — longitudinal v2 {digest}")
    with tempfile.TemporaryDirectory(prefix="diana-longitudinal-output-") as td:
        td_path = Path(td)
        json_path = td_path / f"{base}.json"
        summary_path = td_path / f"{base} — summary.md"
        teacher_path = td_path / f"Диана {lesson_label} — Teacher Brief v2 {digest}.md"
        cards_path = td_path / f"Диана {lesson_label} — Learning Cards v2 {digest}.md"
        staging_path = td_path / f"Диана {lesson_label} — Candidate Staging v2 {digest}.json"
        receipt_path = td_path / f"LONGITUDINAL_V2_DONE_{job_id}_{digest}.json"

        json_path.write_bytes(raw)
        summary_path.write_text(_summary_markdown(payload), encoding="utf-8")
        teacher_path.write_text(_teacher_brief_markdown(payload), encoding="utf-8")
        cards_path.write_text(_cards_markdown(payload), encoding="utf-8")
        staging_path.write_text(
            json.dumps(quality.get("candidate_staging_records") or [], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        receipt = {
            "status": "LONGITUDINAL_V2_DONE",
            "job_id": job_id,
            "digest": digest,
            "schema_version": SCHEMA_VERSION,
            "quality_method_version": QUALITY_METHOD_VERSION,
            "readiness": quality.get("readiness") or {},
            "counts": quality.get("counts") or {},
            "master_pdf_drive_id": master_pdf["drive_id"],
            "source_drive_id": source_id,
            "source_untouched": True,
            "heavy_video_reprocessed": False,
            "canon_activated": False,
            "student_profile_written": False,
            "curriculum_activated": False,
            "database_staging": payload.get("database_staging"),
            "created_at": payload["created_at"],
        }
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        uploads = [
            _upload_immutable(token, result_folder, json_path, "application/json"),
            _upload_immutable(token, result_folder, summary_path, "text/markdown"),
            _upload_immutable(token, result_folder, teacher_path, "text/markdown"),
            _upload_immutable(token, result_folder, cards_path, "text/markdown"),
            _upload_immutable(token, result_folder, staging_path, "application/json"),
            _upload_immutable(token, work_folder or result_folder, receipt_path, "application/json"),
        ]

    source_after = _drive_metadata(token, source_id)
    if result_folder in (source_after.get("parents") or []) or (work_folder and work_folder in (source_after.get("parents") or [])):
        raise RuntimeError("LONGITUDINAL_SOURCE_MOVED_TO_WORK_OR_RESULT")

    print(json.dumps({
        "stage": "DIANA_LONGITUDINAL_POSTPROCESS_V2",
        "status": "DONE",
        "job_id": job_id,
        "digest": digest,
        "source_untouched": True,
        "heavy_video_reprocessed": False,
        "readiness": quality.get("readiness"),
        "counts": quality.get("counts"),
        "database_staging": payload.get("database_staging"),
        "uploads": uploads,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
