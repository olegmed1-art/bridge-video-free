#!/usr/bin/env python3
"""Quality v4.2: evidence-linked learning plus conservative visual board recovery.

v4.2 preserves all v4.1 interaction, identity, authority, source-read-only and
zero-paid-AI gates.  Its only semantic addition is to admit independently parsed
report-visual card observations as ordinary deal candidates.  Those candidates
remain PARTIAL_BOARD unless the inherited 52-unique-card gate actually proves a
full deal; hidden cards are never inferred by complement.

The v4.1/v2 builder historically stamps wall-clock ``created_at`` on every
semantic rebuild. v4.2 deliberately overwrites that volatile field with a
stable master-derived timestamp so identical evidence produces identical
content-addressed Drive artifacts on repeat runs.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

import diana_longitudinal_quality_v4_1 as v41
from bridge_contracts.video_dds_decision_comparison import DDSRequestExecutor
from bridge_contracts.video_extended_extraction import build_extended_extraction
from bridge_contracts.video_learning_feedback import CorrectionReceiptResolver
from bridge_contracts.video_canon_auto_pipeline import run_video_canon_auto_pipeline

QUALITY_SCHEMA = v41.QUALITY_SCHEMA
QUALITY_SCHEMA_VERSION = 5
QUALITY_METHOD_VERSION = "diana-quality-v4.2"


def _stable_quality_created_at(master: Mapping[str, Any]) -> str:
    for key in ("createdAt", "created_at"):
        value = str(master.get(key) or "").strip()
        if value:
            return value
    # Deterministic fail-closed fallback. A rebuild timestamp is metadata about
    # execution, not evidence content, and therefore must not change identity.
    return "1970-01-01T00:00:00Z"


def _pass_verified_integration_evidence(
    master: Mapping[str, Any], quality: dict[str, Any]
) -> None:
    """Route upstream proof receipts to their fail-closed consumers.

    The v4.2 master is the integration boundary used by the real Diana job.
    Keeping these collections only in synthetic ``quality`` dictionaries made
    the standalone validators unreachable in production.  This adapter does
    not declare any item valid: it preserves the supplied collection exactly,
    records whether its container was admissible, and lets the dedicated DDS
    and correction validators prove every field and digest.
    """
    fields = (
        "verified_full_board_evidence",
        "source_bound_logic_evidence",
        "correction_review_receipts",
    )
    integration: dict[str, Any] = {
        "source": "analysis_master",
        "validation": "DEDICATED_DOWNSTREAM_FAIL_CLOSED",
        "collections": {},
    }
    for field in fields:
        raw = master.get(field)
        if raw is None:
            quality[field] = []
            integration["collections"][field] = {
                "status": "NOT_SUPPLIED", "item_count": 0,
            }
        elif isinstance(raw, list):
            quality[field] = deepcopy(raw)
            integration["collections"][field] = {
                "status": "PASSED_TO_VALIDATOR", "item_count": len(raw),
            }
        else:
            # Preserve the invalid value so the dedicated consumer emits an
            # explicit gap instead of silently treating malformed proof as
            # absent evidence.
            quality[field] = deepcopy(raw)
            integration["collections"][field] = {
                "status": "INVALID_CONTAINER_PASSED_TO_VALIDATOR",
                "item_count": 0,
            }
    quality["integrated_verification_evidence"] = integration


def build_quality_layer(
    master: Mapping[str, Any],
    lesson_identity: Mapping[str, Any] | None = None,
    *,
    dds_request_executor: DDSRequestExecutor | None = None,
    correction_receipt_resolver: CorrectionReceiptResolver | None = None,
) -> dict[str, Any]:
    working = deepcopy(dict(master))
    raw_deals = [dict(item) for item in (working.get("deals") or []) if isinstance(item, Mapping)]
    visual_deals = [
        dict(item)
        for item in (working.get("report_visual_board_deals") or [])
        if isinstance(item, Mapping)
    ]
    existing_ids = {str(item.get("deal_id") or "") for item in raw_deals}
    for deal in visual_deals:
        if str(deal.get("deal_id") or "") not in existing_ids:
            raw_deals.append(deal)
    working["deals"] = raw_deals

    quality = deepcopy(v41.build_quality_layer(working, lesson_identity))
    quality["schema_version"] = QUALITY_SCHEMA_VERSION
    quality["method_version"] = QUALITY_METHOD_VERSION
    quality["created_at"] = _stable_quality_created_at(working)

    reconstruction = working.get("report_visual_board_reconstruction")
    if not isinstance(reconstruction, Mapping):
        reconstruction = {}
    qc = reconstruction.get("qc") if isinstance(reconstruction.get("qc"), Mapping) else {}
    deal_results = [x for x in (quality.get("deal_reconstructions") or []) if isinstance(x, Mapping)]
    visual_ids = {str(item.get("deal_id") or "") for item in visual_deals}
    visual_results = [
        item for item in deal_results
        if str(item.get("deal_candidate_id") or "") in visual_ids
    ]
    visual_partial = sum(item.get("board_status") == "PARTIAL_BOARD" for item in visual_results)
    visual_full = sum(item.get("board_status") == "VERIFIED_FULL_BOARD" for item in visual_results)

    quality["board_reconstruction_v4_2"] = {
        "method_version": reconstruction.get("method_version"),
        "parser_scope": reconstruction.get("parser_scope"),
        "report_visual_observations": int(qc.get("report_visual_observation_count") or 0),
        "report_visual_clusters": int(qc.get("board_cluster_count") or 0),
        "report_visual_deal_candidates": len(visual_deals),
        "report_visual_partial_boards": visual_partial,
        "report_visual_verified_full_boards": visual_full,
        "recognized_card_union_total": int(qc.get("recognized_card_union_total") or 0),
        "hidden_hand_complement_inference_allowed": False,
        "time_topic_board_number_identity_allowed": False,
        "full_board_requires_52_unique_cards": True,
        "source_media_reprocessing_required": False,
        "note": (
            "Report screenshots may prove visible partial hands. A full board is never inferred "
            "from missing cards and remains subject to the inherited 52-unique-card gate."
        ),
    }
    counts = quality.setdefault("counts", {})
    counts["report_visual_board_observations_v4_2"] = int(qc.get("report_visual_observation_count") or 0)
    counts["report_visual_board_clusters_v4_2"] = int(qc.get("board_cluster_count") or 0)
    counts["report_visual_partial_boards_v4_2"] = visual_partial
    counts["report_visual_verified_full_boards_v4_2"] = visual_full
    counts["report_visual_recognized_cards_v4_2"] = int(qc.get("recognized_card_union_total") or 0)

    authority = quality.setdefault("authority", {})
    authority.update({
        "canon_activation": "DENY",
        "curriculum_activation": "DENY",
        "methodology_activation": "DENY",
        "student_profile_production_write": "DENY",
        "student_skill_state_production_write": "DENY",
        "person_specific_learning_conclusion": "DENY",
        "database_destination": "STAGING_ONLY",
    })
    incremental = quality.setdefault("incremental_processing", {})
    incremental.update({
        "semantic_only_rebuild_supported": True,
        "heavy_video_reprocessing_required": False,
        "raw_asr_mutated": False,
        "report_pdf_reused_for_board_reconstruction": True,
    })
    cost = quality.setdefault("cost_gate", {})
    cost.update({
        "paid_ai_api_required": False,
        "paid_cloud_required": False,
        "heavy_video_reprocessing_for_this_layer": False,
        "reuses_existing_transcript_and_evidence": True,
    })
    _pass_verified_integration_evidence(working, quality)
    quality["integrated_verification_evidence"]["trusted_resolvers"] = {
        "pinned_dds_rerun": dds_request_executor is not None,
        "correction_review_storage": correction_receipt_resolver is not None,
    }
    extended = build_extended_extraction(
        working,
        quality,
        dds_request_executor=dds_request_executor,
        correction_receipt_resolver=correction_receipt_resolver,
    )
    quality["extended_knowledge_extraction"] = extended
    staging = quality.setdefault("candidate_staging_records", [])
    staging.extend(extended["candidate_records"])
    counts["extended_knowledge_candidates"] = len(extended["candidate_records"])
    counts["extended_knowledge_by_type"] = extended["counts_by_type"]
    counts["staging_records"] = len(staging)
    learning_candidate = working.get("video_canon_learning_candidate")
    assertions = working.get("video_canon_assertions")
    verifications = working.get("video_canon_verification_bundles")
    if isinstance(learning_candidate, Mapping) and isinstance(assertions, list) and isinstance(verifications, Mapping):
        auto_pipeline = run_video_canon_auto_pipeline(
            learning_candidate, assertions, verifications
        )
    else:
        auto_pipeline = {
            "schema": "video-canon-auto-pipeline-v1",
            "status": "NOT_REQUESTED",
            "candidates": [],
            "promotion_commands": [],
            "gaps": [],
            "human_approval_required": False,
            "world_lookup_performed": False,
            "authoritative_write_performed": False,
        }
    quality["video_canon_auto_pipeline"] = auto_pipeline
    staging.extend(auto_pipeline["candidates"])
    counts["video_canon_auto_promotions_ready"] = len(auto_pipeline["promotion_commands"])
    counts["video_canon_auto_gaps"] = len(auto_pipeline["gaps"])
    counts["video_canon_candidates"] = len(auto_pipeline["candidates"])
    counts["staging_records"] = len(staging)
    return quality


__all__ = [
    "QUALITY_SCHEMA",
    "QUALITY_SCHEMA_VERSION",
    "QUALITY_METHOD_VERSION",
    "_stable_quality_created_at",
    "_pass_verified_integration_evidence",
    "build_quality_layer",
]
