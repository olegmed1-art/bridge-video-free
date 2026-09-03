"""Build the complete, staging-only knowledge harvest from one video analysis."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from bridge_contracts.video_dds_decision_comparison import (
    VideoDDSComparisonError,
    build_offline_dds_comparison,
)
from bridge_contracts.video_learning_feedback import (
    VideoLearningFeedbackError,
    build_learning_feedback,
)


SCHEMA = "video-extended-extraction-v1"
_LOGIC_CUES = {
    "CAUSE": ("потому что", "так как", "из-за того", "причина"),
    "PURPOSE": ("для того чтобы", "чтобы", "для этого", "это нужно для"),
    "CONSEQUENCE": ("поэтому", "следовательно", "значит", "в результате"),
    "ALTERNATIVE_CONSEQUENCE": ("иначе",),
}


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _refs(item: Mapping[str, Any]) -> list[str]:
    values: list[object] = []
    for key in ("evidence_refs", "visual_evidence_refs", "evidence"):
        if isinstance(item.get(key), list):
            values.extend(item[key])
    return list(dict.fromkeys(str(value) for value in values if value))


def _record(job_id: str, kind: str, source: Mapping[str, Any], status: str) -> dict[str, Any]:
    payload = dict(source)
    evidence = _refs(source)
    stable_key = str(
        source.get("stable_key")
        or source.get("canon_observation_id")
        or source.get("teaching_effect_candidate_id")
        or source.get("error_mode_candidate_id")
        or source.get("asset_id")
        or source.get("gap_id")
        or _digest(payload)
    )
    return {
        "candidate_id": f"video_{_digest([job_id, kind, stable_key])[:20]}",
        "candidate_type": kind,
        "stable_key": stable_key,
        "quality_status": status,
        "promotion_status": "STAGING_ONLY",
        "promotion_allowed": False,
        "evidence_refs": evidence,
        "payload": payload,
        "method_version": SCHEMA,
    }


def _items(value: object) -> list[Mapping[str, Any]]:
    return [item for item in (value or []) if isinstance(item, Mapping)] if isinstance(value, list) else []


def _logic_relations(text: str) -> list[dict[str, str]]:
    """Preserve explicit logical links without filling either side."""
    low = text.casefold()
    relations: list[dict[str, str]] = []
    for relation_type, cues in _LOGIC_CUES.items():
        for cue in cues:
            offset = low.find(cue)
            if offset < 0:
                continue
            left = text[:offset].strip(" ,;:.-")
            right = text[offset + len(cue):].strip(" ,;:.-")
            relations.append({
                "relation_type": relation_type,
                "cue": cue,
                "left_clause": left,
                "right_clause": right,
            })
    return relations


def _automatic_explanations(
    master: Mapping[str, Any], quality: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Extract explicit causal teacher speech; never manufacture rationale."""
    transcripts = {
        str(item.get("segment_id")): item
        for item in _items(master.get("transcript"))
        if item.get("segment_id")
    }
    out: list[dict[str, Any]] = []
    for rule in _items(quality.get("canon_candidates")):
        rule_key = str(rule.get("stable_key") or rule.get("canon_observation_id") or "")
        if not rule_key:
            continue
        for ref in _refs(rule):
            segment = transcripts.get(ref)
            if not segment:
                continue
            role = str(segment.get("speaker_role") or segment.get("speaker_role_candidate") or "").casefold()
            try:
                confidence = float(segment.get("speaker_role_confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            text = re.sub(r"\s+", " ", str(segment.get("text") or "")).strip()
            low = text.casefold()
            relations = _logic_relations(text)
            if role != "teacher" or confidence < 0.8 or not text or not relations:
                continue
            out.append({
                "stable_key": f"why:{rule_key}:{ref}",
                "rule_stable_key": rule_key,
                "extraction_class": "EXPLICIT_CAUSAL_TEACHER_STATEMENT",
                "statement": text,
                "why_chain": [text],
                "logic_relations": relations,
                "explanation_dimensions": sorted({
                    relation["relation_type"] for relation in relations
                }),
                "speaker_role": "teacher",
                "speaker_role_confidence": confidence,
                "speaker_id": segment.get("speaker") or segment.get("speaker_id"),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "rejected_alternatives": [],
                "prerequisites": [],
                "example": None,
                "counterexample": None,
                "completeness": "PARTIAL_EXPLICIT_EXPLANATION",
                "status": "REVIEW_REQUIRED",
                "evidence_refs": [ref],
                "generated_rationale_allowed": False,
            })
    return out


def build_extended_extraction(
    master: Mapping[str, Any], quality: Mapping[str, Any]
) -> dict[str, Any]:
    """Normalize all useful video-derived artifacts without promoting any of them."""
    job_id = str(master.get("job_id") or "unknown")
    records: list[dict[str, Any]] = []

    for item in _items(quality.get("canon_candidates")):
        classification = str(item.get("classification") or "UNKNOWN")
        kind = (
            "GAP_OR_CONFLICT"
            if classification in {"NO_CANON_MATCH", "CANON_CONFLICT_CANDIDATE"}
            else "RULE_CANDIDATE"
        )
        records.append(_record(job_id, kind, item, classification))

    dynamic = quality.get("dynamic_learning_model")
    dynamic = dynamic if isinstance(dynamic, Mapping) else {}
    for item in _items(dynamic.get("teaching_intervention_effect_candidates")):
        records.append(_record(job_id, "TEACHING_PATTERN", item, str(item.get("effect_status") or "CANDIDATE")))
    for item in _items(dynamic.get("error_mode_candidates")):
        records.append(_record(job_id, "STUDENT_MISCONCEPTION", item, "CANDIDATE"))
    for item in _items(quality.get("reusable_assets")):
        records.append(_record(job_id, "EXERCISE_CANDIDATE", item, str(item.get("status") or "CANDIDATE")))
    for item in _items(quality.get("pending_learning_probes")):
        records.append(_record(job_id, "GAP_OR_CONFLICT", item, str(item.get("status") or "OPEN")))

    # These three families require explicit upstream observations.  The
    # adapter never invents terminology, chronology or external agreement.
    explicit_explanations = [dict(item) for item in _items(master.get("explanation_observations"))]
    automatic_explanations = _automatic_explanations(master, quality)
    explanations = explicit_explanations + automatic_explanations

    for field, kind in (
        ("terminology_observations", "SCHOOL_TERMINOLOGY"),
        ("system_evolution_observations", "SYSTEM_EVOLUTION_OBSERVATION"),
        ("world_comparison_links", "WORLD_COMPARISON_LINK"),
    ):
        for item in _items(master.get(field)):
            status = str(item.get("status") or "REVIEW_REQUIRED")
            records.append(_record(job_id, kind, item, status))
    for item in explanations:
        records.append(_record(
            job_id, "EXPLANATION_CANDIDATE", item,
            str(item.get("status") or "REVIEW_REQUIRED"),
        ))

    for raw in _items(master.get("dds_decision_evaluations")):
        try:
            comparison = build_offline_dds_comparison(raw)
        except VideoDDSComparisonError as exc:
            gap = {
                "stable_key": f"dds-gap:{_digest(raw)}",
                "gap_type": "DDS_DECISION_EVALUATION_INVALID",
                "status": "OPEN",
                "reason": str(exc),
                "evidence_refs": _refs(raw.get("decision") if isinstance(raw.get("decision"), Mapping) else {}),
            }
            records.append(_record(job_id, "GAP_OR_CONFLICT", gap, "OPEN"))
            continue
        records.append(_record(
            job_id, "DDS_DECISION_COMPARISON", comparison,
            "OFFLINE_EVALUATED",
        ))

    try:
        feedback = build_learning_feedback(master, quality)
    except VideoLearningFeedbackError as exc:
        feedback = None
        gap = {"stable_key": f"learning-feedback:{_digest(master.get('job_id'))}", "gap_type": "LEARNING_FEEDBACK_INVALID", "status": "OPEN", "reason": str(exc), "evidence_refs": []}
        records.append(_record(job_id, "GAP_OR_CONFLICT", gap, "OPEN"))
    if feedback is not None:
        for example in feedback["training_examples"]:
            records.append(_record(job_id, "ANALYZER_TRAINING_EXAMPLE", example, "REVIEWED_LABEL"))
        records.append(_record(job_id, "MODEL_IMPROVEMENT_PROPOSAL", feedback["model_improvement_proposal"], feedback["model_improvement_proposal"]["status"]))

    # A detected rule without a source-bound explanation is useful evidence,
    # but not yet a teachable unit.  Record the missing "why" explicitly.
    explained_rule_keys = {
        str(item.get("rule_stable_key") or "")
        for item in explanations
        if item.get("rule_stable_key")
    }
    for item in _items(quality.get("canon_candidates")):
        rule_key = str(item.get("stable_key") or item.get("canon_observation_id") or "")
        if rule_key and rule_key not in explained_rule_keys:
            gap = {
                "stable_key": f"explanation-gap:{rule_key}",
                "gap_type": "EXPLANATION_MISSING",
                "rule_stable_key": rule_key,
                "question": "Почему применяется это правило и почему отклоняются альтернативы?",
                "status": "OPEN",
                "evidence_refs": _refs(item),
            }
            records.append(_record(job_id, "GAP_OR_CONFLICT", gap, "OPEN"))

    technical = quality.get("readiness")
    technical = dict(technical) if isinstance(technical, Mapping) else {}
    technical.update({
        "source_algorithm_revision": master.get("algorithmRevision"),
        "source_sha256": (master.get("source") or {}).get("sha256")
        if isinstance(master.get("source"), Mapping) else None,
        "board_reconstruction": quality.get("board_reconstruction_v4_2"),
        "authority": quality.get("authority"),
    })
    records.append(_record(job_id, "ANALYSIS_QUALITY_EVIDENCE", technical, "OBSERVED"))

    by_type: dict[str, int] = {}
    for record in records:
        by_type[record["candidate_type"]] = by_type.get(record["candidate_type"], 0) + 1
    return {
        "schema": SCHEMA,
        "status": "STAGING_ONLY",
        "candidate_records": records,
        "counts_by_type": by_type,
        "explanation_extraction": {
            "explicit_upstream": len(explicit_explanations),
            "automatic_explicit_causal": len(automatic_explanations),
            "generated_rationale_allowed": False,
            "minimum_teacher_role_confidence": 0.8,
            "logic_dimensions": sorted(_LOGIC_CUES),
            "target_model": [
                "CONDITIONS", "CAUSE_OR_PURPOSE", "CONCLUSION", "ACTION",
                "CONSEQUENCES", "REJECTED_ALTERNATIVES",
            ],
        },
        "learning_feedback": feedback,
        "authority": {
            "canon_activation": "DENY",
            "curriculum_activation": "DENY",
            "student_profile_write": "DENY",
            "world_to_canon_promotion": "DENY",
        },
    }


__all__ = ["SCHEMA", "build_extended_extraction"]
