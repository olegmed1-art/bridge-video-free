"""Bounded source normalization only; never creates verified canon inputs."""
from copy import deepcopy
import hashlib
import json
import math
import re

SCHEMA = "video-source-knowledge-draft-v1"
PACKET_SCHEMA = "video-bounded-source-inspection-v1"


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def normalize_source_packet(packet):
    """Preserve source claims and extract only a narrow, explicitly quoted relation.

    This separate draft schema deliberately permits UNKNOWN. It is not the
    completed-learning contract and must never bootstrap a trusted resolver.
    """
    gaps = []
    def gap(code, path):
        gaps.append({"code": code, "path": path})
    if not isinstance(packet, dict):
        packet = {"unrecognized_input": deepcopy(packet)}
    if packet.get("schema") != PACKET_SCHEMA:
        gap("SOURCE_SCHEMA_INVALID", "schema")
    def obj(key):
        value = packet.get(key)
        if not isinstance(value, dict):
            gap("SOURCE_FIELD_INVALID", key)
            return {}
        return value
    source = obj("video_source_claim")
    document = obj("source_document")
    segment = obj("selected_transcript_segment")
    episode = obj("episode_context")
    identity = obj("speaker_identity_claim")
    for label, record, fields in (
        ("video_source_claim", source, ("drive_file_id",)),
        ("source_document", document, ("drive_file_id",)),
        ("selected_transcript_segment", segment, ("field_path", "segment_id", "master_speaker_cluster")),
        ("episode_context", episode, ("field_path", "episode_id")),
    ):
        for field in fields:
            if not isinstance(record.get(field), str) or not record[field].strip():
                gap("SOURCE_FIELD_INVALID", label + "." + field)
    # Validate digest shape throughout the bounded input; no claim proves bytes.
    def walk(value, path="$"):
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = path + "." + key
                if key.endswith("sha256") and (not isinstance(child, str) or
                        not re.fullmatch("[0-9a-f]{64}", child)):
                    gap("DIGEST_INVALID", child_path)
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
    walk(packet)
    for record, field, path in (
        (source, "source_sha256", "video_source_claim"),
        (document, "embedded_raw_sha256", "source_document"),
        (document, "embedded_canonical_sort_compact_utf8_sha256", "source_document"),
        (document, "source_map_declared_master_sha256", "source_document"),
        (segment, "exact_text_utf8_sha256", "selected_transcript_segment"),
    ):
        if field not in record:
            gap("DIGEST_MISSING", path + "." + field)
    text = segment.get("exact_text")
    computed = hashlib.sha256(text.encode("utf-8")).hexdigest() if isinstance(text, str) else None
    if computed is None or computed != segment.get("exact_text_utf8_sha256"):
        gap("TEXT_DIGEST_MISMATCH", "selected_transcript_segment.exact_text_utf8_sha256")
    if document.get("embedded_canonical_sort_compact_utf8_sha256") != document.get("source_map_declared_master_sha256"):
        gap("PARENT_CANONICAL_DIGEST_MISMATCH", "source_document.source_map_declared_master_sha256")
    def interval(record):
        values = [record.get(key) for key in ("start", "end")]
        try:
            return all(type(v) in (int, float) and math.isfinite(v) for v in values) and 0 <= values[0] < values[1]
        except OverflowError:
            return False
    if not interval(segment) or not interval(episode) or not (
            episode["start"] <= segment["start"] < segment["end"] <= episode["end"]):
        gap("SOURCE_INTERVAL_INVALID", "selected_transcript_segment")
    if identity.get("cluster_id") != segment.get("master_speaker_cluster"):
        gap("SPEAKER_CLUSTER_MISMATCH", "speaker_identity_claim.cluster_id")
    gaps.extend(source_constraint_gaps(packet))
    if packet.get("input_builder_conflicts"):
        gap("INPUT_BUILDER_CONFLICT", "input_builder_conflicts")
    integrity_ok = not gaps
    # Only an affirmative opening with an explicit causal connector is supported.
    # Unmatched wording is retained verbatim, never completed by a language model.
    relation = None
    patterns = (
        r"(?P<action>Develop (?P<suit>clubs|diamonds|hearts|spades)) because (?P<reason>they provide (?P<tricks>[1-9]|1[0-3]) tricks)",
        r"(?P<action>Разыгрывайте (?P<suit>трефу|бубну|черву|пику)), потому что (?P<reason>это даст (?P<tricks>[1-9]|1[0-3]) взят(?:ку|ки|ок))",
    )
    match = None
    if isinstance(text, str):
        for pattern in patterns:
            match = re.fullmatch(pattern + r"[.!]?", text)
            if match:
                break
    suits = {"clubs": "CLUBS", "diamonds": "DIAMONDS", "hearts": "HEARTS", "spades": "SPADES",
             "трефу": "CLUBS", "бубну": "DIAMONDS", "черву": "HEARTS", "пику": "SPADES"}
    if integrity_ok and match:
        relation = {
            "kind": "SOURCE_CLAIM_NOT_GENERAL_RULE", "action": "DEVELOP_SUIT",
            "suit": suits[match.group("suit")], "claimed_tricks": int(match.group("tricks")),
            "action_quote": match.group("action"), "reason_quote": match.group("reason"),
            "causal_quote": text[:match.end("reason")],
            "quote_offsets": {key: list(match.span(key)) for key in ("action", "reason")},
            "transcript_locator": segment.get("field_path"),
            "text_sha256": computed,
        }
    else:
        gap("EXTRACTION_AMBIGUOUS", "selected_transcript_segment.exact_text")
    for code, path in (
        ("TEACHER_IDENTITY_UNPROVEN", "speaker_identity_claim"),
        ("AUDIO_TEXT_UNVERIFIED", "selected_transcript_segment"),
        ("SOURCE_MEDIA_UNVERIFIED", "video_source_claim.source_sha256"),
        ("FRAME_BYTES_UNVERIFIED", "frame_inventory_candidates"),
        ("SPEECH_FRAME_BINDING_UNPROVEN", "frame_binding_status"),
        ("BRIDGE_CONTEXT_UNKNOWN", "bridge_context"),
        ("LEARNING_OUTCOME_UNKNOWN", "episode_context"),
        ("SOURCE_AUTHORIZATION_UNPROVEN", "source_authorization"),
        ("INDEPENDENT_VERIFICATION_MISSING", "verification_receipts"),
    ):
        gap(code, path)
    return {
        "schema": SCHEMA, "status": "CANDIDATE_RESEARCH", "acceptance_status": "BLOCKED",
        "extraction_status": "QUOTED_RELATION_EXTRACTED" if relation else "EXTRACTION_AMBIGUOUS",
        "semantic_domain": "CARD_PLAY" if relation else "UNKNOWN",
        "input_packet_sha256": _hash(packet),
        "source_claim": deepcopy(source), "parent_document_claim": deepcopy(document),
        "transcript": deepcopy(segment), "computed_text_sha256": computed,
        "episode_claim": deepcopy(episode), "speaker_claim": deepcopy(identity),
        "speaker_map_context_claim": deepcopy(packet.get("speaker_map_context_claim", {})),
        "frame_inventory_claims": deepcopy(packet.get("frame_inventory_candidates", [])),
        "source_constraints": deepcopy(packet.get("source_constraints", {})),
        "input_builder_lineage": deepcopy(packet.get("input_builder_lineage")),
        "input_builder_conflicts": deepcopy(packet.get("input_builder_conflicts", [])),
        "source_integrity_status": "INTERNAL_CHECKS_PASSED" if integrity_ok else "INVALID",
        "knowledge": relation,
        "unknown": {key: "UNKNOWN" for key in (
            "verified_teacher", "audio_accuracy", "frame_binding", "source_authorization",
            "board", "deal", "auction", "contract", "student_action", "learning_outcome",
            "general_rule_applicability", "independent_verification")},
        "gaps": gaps,
        "authority": {"accepted_canon_input": False, "school_canon_write_allowed": False,
                      "promotion_allowed": False, "student_facing_use_allowed": False},
    }


