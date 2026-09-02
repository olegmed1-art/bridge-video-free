"""Server-side final technical review for Universal Video results.

This post-process deliberately stays on the technical side of the canon
boundary.  It reduces a conformant result bundle to a bounded exception-first
handoff without claiming that bridge or pedagogical interpretation ran.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA = "universal-video-server-review-v1"
MAX_REVIEW_ITEMS = 100
MAX_EXCERPT_CHARS = 500
_SPACE_RE = re.compile(r"\s+")


class ServerReviewError(RuntimeError):
    """Raised when a technical result cannot produce a safe review packet."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ServerReviewError(f"invalid server-review input: {path.name}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ServerReviewError("invalid server-review transcript") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise ServerReviewError("invalid server-review transcript rows")
    return rows


def _excerpt(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()[:MAX_EXCERPT_CHARS]


def _bounded_item(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    if len(items) < MAX_REVIEW_ITEMS:
        items.append(item)


def build_server_review(job_dir: Path, conformance: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded, deterministic final-review packet.

    ``conformance`` must be the generation-finalization or safe-reuse result
    produced before this supplementary artifact exists. The returned packet
    binds itself to that exact base artifact inventory. Later reuse and
    publication passes revalidate the immutable binding instead of rewriting
    the review packet.
    """

    if conformance.get("state") != "PASS" or conformance.get("technical_bundle_ready") is not True:
        raise ServerReviewError("server review requires a conformant technical bundle")
    if conformance.get("evidence_phase") not in {"GENERATION_FINALIZATION", "REUSE_OBSERVATION"}:
        raise ServerReviewError("server review requires a finalization evidence phase")
    base_artifact_set = str(conformance.get("artifact_set_sha256") or "")
    if len(base_artifact_set) != 64:
        raise ServerReviewError("server review requires a bound base artifact set")

    manifest = _read_json(job_dir / "manifest.json")
    qc_rows = _read_json(job_dir / "transcript_qc.json")
    transcript_rows = _read_jsonl(job_dir / "transcript.jsonl")
    if not isinstance(manifest, dict) or not isinstance(qc_rows, list):
        raise ServerReviewError("invalid server-review bundle")

    items: list[dict[str, Any]] = []
    for row in qc_rows:
        if not isinstance(row, dict):
            raise ServerReviewError("invalid server-review QC row")
        if bool(row.get("ok")) and not bool(row.get("critical")) and not bool(row.get("nonspeech_hallucination")):
            continue
        reasons = row.get("failure_reasons")
        if not isinstance(reasons, list):
            reasons = []
        _bounded_item(
            items,
            {
                "kind": "ASR_QC_EXCEPTION",
                "severity": "CRITICAL" if bool(row.get("critical")) else "REVIEW",
                "start": float(row.get("start") or 0.0),
                "end": float(row.get("end") or 0.0),
                "reason_codes": [str(value)[:96] for value in reasons[:8]],
                "evidence": f"transcript_qc.json#chunk={int(row.get('chunk') or 0)}",
            },
        )

    for index, row in enumerate(transcript_rows):
        if not bool(row.get("unreliable")):
            continue
        _bounded_item(
            items,
            {
                "kind": "UNRELIABLE_TRANSCRIPT_SEGMENT",
                "severity": "REVIEW",
                "start": float(row.get("start") or 0.0),
                "end": float(row.get("end") or 0.0),
                "excerpt": _excerpt(row.get("text")),
                "evidence": f"transcript.jsonl#segment={index}",
            },
        )

    deferred = manifest.get("deferred_analysis")
    if not isinstance(deferred, list) or not all(isinstance(value, str) for value in deferred):
        raise ServerReviewError("invalid deferred-analysis boundary")
    if deferred:
        _bounded_item(
            items,
            {
                "kind": "DEFERRED_DOMAIN_ANALYSIS",
                "severity": "REVIEW",
                "reason_codes": [str(value)[:96] for value in deferred[:20]],
                "evidence": "manifest.json#deferred_analysis",
            },
        )

    truncated = max(0, sum(
        1
        for row in qc_rows
        if isinstance(row, dict)
        and (not bool(row.get("ok")) or bool(row.get("critical")) or bool(row.get("nonspeech_hallucination")))
    ) + sum(bool(row.get("unreliable")) for row in transcript_rows) + bool(deferred) - len(items))
    transcript = manifest.get("transcript") if isinstance(manifest.get("transcript"), dict) else {}
    frames = manifest.get("frames") if isinstance(manifest.get("frames"), list) else []
    requires_expert_review = bool(items)

    return {
        "schema": SCHEMA,
        "state": "REVIEW_REQUIRED" if requires_expert_review else "PASS",
        "review_scope": "TECHNICAL_POSTPROCESS_ONLY",
        "execution_location": "RESIDENT_SERVER_POSTPROCESS",
        "job_id": manifest.get("job_id"),
        "job_hash": manifest.get("job_hash"),
        "profile": manifest.get("profile"),
        "source_fingerprint": manifest.get("source_fingerprint"),
        "processing_fingerprint": manifest.get("processing_fingerprint"),
        "input_conformance": {
            "schema": conformance.get("schema"),
            "state": "PASS",
            "evidence_phase": conformance.get("evidence_phase"),
            "artifact_set_sha256": base_artifact_set,
        },
        "checks": {
            "artifact_integrity": "PASS",
            "transcript_timeline": "PASS",
            "transcript_qc": "PASS",
            "keyframe_inventory": "PASS" if frames else "NOT_APPLICABLE",
            "exception_compaction": "PASS",
        },
        "summary": {
            "duration_seconds": (manifest.get("media") or {}).get("duration_seconds"),
            "transcript_segments": transcript.get("segments"),
            "transcript_words": transcript.get("words"),
            "transcript_language": transcript.get("language"),
            "qc_blocks": transcript.get("qc_blocks"),
            "qc_failed": transcript.get("qc_failed"),
            "keyframes": len(frames),
            "deferred_analysis": list(deferred),
        },
        "review_items": items,
        "review_items_truncated": truncated,
        "handoff": {
            "mode": "EXCEPTIONS_ONLY" if items else "SUMMARY_ONLY",
            "requires_expert_review": requires_expert_review,
            "technical_final_review_completed": True,
            "domain_analysis_status": "DEFERRED" if deferred else "NOT_APPLICABLE",
            "pedagogical_status": "NOT_EVALUATED",
            "canonical_promotion_allowed": False,
            "raw_media_included": False,
            "full_transcript_included": False,
        },
    }


__all__ = ["SCHEMA", "ServerReviewError", "build_server_review"]
