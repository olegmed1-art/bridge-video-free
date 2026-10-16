"""Offline book staging contract. Never authorizes publication or Canon writes.

Consumes already extracted, source-anchored records; it is not an OCR/LLM parser.
Indexes are zero-based PDF indexes; printed labels remain separate. New object
identity excludes run/model/review metadata. Existing registry IDs require an
explicit identity mapping; this module must not remint accepted legacy objects.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import unicodedata
from typing import Any
from .book_source_identity import is_public_book_locator

SCHEMA = "book-material-staging-v1"
SHA = re.compile(r"[0-9a-f]{64}\Z")
KINDS = {"POSITION", "EXERCISE", "AUTHOR_ANSWER", "ATOM", "DDS_RESULT", "SINGLE_DUMMY"}
RELATIONS = {"SUPPORTS", "CONTRADICTS", "EXEMPLIFIES", "COUNTEREXAMPLE_OF",
             "EXERCISES", "ANSWERS", "DERIVED_FROM", "SUPERSEDES", "AFFECTS"}


class InvalidBookMaterial(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise InvalidBookMaterial(code)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _fields(value: Any, required: set[str], code: str) -> None:
    _require(isinstance(value, dict) and set(value) == required, code)


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", value).split())
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    return value


def identity(source: dict, record: dict) -> str:
    """Content plus stable rendition/page/region anchor; excludes extraction hash."""
    anchor = record["anchor"]
    return digest({"edition": source["edition_id"], "rendition": source["rendition_sha256"],
                   "page": anchor["pdf_page_index"], "bbox": [float(v) for v in anchor["bbox"]],
                   "bbox_units": anchor["bbox_units"],
                   "kind": record["kind"], "content": _content(record)})


def _content(record: dict) -> dict:
    content = _normalize(record["content"])
    if record["kind"] == "POSITION":
        validate_position(content)
        for hand in content["hands"].values():
            for ranks in hand.values():
                ranks.sort(key=lambda rank: list("AKQJT98765432").index(rank) if rank != "UNKNOWN" else 13)
    return content


def validate_position(content: dict) -> bool:
    """Return exact-52 eligibility. UNKNOWN represents one unprinted card slot."""
    _require(isinstance(content, dict) and set(content) in (
        {"position_type", "hands"}, {"position_type", "hands", "play_state"}), "POSITION_FIELDS")
    _require(content["position_type"] in {"EXACT_FULL", "PARTIAL", "END"}, "POSITION_TYPE")
    hands = content["hands"]
    _require(isinstance(hands, dict) and bool(hands) and set(hands) <= set("NESW"), "HANDS")
    seen: set[str] = set()
    counts = []
    seat_counts = {}
    unknown = False
    for seat, hand in hands.items():
        _require(isinstance(hand, dict) and set(hand) <= set("SHDC") and bool(hand), "SUITS")
        count = 0
        for suit, ranks in hand.items():
            _require(isinstance(ranks, list) and len(ranks) <= 13, "RANKS")
            for rank in ranks:
                _require(isinstance(rank, str) and (rank == "UNKNOWN" or rank in list("AKQJT98765432")), "RANK")
                if rank == "UNKNOWN":
                    unknown = True
                else:
                    card = suit + rank
                    _require(card not in seen, "DUPLICATE_CARD")
                    seen.add(card)
                count += 1
        _require(count <= 13, "HAND_OVERFLOW")
        counts.append(count)
        seat_counts[seat] = count
    exact = (set(hands) == set("NESW") and all(set(h) == set("SHDC") for h in hands.values())
             and counts == [13] * 4 and len(seen) == 52 and not unknown)
    if content["position_type"] == "EXACT_FULL":
        _require(exact, "EXACT_52_13_REQUIRED")
    if "play_state" in content:
        _validate_play_state(content["play_state"], seat_counts, seen)
        if content["position_type"] == "EXACT_FULL":
            _require(content["play_state"]["trick_phase"] == "BETWEEN_TRICKS"
                     and content["play_state"]["tricks_completed"] == 0, "EXACT_FULL_INITIAL_STATE")
    return exact and content["position_type"] == "EXACT_FULL"


def _validate_play_state(state: dict, counts: dict, remaining_cards: set[str]) -> None:
    """Phase-aware inventory only; no hidden-card inference or play legality claim."""
    _fields(state, {"trick_phase", "tricks_completed", "leader", "next_player",
                    "played_cards", "inventory_complete"}, "PLAY_STATE_FIELDS")
    _require(state["trick_phase"] in {"BETWEEN_TRICKS", "IN_TRICK"}, "TRICK_PHASE")
    _require(type(state["tricks_completed"]) is int and 0 <= state["tricks_completed"] <= 12, "TRICKS_COMPLETED")
    _require(isinstance(state["leader"], str) and state["leader"] in list("NESW")
             and isinstance(state["next_player"], str) and state["next_player"] in list("NESW"), "PLAY_SEAT")
    _require(type(state["inventory_complete"]) is bool, "INVENTORY_COMPLETENESS")
    played = state["played_cards"]
    _require(isinstance(played, list) and (1 <= len(played) <= 3 if state["trick_phase"] == "IN_TRICK" else len(played) == 0), "PLAYED_CARD_COUNT")
    seats = "NESW"
    leader = seats.index(state["leader"])
    played_seats, seen = set(), set(remaining_cards)
    for index, card in enumerate(played):
        _fields(card, {"seat", "suit", "rank", "origin"}, "PLAYED_CARD_FIELDS")
        _require(card["seat"] == seats[(leader + index) % 4], "PLAY_ORDER")
        _require(isinstance(card["suit"], str) and card["suit"] in list("SHDC")
                 and isinstance(card["rank"], str) and card["rank"] in [*"AKQJT98765432", "UNKNOWN"]
                 and card["origin"] in {"AUTHOR", "INFERENCE"}, "PLAYED_CARD_SYMBOL")
        if card["rank"] != "UNKNOWN":
            token = card["suit"] + card["rank"]
            _require(token not in seen, "PLAYED_CARD_DUPLICATE")
            seen.add(token)
        played_seats.add(card["seat"])
    _require(state["next_player"] == seats[(leader + len(played)) % 4], "NEXT_PLAYER")
    if state["inventory_complete"]:
        _require(set(counts) == set(seats), "COMPLETE_INVENTORY_SEATS")
        expected = 13 - state["tricks_completed"]
        _require(all(counts[seat] == expected - (seat in played_seats) for seat in seats), "PHASE_CARD_COUNT")


def validate_bundle(bundle: dict) -> None:
    _fields(bundle, {"schema", "source", "run", "objects", "relations", "legacy_ids",
                     "processing", "publication"}, "BUNDLE_FIELDS")
    _require(bundle["schema"] == SCHEMA and bundle["processing"] == "STAGED"
             and bundle["publication"] == "NOT_PUBLISHED", "STAGING_ONLY")
    source = bundle["source"]
    _fields(source, {"source_id", "edition_id", "rendition_sha256", "title", "locator", "page_count"}, "SOURCE_FIELDS")
    _require(all(_text(source[k]) for k in ("source_id", "edition_id", "title", "locator")), "SOURCE_TEXT")
    _require(isinstance(source["rendition_sha256"], str) and bool(SHA.fullmatch(source["rendition_sha256"])), "SOURCE_HASH")
    _require(type(source["page_count"]) is int and source["page_count"] > 0, "PAGE_COUNT")
    _require(is_public_book_locator(source["locator"]), "CANONICAL_LOCATOR_REQUIRED")
    _fields(bundle["run"], {"run_id", "model_version", "prompt_version"}, "RUN_FIELDS")
    _require(all(_text(v) for v in bundle["run"].values()), "RUN_TEXT")
    legacy = bundle["legacy_ids"]
    _require(isinstance(legacy, dict) and all(isinstance(k, str) and SHA.fullmatch(k) and _text(v)
                                            for k, v in legacy.items()), "LEGACY_MAPPING")
    _require(len(set(legacy.values())) == len(legacy), "LEGACY_ID_COLLISION")
    objects = bundle["objects"]
    _require(isinstance(objects, list) and 0 < len(objects) <= 1000, "BOUNDED_OBJECTS")
    by_id = {}
    for obj in objects:
        _fields(obj, {"object_id", "kind", "anchor", "content", "origin", "verification",
                      "review", "rights", "errata_ids"}, "OBJECT_FIELDS")
        _require(_text(obj["object_id"]) and obj["object_id"] not in by_id, "DUPLICATE_ID")
        _require(obj["kind"] in KINDS and obj["origin"] in {"AUTHOR", "EDITOR", "INFERENCE"}, "OBJECT_KIND_ORIGIN")
        _require(obj["verification"] in {"M0", "M1", "M2", "NOT_APPLICABLE"}
                 and obj["review"] in {"NOT_REVIEWED", "NEEDS_REVIEW", "REVIEWED"}
                 and obj["rights"] in {"INTERNAL_ALLOWED", "RESTRICTED", "NEEDS_RIGHTS"}, "OBJECT_AXES")
        _require(isinstance(obj["errata_ids"], list) and all(_text(x) for x in obj["errata_ids"]), "ERRATA_IDS")
        anchor = obj["anchor"]
        _fields(anchor, {"pdf_page_index", "printed_page_label", "bbox", "bbox_units",
                         "segment_sha256", "segment_hash_method"}, "ANCHOR_FIELDS")
        _require(type(anchor["pdf_page_index"]) is int and 0 <= anchor["pdf_page_index"] < source["page_count"], "PDF_INDEX")
        _require(anchor["printed_page_label"] is None or _text(anchor["printed_page_label"]), "PRINTED_LABEL")
        _require(anchor["bbox_units"] in {"NORMALIZED_TOP_LEFT", "PDF_POINTS_TOP_LEFT"}, "BBOX_UNITS")
        _require(anchor["segment_hash_method"] in {"CROP_BYTES_SHA256", "RENDER_BYTES_SHA256", "NORMALIZED_TEXT_UTF8_SHA256"}, "SEGMENT_HASH_METHOD")
        box = anchor["bbox"]
        _require(isinstance(box, list) and len(box) == 4 and all(type(x) in (int, float)
                 and math.isfinite(x) and x >= 0 for x in box), "FINITE_BBOX")
        if anchor["bbox_units"] == "NORMALIZED_TOP_LEFT":
            _require(all(x <= 1 for x in box), "NORMALIZED_BBOX")
        _require(box[0] < box[2] and box[1] < box[3], "BBOX_ORDER")
        _require(isinstance(anchor["segment_sha256"], str) and bool(SHA.fullmatch(anchor["segment_sha256"])), "SEGMENT_HASH")
        _require(isinstance(obj["content"], dict), "CONTENT_OBJECT")
        key = identity(source, obj)
        _require(obj["object_id"] == legacy.get(key, "BOOK-" + key), "IDENTITY_MISMATCH")
        by_id[obj["object_id"]] = obj
    _require(set(legacy) <= {identity(source, obj) for obj in objects}, "UNUSED_LEGACY_MAPPING")
    for obj in objects:
        content, kind = obj["content"], obj["kind"]
        if kind == "POSITION":
            validate_position(content)
        elif kind == "ATOM":
            _fields(content, {"statement", "domain", "conditions", "exceptions"}, "ATOM_FIELDS")
            _require(_text(content["statement"]) and content["domain"] in {"CARD_PLAY", "DEFENSE"}, "WORLD_CARDPLAY_ONLY")
            for field in ("conditions", "exceptions"):
                _require(isinstance(content[field], list) and all(_text(v) for v in content[field]), "ATOM_SCOPE")
        else:
            if kind == "EXERCISE":
                _fields(content, {"question", "position_id"}, "EXERCISE_FIELDS")
                _require(_text(content["question"]), "QUESTION")
            elif kind == "AUTHOR_ANSWER":
                _fields(content, {"statement", "exercise_id"}, "ANSWER_FIELDS")
                _require(obj["origin"] == "AUTHOR" and _text(content["statement"]), "AUTHOR_ANSWER_ORIGIN")
            elif kind == "SINGLE_DUMMY":
                _fields(content, {"statement", "position_id", "available_information"}, "SINGLE_DUMMY_FIELDS")
                _require(_text(content["statement"]) and _text(content["available_information"]), "SINGLE_DUMMY_CONTEXT")
            elif kind == "DDS_RESULT":
                _fields(content, {"position_id", "solver", "solver_version", "input_sha256", "claim", "result"}, "DDS_FIELDS")
                _require(obj["origin"] == "INFERENCE" and all(_text(content[x]) for x in ("solver", "solver_version", "claim", "result")), "DDS_PROVENANCE")
            link = "exercise_id" if kind == "AUTHOR_ANSWER" else "position_id"
            target = by_id.get(content[link]) if isinstance(content[link], str) else None
            _require(target is not None and target["kind"] == ("EXERCISE" if link == "exercise_id" else "POSITION"), "FOREIGN_KEY")
            if kind == "DDS_RESULT":
                _require(validate_position(target["content"]) and not target["errata_ids"], "DDS_REQUIRES_EXACT_POSITION")
                _require(content["input_sha256"] == digest(target["content"]), "DDS_INPUT_HASH")
    relations = bundle["relations"]
    _require(isinstance(relations, list) and len(relations) <= 5000, "BOUNDED_RELATIONS")
    seen = set()
    for rel in relations:
        _fields(rel, {"from_id", "to_id", "type"}, "RELATION_FIELDS")
        _require(all(isinstance(rel[k], str) for k in rel), "RELATION_TEXT")
        _require(rel["from_id"] in by_id and rel["to_id"] in by_id and rel["type"] in RELATIONS, "RELATION_FK")
        if rel["type"] == "ANSWERS":
            answer, exercise = by_id[rel["from_id"]], by_id[rel["to_id"]]
            _require(answer["kind"] == "AUTHOR_ANSWER" and exercise["kind"] == "EXERCISE"
                     and answer["content"]["exercise_id"] == rel["to_id"], "ANSWER_RELATION_TYPE")
        key = canonical_json(rel)
        _require(key not in seen, "DUPLICATE_RELATION")
        seen.add(key)


def produce_bundle(source: dict, run: dict, records: list[dict], *,
                   relations: list[dict] | None = None, legacy_ids: dict | None = None) -> dict:
    """Stage bounded parsed records without granting review or publication status."""
    bundle = {"schema": SCHEMA, "source": deepcopy(source), "run": deepcopy(run),
              "objects": deepcopy(records), "relations": deepcopy(relations or []),
              "legacy_ids": deepcopy(legacy_ids or {}), "processing": "STAGED",
              "publication": "NOT_PUBLISHED"}
    for obj in bundle["objects"]:
        obj["content"] = _content(obj)
        key = identity(source, obj)
        obj["object_id"] = bundle["legacy_ids"].get(key, "BOOK-" + key)
        obj["verification"] = "M0"
        obj["review"] = "NOT_REVIEWED"
    validate_bundle(bundle)
    return bundle


def preview_citation(bundle: dict, object_id: str) -> dict:
    """Non-student-facing projection, explicitly lacking source verification."""
    validate_bundle(bundle)
    obj = next((x for x in bundle["objects"] if x["object_id"] == object_id), None)
    _require(obj is not None, "OBJECT_NOT_FOUND")
    _require(obj["rights"] == "INTERNAL_ALLOWED", "PREVIEW_RIGHTS_REQUIRED")
    source, anchor = bundle["source"], obj["anchor"]
    return {"authority_lane": "WORLD_EXTERNAL", "publication": "NOT_PUBLISHED",
            "source_bytes_verified": False, "student_delivery_allowed": False,
            "object_id": object_id, "content": deepcopy(obj["content"]),
            "citation": {"title": source["title"], "locator": source["locator"],
                         "source_id": source["source_id"], "edition_id": source["edition_id"],
                         "rendition_sha256": source["rendition_sha256"], **deepcopy(anchor)},
            "review": obj["review"], "errata_ids": list(obj["errata_ids"])}