def source_constraint_gaps(packet):
    """Annotations can add review blockers, never clear them or confer trust.

    Keep the complete annotation in the receipt. Even a claimed resolution is
    untrusted until a separately reviewed reader handles it; this adapter does
    not implement that release path.
    """
    if "source_constraints" not in packet:
        return []
    constraints = packet["source_constraints"]
    def gap(code, path):
        return {"code": code, "path": path, "reason": "Existing source constraint requires independent review"}
    if not isinstance(constraints, dict) or not constraints:
        return [gap("SOURCE_CONSTRAINTS_INVALID", "source_constraints")]
    result = [gap("SOURCE_CONSTRAINT_REVIEW_REQUIRED", "source_constraints")]
    for key, code in (
        ("example_end", "EXAMPLE_BOUNDARY_UNVERIFIED"),
        ("excluded_next_segment_refs", "CROSS_EXAMPLE_EVIDENCE_EXCLUDED"),
        ("missing_episode_refs", "EPISODE_EVIDENCE_INCOMPLETE"),
        ("overlap", "OVERLAP_UNRESOLVED"),
        ("identity_lineage", "IDENTITY_LINEAGE_REVIEW_REQUIRED"),
        ("frame_boundary_conflicts", "FRAME_BOUNDARY_CONFLICT"),
    ):
        if key in constraints:
            result.append(gap(code, "source_constraints." + key))
    return result
