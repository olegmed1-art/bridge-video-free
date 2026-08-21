#!/usr/bin/env python3
"""Quality v4.2: evidence-linked learning plus conservative visual board recovery.

v4.2 preserves all v4.1 interaction, identity, authority, source-read-only and
zero-paid-AI gates.  Its only semantic addition is to admit independently parsed
report-visual card observations as ordinary deal candidates.  Those candidates
remain PARTIAL_BOARD unless the inherited 52-unique-card gate actually proves a
full deal; hidden cards are never inferred by complement.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

import diana_longitudinal_quality_v4_1 as v41

QUALITY_SCHEMA = v41.QUALITY_SCHEMA
QUALITY_SCHEMA_VERSION = 5
QUALITY_METHOD_VERSION = "diana-quality-v4.2"


def build_quality_layer(
    master: Mapping[str, Any],
    lesson_identity: Mapping[str, Any] | None = None,
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
    return quality


__all__ = [
    "QUALITY_SCHEMA",
    "QUALITY_SCHEMA_VERSION",
    "QUALITY_METHOD_VERSION",
    "build_quality_layer",
]
