"""Dry-run book-card adapter using the existing book-material-staging-v1 contract.

Source bytes and physical anchors are supplied as local evidence. The adapter
never guesses cards, promotes Canon, or grants publication.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from bridge_contracts.book_material import InvalidBookMaterial, produce_bundle, validate_position


class MissingBookEvidence(ValueError):
    pass


def _canonical_drive_locator(value: str) -> str:
    """Keep the Drive file identity while removing only its UI sharing parameter."""
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname != "drive.google.com"
            or parsed.fragment or parsed.query not in {"", "usp=drivesdk"}
            or not re.fullmatch(r"/file/d/[A-Za-z0-9_-]{20,128}/view", parsed.path)):
        raise MissingBookEvidence("BOOK_SOURCE_LOCATOR_INVALID")
    return "https://drive.google.com" + parsed.path


def _version(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _section(text: str, title: str) -> str:
    match = re.search(r"\*\*" + re.escape(title) + r"\*\*\s*(.*?)(?=\n\*\*|\Z)",
                      text, re.S)
    if not match:
        raise MissingBookEvidence("CARD_SECTION_MISSING:" + title)
    return " ".join(match.group(1).split())


def read_low_card(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    link = re.search(r"\[Bridge 04_final6\.pdf\]\((https://drive\.google\.com/file/d/[^)]+)\)", text)
    if not link or "не утверждённое правило школьного Canon" not in text:
        raise MissingBookEvidence("LOW_CARD_SOURCE_OR_CANDIDATE_STATUS_MISSING")
    return {
        "candidate_id": "bridge04_nt_low_card_trick",
        "candidate_version": _version(path),
        "status": "candidate_unpublished",
        "source_title": "Bridge 04_final6.pdf",
        "source_locator": _canonical_drive_locator(link.group(1)),
        "source_link": link.group(1),
        "printed_page": "26",
        "supporting_printed_pages": ["26", "28"],
        "statement": _section(text, "Применимость и действие:"),
        "conditions": [_section(text, "Точное утверждение источника, стр. 26:")],
        "exceptions": [_section(text, "Контрпример, стр. 28:")],
        "origin": "INFERENCE",
        "instructional_use_allowed": False,
        "review_flags": [],
    }


def read_entry(path: Path) -> dict:
    card = json.loads(path.read_text(encoding="utf-8"))
    if (card.get("schema_version") != "book_skill_candidate.v1"
            or card.get("status") != "candidate_unpublished"
            or card.get("derived_skill", {}).get("origin") != "our_inference_from_pages_12_15_27"
            or card.get("pedagogical_review_status") != "pending"
            or card.get("instructional_use_allowed") is not False
            or "counterexample" in card):
        raise MissingBookEvidence("ENTRY_CANDIDATE_STATUS_OR_VERSION_INVALID")
    review_file = path.parent / card.get("example_review_file", "")
    if not review_file.is_file():
        raise MissingBookEvidence("ENTRY_EXAMPLE_REVIEW_MISSING")
    review = json.loads(review_file.read_text(encoding="utf-8"))
    examples = review.get("examples", [])
    if (review.get("candidate_id") != card["id"]
            or {entry.get("status") for entry in examples} != {"rejected", "pending"}
            or any(entry.get("instructional_use_allowed") is not False for entry in examples)):
        raise MissingBookEvidence("ENTRY_EXAMPLE_REVIEW_INVALID")
    return {
        "candidate_id": card["id"],
        "candidate_version": card["schema_version"] + "+" + _version(path),
        "status": card["status"],
        "source_title": card["source"]["title"],
        "source_locator": _canonical_drive_locator(card["source"]["url"]),
        "source_link": card["source"]["url"],
        "printed_page": "12",
        "supporting_printed_pages": [str(page) for page in card["source"]["pages"]],
        "statement": card["derived_skill"]["action"],
        "conditions": [card["derived_skill"]["scope"]],
        "exceptions": [card["derived_skill"]["negative_condition"]],
        "origin": "INFERENCE",
        "instructional_use_allowed": False,
        "review_flags": [{"example_id": entry["id"], "status": entry["status"]}
                         for entry in examples],
    }


def _parse_printed_hands(printed: dict) -> dict:
    """Transcribe explicit suit/rank tokens; never add a missing suit or card."""
    suits = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}
    if not isinstance(printed, dict) or not printed:
        raise ValueError("PRINTED_HANDS_REQUIRED")
    result = {}
    for seat, text in printed.items():
        if seat not in "NESW" or not isinstance(text, str):
            raise ValueError("PRINTED_HANDS_INVALID")
        compact = "".join(text.split())
        chunks = re.findall(r"([♠♥♦♣])([^♠♥♦♣]+)", compact)
        if not chunks or "".join(suit + ranks for suit, ranks in chunks) != compact:
            raise ValueError("PRINTED_HANDS_INVALID")
        hand = {}
        for suit, token in chunks:
            code = suits[suit]
            if code in hand:
                raise ValueError("PRINTED_HANDS_INVALID")
            ranks = []
            while token:
                rank, token = ("T", token[2:]) if token.startswith("10") else (token[0], token[1:])
                if rank not in "AKQJT98765432":
                    raise ValueError("PRINTED_HANDS_INVALID")
                ranks.append(rank)
            hand[code] = ranks
        result[seat] = hand
    return result


def assess_illustrations(evidence: dict) -> list[dict]:
    """Validate transcribed source diagrams without filling any unseen cards.

    Invalid diagrams are reported and excluded; they do not invalidate independent
    text ATOMs from the same rendition. A claimed complete deal uses the existing
    exact-52 contract, while an unclaimed diagram remains PARTIAL.
    """
    source_hash = evidence["source"]["rendition_sha256"]
    fragments = {item["name"]: item for item in evidence.get("fragments", [])}
    illustrations = evidence.get("illustrations", [])
    if not isinstance(illustrations, list) or len(illustrations) > 100:
        raise MissingBookEvidence("ILLUSTRATION_LIST_INVALID")
    results, seen = [], set()
    for item in illustrations:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in seen:
            raise MissingBookEvidence("ILLUSTRATION_ID_INVALID")
        seen.add(item["id"])
        fragment = fragments.get(item.get("fragment_name"))
        if (item.get("source_rendition_sha256") != source_hash or fragment is None
                or fragment.get("anchor") != item.get("anchor")):
            raise MissingBookEvidence("ILLUSTRATION_SOURCE_BINDING_INVALID")
        full = item.get("claims_complete_deal")
        if type(full) is not bool:
            raise MissingBookEvidence("ILLUSTRATION_COMPLETENESS_CLAIM_REQUIRED")
        position_type = "EXACT_FULL" if full else "PARTIAL"
        try:
            hands = item.get("hands")
            validate_position({"position_type": position_type, "hands": hands})
            nonempty_hands = {seat: {suit: ranks for suit, ranks in hand.items() if ranks}
                              for seat, hand in hands.items()}
            if _parse_printed_hands(item.get("printed_hands")) != nonempty_hands:
                raise InvalidBookMaterial("TRANSCRIPTION_MISMATCH")
        except (InvalidBookMaterial, ValueError) as exc:
            status, diagnostic = "invalid", str(exc)
        else:
            status, diagnostic = "valid", "PARTIAL_ONLY" if not full else "EXACT_52_13"
        anchor = item["anchor"]
        results.append({"id": item["id"], "status": status,
                        "diagnostic_code": diagnostic,
                        "claims_complete_deal": full,
                        "pdf_page_index": anchor["pdf_page_index"],
                        "printed_page_label": anchor["printed_page_label"],
                        "source_rendition_sha256": source_hash,
                        "source_segment_sha256": anchor["segment_sha256"],
                        "included_in_bundle": False,
                        "student_delivery_allowed": False})
    return results


def verify_pdf_source(pdf_path: Path, evidence: dict) -> dict:
    """Recheck the local PDF bytes and every asserted text crop before dry-run."""
    import pdfplumber  # Optional offline PDF dependency; never used by staging itself.
    from pypdf import PdfReader

    source = evidence["source"]
    source_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if source_hash != source["rendition_sha256"]:
        raise MissingBookEvidence("PDF_RENDITION_HASH_MISMATCH")
    fragments = evidence.get("fragments", [])
    if not fragments:
        raise MissingBookEvidence("PDF_FRAGMENTS_REQUIRED")
    page_labels = PdfReader(str(pdf_path))
    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) != len(page_labels.pages) or len(pdf.pages) != source["page_count"]:
            raise MissingBookEvidence("PDF_PAGE_COUNT_MISMATCH")
        for fragment in fragments:
            anchor = fragment["anchor"]
            index = anchor["pdf_page_index"]
            if (not 0 <= index < len(pdf.pages)
                    or anchor["bbox_units"] != "NORMALIZED_TOP_LEFT"
                    or anchor["segment_hash_method"] != "NORMALIZED_TEXT_UTF8_SHA256"):
                raise MissingBookEvidence("PDF_FRAGMENT_ANCHOR_INVALID")
            page = pdf.pages[index]
            bbox = anchor["bbox"]
            crop = page.crop((bbox[0] * page.width, bbox[1] * page.height,
                              bbox[2] * page.width, bbox[3] * page.height))
            text = " ".join(unicodedata.normalize("NFC", crop.extract_text() or "").split())
            if (text != fragment["excerpt"]
                    or hashlib.sha256(text.encode("utf-8")).hexdigest() != anchor["segment_sha256"]
                    or not (page_labels.pages[index].extract_text() or "").strip().endswith(
                        anchor["printed_page_label"])):
                raise MissingBookEvidence("PDF_FRAGMENT_MISMATCH:" + fragment["name"])
    by_name = {fragment["name"]: fragment for fragment in fragments}
    for item in evidence.get("illustrations", []):
        fragment = by_name.get(item.get("fragment_name"))
        if fragment is None or any("".join(holding.split()) not in
                                   "".join(fragment["excerpt"].split())
                                   for holding in item["printed_hands"].values()):
            raise MissingBookEvidence("PDF_PRINTED_ILLUSTRATION_MISMATCH")
    return {"source_sha256": source_hash, "page_count": len(pdf.pages),
            "verified_fragment_count": len(fragments)}


def dry_run(candidates: list[dict], evidence: dict | None = None) -> dict:
    """Return a staging preview and import trace; no store or publication call.

    evidence is a locally verified descriptor with existing-contract ``source`` and
    per-candidate ``anchors``. Missing evidence is a pending result, never a fake
    hash, guessed PDF index, or accepted publication.
    """
    if not candidates or any(card.get("status") != "candidate_unpublished"
                             or card.get("origin") != "INFERENCE"
                             or card.get("instructional_use_allowed") is not False
                             or any(flag.get("status") not in {"rejected", "pending"}
                                    for flag in card.get("review_flags", []))
                             for card in candidates):
        raise MissingBookEvidence("CANDIDATE_UNPUBLISHED_REQUIRED")
    trace = [{"candidate_id": card["candidate_id"],
              "candidate_version": card["candidate_version"],
              "candidate_status": card["status"],
              "source_locator": card["source_locator"],
              "source_link": card.get("source_link", card["source_locator"]),
              "printed_page": card["printed_page"],
              "supporting_printed_pages": list(card["supporting_printed_pages"]),
              "instructional_use_allowed": False,
              "review_flags": list(card["review_flags"])}
             for card in candidates]
    if evidence is None:
        return {"state": "PENDING_SOURCE_ANCHORS", "trace": trace,
                "student_delivery_allowed": False, "canon_promotion_allowed": False,
                "missing": ["source_id", "edition_id", "rendition_sha256", "page_count",
                            "pdf_page_index", "bbox", "bbox_units", "segment_sha256",
                            "segment_hash_method"]}
    source = evidence["source"]
    if any(card["source_locator"] != source["locator"]
           or card["source_title"] != source["title"] for card in candidates):
        raise MissingBookEvidence("SOURCE_MISMATCH")
    anchors = evidence["anchors"]
    if set(anchors) != {card["candidate_id"] for card in candidates}:
        raise MissingBookEvidence("ANCHOR_SET_MISMATCH")
    illustration_diagnostics = assess_illustrations(evidence)
    records = []
    for card in candidates:
        anchor = anchors[card["candidate_id"]]
        if anchor.get("printed_page_label") != card["printed_page"]:
            raise MissingBookEvidence("PRINTED_PAGE_MISMATCH")
        records.append({"kind": "ATOM", "anchor": anchor,
                        "content": {"statement": card["statement"], "domain": "CARD_PLAY",
                                    "conditions": card["conditions"],
                                    "exceptions": card["exceptions"]},
                        "origin": card["origin"], "rights": "RESTRICTED", "errata_ids": []})
    run = {"run_id": "candidate-dry-run", "model_version": "none",
           "prompt_version": "book-candidate-adapter-v1"}
    bundle = produce_bundle(source, run, records)
    for item, obj in zip(trace, bundle["objects"]):
        item["staging_object_id"] = obj["object_id"]
        item["staging_publication"] = bundle["publication"]
        item["staging_review"] = obj["review"]
    return {"state": "STAGED_DRY_RUN", "trace": trace, "bundle": bundle,
            "illustration_diagnostics": illustration_diagnostics,
            "student_delivery_allowed": False, "canon_promotion_allowed": False}


def real_card_dry_run(low_card_path: Path, entry_path: Path) -> dict:
    return dry_run([read_low_card(low_card_path), read_entry(entry_path)])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Read two book candidates; never publish")
    parser.add_argument("low_card", type=Path)
    parser.add_argument("entry_card", type=Path)
    parser.add_argument("--evidence", type=Path, help="verified source and anchor descriptor")
    parser.add_argument("--pdf", type=Path, help="local original PDF for byte and crop verification")
    parser.add_argument("--output", type=Path, help="write the complete local dry-run result")
    args = parser.parse_args()
    cards = [read_low_card(args.low_card), read_entry(args.entry_card)]
    evidence = json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else None
    if args.pdf and evidence is None:
        parser.error("--pdf requires --evidence")
    pdf_verification = verify_pdf_source(args.pdf, evidence) if args.pdf else None
    result = dry_run(cards, evidence)
    if pdf_verification:
        result["pdf_verification"] = pdf_verification
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "bundle"},
                     ensure_ascii=False, indent=2))
