#!/usr/bin/env python3
"""Create longitudinal School-learning candidate artifacts from one completed video job.

This is a conservative postprocessor over the already evidence-linked
``master_analysis.json``.  It never changes the source video, never activates
School canon/curriculum, and never writes a production Student profile.
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
from typing import Any

import fitz
import requests

import run_drive_3_1_free as io
from run_drive_3_1_free_oidc import user_oauth_token

DRIVE = "https://www.googleapis.com/drive/v3"
SCHEMA = "diana-longitudinal-extraction"
SCHEMA_VERSION = 1


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stable_id(kind: str, *parts: object) -> str:
    seed = "|".join(str(value) for value in parts)
    return f"{kind}_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _read_json_file(token: str, item: dict) -> dict | None:
    with tempfile.TemporaryDirectory(prefix="diana-longitudinal-json-") as td:
        path = Path(td) / "payload.json"
        io.download(token, item["id"], path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


def _latest_done(token: str, job_id: str) -> tuple[dict, dict]:
    name = f"AI_DONE_{job_id}.json"
    candidates = io.search(token, f"trashed=false and name='{name}'")
    candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for item in candidates:
        payload = _read_json_file(token, item)
        if payload and payload.get("status") == "AI_DONE" and payload.get("job_id") == job_id:
            return item, payload
    raise RuntimeError("LONGITUDINAL_AI_DONE_NOT_FOUND")


def _load_master(token: str, done: dict) -> tuple[dict, dict]:
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
        "pdf_sha256": actual_pdf_sha,
        "master_json_sha256": actual_master_sha,
    }


def _drive_metadata(token: str, file_id: str) -> dict:
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


def _date_from_name(name: str) -> tuple[str | None, str | None]:
    patterns = (
        (r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})[.\-/](20\d{2}|\d{2})(?!\d)", "dmy"),
        (r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)", "ymd"),
    )
    for pattern, order in patterns:
        match = re.search(pattern, name or "")
        if not match:
            continue
        try:
            if order == "dmy":
                day, month, year = match.groups()
            else:
                year, month, day = match.groups()
            if len(year) == 2:
                year = "20" + year
            value = datetime(int(year), int(month), int(day), tzinfo=timezone.utc).date().isoformat()
            return value, match.group(0)
        except ValueError:
            continue
    return None, None


def _lesson_identity(master: dict, source_meta: dict, original_meta: dict | None) -> dict:
    source_name = str(source_meta.get("name") or (master.get("source") or {}).get("name") or "")
    explicit_date, explicit_text = _date_from_name(source_name)
    env_date = os.getenv("BRIDGE_LESSON_DATE_CANDIDATE", "").strip()
    env_source = os.getenv("BRIDGE_LESSON_DATE_SOURCE", "").strip()
    if explicit_date:
        lesson_date = explicit_date
        date_status = "CANDIDATE_HIGH"
        date_source = f"explicit_source_filename:{explicit_text}"
    elif env_date:
        lesson_date = env_date
        date_status = "CANDIDATE_MEDIUM"
        date_source = env_source or "externally_supplied_source_metadata_candidate"
    else:
        lesson_date = None
        date_status = "UNKNOWN"
        date_source = None

    number_raw = os.getenv("BRIDGE_LESSON_NUMBER", "").strip()
    lesson_number = int(number_raw) if number_raw.isdigit() else None
    original = original_meta or {}
    return {
        "lesson_id": _stable_id("lesson", master.get("job_id"), lesson_number or "unknown"),
        "lesson_number": lesson_number,
        "lesson_date": lesson_date,
        "lesson_date_status": date_status,
        "lesson_date_source": date_source,
        "chronology_position": lesson_number,
        "master_source_drive_id": source_meta.get("id"),
        "master_source_created_at": source_meta.get("createdTime"),
        "master_source_modified_at": source_meta.get("modifiedTime"),
        "original_source_drive_id": original.get("id"),
        "original_source_created_at": original.get("createdTime"),
        "original_source_modified_at": original.get("modifiedTime"),
        "date_warning": (
            None if date_status == "CANDIDATE_HIGH"
            else "Дата занятия является кандидатом, а не подтверждённым фактом; дата Drive хранится отдельно."
        ),
    }


def _episode_map(master: dict) -> dict[str, dict]:
    return {
        str(item.get("episode_id")): item
        for item in (master.get("episodes") or [])
        if item.get("episode_id")
    }


def _canon_candidates(master: dict, episodes: dict[str, dict]) -> list[dict]:
    out = []
    for index, link in enumerate(master.get("canon_links") or [], 1):
        if link.get("status") == "не найдено" or not _norm(link.get("canonical_excerpt")):
            continue
        episode_id = str(link.get("episode_id") or "")
        episode = episodes.get(episode_id, {})
        out.append({
            "canon_observation_id": _stable_id("canonobs", master.get("job_id"), episode_id, index),
            "status": "CANON_MATCH_CANDIDATE",
            "activation_allowed": False,
            "episode_id": episode_id or None,
            "observed_lesson_text": _norm(episode.get("summary_text"))[:1600] or None,
            "candidate_canonical_excerpt": _norm(link.get("canonical_excerpt"))[:1600],
            "match_status": link.get("status"),
            "match_score": link.get("score"),
            "evidence_refs": episode.get("evidence") or [],
            "authority_note": "Видео и тематическое совпадение не активируют канон автоматически.",
        })
    return out


def _knowledge_candidates(master: dict) -> list[dict]:
    out = []
    seen: set[str] = set()
    for episode in master.get("episodes") or []:
        summary = _norm(episode.get("summary_text"))
        terms = sorted({_norm(term) for term in (episode.get("terms") or []) if _norm(term)})
        if not summary or not terms:
            continue
        key = hashlib.sha256(("|".join(terms) + "|" + summary.casefold()).encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "knowledge_candidate_id": _stable_id("knowledge", master.get("job_id"), key),
            "status": "CANDIDATE_KNOWLEDGE",
            "knowledge_type": episode.get("type") or "lesson_episode",
            "title_candidates": terms,
            "content": summary[:2400],
            "scope": {"single_lesson": True, "lesson_stage": "historical"},
            "episode_id": episode.get("episode_id"),
            "evidence_refs": episode.get("evidence") or [],
            "visual_evidence_refs": episode.get("visual_evidence") or [],
            "confidence_class": episode.get("confidence"),
            "dedupe_key": key,
            "verification_required": True,
        })
    return out


def _reusable_assets(master: dict, episodes: dict[str, dict]) -> list[dict]:
    specs = (
        ("EXPLANATION_CANDIDATE", master.get("best_explanations") or [], "explanation_id"),
        ("TYPICAL_ERROR_CANDIDATE", master.get("errors") or [], "error_id"),
        ("POSITIVE_DECISION_CANDIDATE", master.get("strengths") or [], "strength_id"),
        ("TEACHER_INTERVENTION_CANDIDATE", master.get("teacher_analysis") or [], "observation_id"),
    )
    out = []
    for asset_type, items, id_field in specs:
        for item in items:
            episode_id = str(item.get("episode_id") or "")
            episode = episodes.get(episode_id, {})
            out.append({
                "asset_id": str(item.get(id_field) or _stable_id("asset", asset_type, episode_id)),
                "asset_type": asset_type,
                "status": "CANDIDATE",
                "episode_id": episode_id or None,
                "content": item,
                "source_excerpt": _norm(episode.get("summary_text"))[:1600] or None,
                "evidence_refs": list(dict.fromkeys((item.get("evidence") or []) + (episode.get("evidence") or []))),
                "reuse_authority": "NON_CANONICAL_CANDIDATE",
            })
    return out


def _student_opportunities(master: dict) -> list[dict]:
    out = []
    for item in ((master.get("student_analysis") or {}).get("observations") or []):
        out.append({
            "opportunity_id": str(item.get("observation_id") or _stable_id("opportunity", json.dumps(item, sort_keys=True))),
            "status": "CANDIDATE_OBSERVATION",
            "learning_vs_assessment": "learning",
            "task": item.get("task"),
            "observed_response": item.get("student_action"),
            "help_state": item.get("support_state"),
            "result": item.get("result"),
            "transfer": item.get("transfer"),
            "evidence_refs": item.get("evidence") or [],
            "profile_write_allowed": False,
        })
    for item in master.get("decisions") or []:
        out.append({
            "opportunity_id": str(item.get("decision_id") or _stable_id("decisionop", json.dumps(item, sort_keys=True))),
            "status": "OBSERVED_DECISION_CANDIDATE",
            "learning_vs_assessment": "unknown",
            "task": item.get("observed_context"),
            "observed_response": item.get("action_taken"),
            "reasoning": item.get("reasoning"),
            "actor_attribution_status": item.get("actor_attribution_status"),
            "evidence_refs": item.get("evidence") or [],
            "profile_write_allowed": False,
        })
    return out


def _teacher_observations(master: dict) -> list[dict]:
    out = []
    for item in master.get("teacher_analysis") or []:
        out.append({
            "teacher_observation_id": item.get("observation_id"),
            "status": "DESCRIPTIVE_CANDIDATE",
            "method_label": item.get("method"),
            "note": item.get("note"),
            "episode_id": item.get("episode_id"),
            "evidence_refs": item.get("evidence") or [],
            "methodology_activation_allowed": False,
        })
    for item in master.get("learning_interactions") or []:
        out.append({
            "teacher_observation_id": item.get("cycle_id"),
            "status": item.get("verification_status") or "CANDIDATE_CYCLE",
            "method_label": item.get("intervention_type"),
            "teacher_intervention": item.get("teacher_intervention"),
            "observed_followup": item.get("student_response"),
            "outcome": item.get("outcome"),
            "evidence_refs": item.get("evidence") or [],
            "methodology_activation_allowed": False,
        })
    return out


def _curriculum(master: dict, lesson: dict) -> dict:
    session = master.get("session_summary") or {}
    topic_counts = session.get("top_topic_counts") or []
    if not topic_counts:
        topic_counts = Counter(
            term for episode in (master.get("episodes") or []) for term in (episode.get("terms") or [])
        ).most_common(50)
    domains = Counter(str(episode.get("type") or "не классифицировано") for episode in master.get("episodes") or [])
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
        })
    return {
        "historical_curriculum": {
            "status": "HISTORICAL_CANDIDATE",
            "lesson_number": lesson.get("lesson_number"),
            "lesson_date": lesson.get("lesson_date"),
            "topic_order": [item.get("topic") for item in modules],
            "domain_episode_counts": dict(domains),
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


def _upload_immutable(token: str, parent: str, path: Path, mime: str) -> dict:
    existing = io.search(token, f"'{parent}' in parents and trashed=false and name='{path.name.replace(chr(39), chr(92)+chr(39))}'")
    if existing:
        existing.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
        return {"id": existing[0]["id"], "name": existing[0].get("name"), "status": "already_exists"}
    uploaded = io.upload_file(token, parent, path, mime)
    return {"id": uploaded["id"], "name": uploaded.get("name"), "status": "uploaded"}


def _markdown(payload: dict) -> str:
    lesson = payload["lesson_identity"]
    counts = payload["counts"]
    lines = [
        "# Диана 1 — продольное извлечение",
        "",
        f"- Job ID: `{payload['job_id']}`",
        f"- Дата занятия: **{lesson.get('lesson_date') or 'не установлена'}** ({lesson.get('lesson_date_status')})",
        f"- Основание даты: {lesson.get('lesson_date_source') or 'нет'}",
        f"- Источник MASTER: `{lesson.get('master_source_drive_id')}` — только чтение",
        f"- Версия видеоанализа: `{payload.get('algorithm_revision')}`",
        "- Канон автоматически не активировался; профиль ученицы автоматически не изменялся.",
        "",
        "## Что извлечено",
        "",
    ]
    for label, key in (
        ("Смысловые эпизоды", "episodes"),
        ("Наблюдения связи с каноном", "canon_candidates"),
        ("Кандидаты знаний", "knowledge_candidates"),
        ("Переиспользуемые материалы", "reusable_assets"),
        ("Учебные возможности/решения", "student_opportunities"),
        ("Наблюдения работы преподавателя", "teacher_observations"),
        ("Пробелы базы знаний", "knowledge_gaps"),
    ):
        lines.append(f"- {label}: **{counts.get(key, 0)}**")
    lines += [
        "",
        "## Темы первого занятия",
        "",
    ]
    for module in payload["curriculum"]["candidate_school_curriculum"]["modules"][:30]:
        count = module.get("observed_episode_count")
        lines.append(f"- {module.get('topic')}" + (f" — {count} эпизод(а)" if count is not None else ""))
    lines += [
        "",
        "## Ограничения",
        "",
        "- Все содержательные элементы остаются кандидатами до проверки Evidence и канона школы.",
        "- Одно видео не доказывает устойчивый навык, причинность педагогического приёма или место темы в общем многолетнем курсе.",
        "- `NOT_OBSERVED` не трактуется как ошибка или отсутствие знания.",
        "",
        "## Cost Gate",
        "",
        "- Платные AI API: **0**.",
        "- Использован существующий бесплатный локальный ASR-путь GitHub Actions; тяжёлые постоянные рабочие копии не создавались.",
    ]
    return "\n".join(lines) + "\n"


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
    episodes = _episode_map(master)
    canon = _canon_candidates(master, episodes)
    knowledge = _knowledge_candidates(master)
    assets = _reusable_assets(master, episodes)
    opportunities = _student_opportunities(master)
    teachers = _teacher_observations(master)
    curriculum = _curriculum(master, lesson)
    gaps = list(master.get("knowledge_gaps") or [])
    if lesson.get("lesson_date_status") != "CANDIDATE_HIGH":
        gaps.append({
            "gap_id": _stable_id("gap", job_id, "lesson-date-confirmation"),
            "gap_type": "SOURCE_WEAK",
            "status": "OPEN",
            "question": "Подтвердить фактическую дату занятия Диана 1 независимо от даты загрузки Drive.",
        })

    payload = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "algorithm_version": master.get("algorithmVersion"),
        "algorithm_revision": master.get("algorithmRevision"),
        "authority": {
            "canon_activation": "DENY",
            "curriculum_activation": "DENY",
            "student_profile_production_write": "DENY",
            "artifact_status": "A1_CANDIDATE_EVIDENCE",
        },
        "provenance": {
            "ai_done_drive_id": done_item["id"],
            "master_pdf": master_pdf,
            "master_json_job_id_verified": True,
            "source_read_only": True,
        },
        "lesson_identity": lesson,
        "session_summary": master.get("session_summary") or {},
        "timeline": master.get("timeline") or [],
        "canon_candidates": canon,
        "knowledge_candidates": knowledge,
        "reusable_assets": assets,
        "student_opportunities": opportunities,
        "teacher_observations": teachers,
        "outcome_candidates": {
            "strengths": master.get("strengths") or [],
            "learning_interactions": master.get("learning_interactions") or [],
            "retention": "NOT_OBSERVED_IN_SINGLE_LESSON_UNLESS_EXPLICITLY_LINKED",
            "generalization": "NOT_CONFIRMED",
            "transfer": "NOT_CONFIRMED",
        },
        "curriculum": curriculum,
        "knowledge_gaps": gaps,
        "technical_qc": master.get("technical_qc") or {},
        "content_quality": master.get("content_quality") or {},
        "warnings": master.get("warnings") or [],
        "cost": {
            "paid_ai_api_cost": 0,
            "paid_cloud_fallback_used": False,
            "source_size_bytes": int(source_meta.get("size") or 0),
            "persistent_heavy_working_video_copy_created": False,
            "cost_note": "Standard public GitHub runner and local open-source ASR path; actual provider quota/plan accounting is external.",
        },
    }
    payload["counts"] = {
        "episodes": len(master.get("episodes") or []),
        "transcript_segments": len(master.get("transcript") or []),
        "canon_candidates": len(canon),
        "knowledge_candidates": len(knowledge),
        "reusable_assets": len(assets),
        "student_opportunities": len(opportunities),
        "teacher_observations": len(teachers),
        "knowledge_gaps": len(gaps),
        "decisions": len(master.get("decisions") or []),
        "deals": len(master.get("deals") or []),
    }

    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    digest = _sha_bytes(raw)[:12]
    base = _safe_filename(f"Диана 1 — longitudinal {digest}")
    with tempfile.TemporaryDirectory(prefix="diana-longitudinal-output-") as td:
        td_path = Path(td)
        json_path = td_path / f"{base}.json"
        md_path = td_path / f"{base}.md"
        receipt_path = td_path / f"LONGITUDINAL_DONE_{job_id}_{digest}.json"
        json_path.write_bytes(raw)
        md_path.write_text(_markdown(payload), encoding="utf-8")
        receipt = {
            "status": "LONGITUDINAL_DONE",
            "job_id": job_id,
            "digest": digest,
            "schema_version": SCHEMA_VERSION,
            "master_pdf_drive_id": master_pdf["drive_id"],
            "source_drive_id": source_id,
            "source_untouched": True,
            "canon_activated": False,
            "student_profile_written": False,
            "created_at": payload["created_at"],
        }
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        uploads = [
            _upload_immutable(token, result_folder, json_path, "application/json"),
            _upload_immutable(token, result_folder, md_path, "text/markdown"),
            _upload_immutable(token, work_folder or result_folder, receipt_path, "application/json"),
        ]

    source_after = _drive_metadata(token, source_id)
    if result_folder in (source_after.get("parents") or []) or (work_folder and work_folder in (source_after.get("parents") or [])):
        raise RuntimeError("LONGITUDINAL_SOURCE_MOVED_TO_WORK_OR_RESULT")

    print(json.dumps({
        "stage": "DIANA_LONGITUDINAL_POSTPROCESS",
        "status": "DONE",
        "job_id": job_id,
        "digest": digest,
        "source_untouched": True,
        "counts": payload["counts"],
        "uploads": uploads,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
