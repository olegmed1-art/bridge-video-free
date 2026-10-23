"""PBN 2.1 sidecars for individual machine observations, never canon promotion.

Partial original hands are unavailable hands ('-'), not end positions. Observed
subsets stay in ASCII JSON comments and in the original diagnostics. With an
unknown dealer we emit IMPORT format: N is only the seat ordering anchor.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from bridge_contracts.video_deal import (
    BRIDGE_VIDEO_DEAL_CONTRACT_VERSION, RANK_ORDER, SEATS, SUIT_ORDER,
    canonicalize_video_deal,
)

PBN_WRITER_VERSION = "bridge-observation-pbn-v1"
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]) *(?:\.|$)", re.I)
_TAG_NAMES = ("Event", "Site", "Date", "Board", "West", "North", "East", "South",
              "Dealer", "Vulnerable", "Deal", "Scoring", "Declarer", "Contract", "Result")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def observation_record_sha256(record: Mapping[str, Any]) -> str:
    """Binding key for optional external provenance; exact canonical JSON record."""
    return hashlib.sha256(_json(record).encode("ascii")).hexdigest()


def _truncate(text: str, limit: int) -> str:
    out = ""
    for char in text:
        if len((out + char).encode("utf-8")) > limit:
            break
        out += char
    return out


def safe_video_stem(title: Any, *, source_id: str) -> str:
    """One Unicode component; callers must still use a fixed output directory."""
    if not isinstance(title, str) or not title.strip():
        title = "UNKNOWN_TITLE-" + source_id
    title = unicodedata.normalize("NFC", title)
    clean = "".join("_" if c in '<>:"/\\|?*' or unicodedata.category(c).startswith("C") else c for c in title)
    while ".." in clean:
        clean = clean.replace("..", "_")
    clean = clean.strip(" .")
    if not clean:
        clean = "UNKNOWN_TITLE-" + hashlib.sha256(source_id.encode()).hexdigest()[:12]
    if _RESERVED.match(clean):
        clean = "_" + clean
    return _truncate(clean, 100).rstrip(" .")


def _observed_hands(record: Mapping[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    empty = {seat: [] for seat in SEATS}
    if record.get("status") == "CONFLICT" or record.get("conflicts"):
        return empty, ["CONFLICTING_OBSERVATION"]
    deal = record.get("deal")
    if deal is None:
        return empty, ["NO_CANONICAL_OBSERVATION"]
    try:
        if not isinstance(deal, Mapping) or deal.get("contract_version") != BRIDGE_VIDEO_DEAL_CONTRACT_VERSION:
            raise ValueError("UNSUPPORTED_DEAL_CONTRACT")
        if deal.get("derivations"):
            raise ValueError("DERIVED_CARDS_PROHIBITED")
        raw = deal["hands"]
        if not isinstance(raw, Mapping) or set(raw) != set(SEATS):
            raise ValueError("INVALID_SEATS")
        canonical = canonicalize_video_deal({"hands": {s: raw[s]["cards"] for s in SEATS}}).to_dict()
        for seat in SEATS:
            unknown_count = raw[seat].get("unknown_count")
            if type(unknown_count) is not int or unknown_count != canonical["hands"][seat]["unknown_count"]:
                raise ValueError("INVALID_UNKNOWN_COUNT")
            provenance = deal["card_provenance"][seat]
            if provenance.get("derived_cards") or provenance.get("observed_cards") != canonical["hands"][seat]["cards"]:
                raise ValueError("INVALID_OBSERVED_PROVENANCE")
        return {s: canonical["hands"][s]["cards"] for s in SEATS}, []
    except (ValueError, TypeError, KeyError, AttributeError):
        # The original JSON remains authoritative for diagnosis of rejected data.
        return empty, ["INVALID_OR_DERIVED_DEAL_REVIEW_REQUIRED"]


def _hand(cards: list[str]) -> str:
    return ".".join("".join(r for r in RANK_ORDER if r + suit in cards) for suit in SUIT_ORDER)


def _comment(label: str, value: Any) -> list[str]:
    # Semicolon comments cannot escape into tags. ASCII escaping also keeps the
    # file within PBN's ISO-8859-1 character repertoire; title filenames are UTF-8.
    encoded = _json(value)
    return ["; " + label + ("=" if i == 0 else "+=") + encoded[i:i + 160]
            for i in range(0, len(encoded), 160)]


def render_frame_pbn(record: Mapping[str, Any], *, provenance: Mapping[str, Any] | None = None) -> tuple[bytes, dict[str, Any]]:
    """Render one original-frame observation; do not fuse frames or fill a deck."""
    hands, reasons = _observed_hands(record)
    return _render_hands(record, hands, reasons, provenance)


def _render_hands(record, hands, reasons, provenance, *, ownership=None):
    """Shared formatting after the caller's evidence-specific admission checks."""
    count = sum(map(len, hands.values()))
    representation = ("REVIEW_INVALID_OBSERVATION" if reasons and record.get("deal") is not None
                      else "COMPLETE_MACHINE_OBSERVATION" if count == 52
                      else "PARTIAL_MACHINE_OBSERVATION")
    dealer = record.get("dealer")
    dealer = dealer if isinstance(dealer, str) and dealer in SEATS else "?"
    first = dealer if dealer != "?" else "N"
    order = SEATS[SEATS.index(first):] + SEATS[:SEATS.index(first)]
    # A 1..12-card original hand must never be serialized as an end position.
    deal_value = (first + ":" + " ".join(_hand(hands[s]) if len(hands[s]) == 13 else "-" for s in order)
                  if any(len(cards) == 13 for cards in hands.values()) else "?")
    values = dict.fromkeys(_TAG_NAMES, "?")
    values.update(Dealer=dealer, Deal=deal_value)
    board = record.get("board_number")
    if isinstance(board, int) and not isinstance(board, bool) and 0 < board < 10**64:
        values["Board"] = str(board)
    elif isinstance(board, int) and not isinstance(board, bool) and board >= 10**64:
        reasons.append("BOARD_NUMBER_EXCEEDS_TAG_LIMIT")
    vulnerable = record.get("vulnerability")
    if isinstance(vulnerable, str) and vulnerable in ("None", "NS", "EW", "All"):
        values["Vulnerable"] = vulnerable
    info = {"writer_version": PBN_WRITER_VERSION, "representation": representation,
            "pbn_format": "EXPORT" if dealer != "?" else "IMPORT",
            "observed_card_count": count, "review_reasons": reasons,
            "canonical_promotion_allowed": False}
    if ownership is not None:
        info.update(ownership)
    lines = ["% PBN 2.1"]
    if dealer != "?":
        lines.append("% EXPORT")
    if ownership is None:
        lines += ["; MACHINE_CANDIDATE_NOT_CONFIRMED", "; PARTIAL_HANDS_IN_COMMENTS; no deck completion or frame fusion"]
    else:
        lines += ["; CONDITIONAL_ON_REVIEWED_PREMISES; canonical_promotion_allowed=false",
                  "; SERIALIZER_DOES_NOT_SOLVE_OR_JOIN_FRAMES; individual partial hands in comments"]
    if dealer == "?":
        lines.append("; IMPORT format: N is an ordering anchor; Dealer remains unknown")
    lines += _comment("export", info)
    lines += _comment("provenance", dict(provenance or {}))
    lines += _comment("qualified_ownership" if ownership is not None else "observed_subsets", hands)
    lines += _comment("observation_status", record.get("status"))
    lines += [f'[{name} "{values[name]}"]' for name in _TAG_NAMES]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"), info


def _write_exclusive(path: Path, data: bytes) -> str:
    """Never truncate a preexisting entry, including an identical symbolic link."""
    try:
        with path.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise FileExistsError("PBN output collision; existing entry preserved: " + str(path)) from None
        return "ALREADY_PRESENT_IDENTICAL"
    return "CREATED"


def export_frame_pbns(output_dir: Path, records: Iterable[Mapping[str, Any]], *,
                      source_metadata: Mapping[str, Any] | None = None,
                      diagnostics_file: str = "bridge_positions.jsonl",
                      provenance_by_record_sha256: Mapping[str, Mapping[str, Any]] | None = None,
                      qualified_by_record_sha256: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Persist independent sidecars in output_dir/pbn; metadata grants no paths.

    Input is the v4 canonical observation inside a Bridge Vision result. Raw
    candidates/fused or inferred deals are deliberately not direct substitutes. Source
    fingerprints and titles are metadata claims, not newly verified source bytes.
    Optional temporal/solver provenance is bound to one original record's hash,
    retained as a namespaced comment claim, and cannot add cards to Deal. Its
    observed/inferred/uncertain labels never change the native card notation.
    A separate qualified_by_record_sha256 input accepts only pinned loader
    objects, with an independently bound producer result and reviewed premises.
    """
    root = Path(output_dir).resolve(strict=True)
    folder = root / "pbn"
    if folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
        raise ValueError("PBN directory must not be a symlink")
    folder.mkdir(exist_ok=True)
    if folder.resolve(strict=True) != folder or not folder.is_dir():
        raise ValueError("PBN directory must be directly inside output directory")
    metadata = dict(source_metadata or {})
    rows = list(records)
    record_hashes = [observation_record_sha256(row) for row in rows]
    external = dict(provenance_by_record_sha256 or {})
    if set(external) - set(record_hashes) or any(not isinstance(v, Mapping) for v in external.values()):
        raise ValueError("PBN external provenance must bind to an existing observation SHA256")
    qualified = dict(qualified_by_record_sha256 or {})
    if qualified:
        from .recovery_pbn import QualifiedRecovery, render_qualified_recovery
        if set(qualified) - set(record_hashes) or any(type(v) is not QualifiedRecovery for v in qualified.values()):
            raise ValueError("PBN qualified ownership requires pinned loader objects")
        for record, key in zip(rows, record_hashes):
            if key in qualified and not qualified[key].matches(record):
                raise ValueError("PBN qualified ownership binding mismatch")
    observations_sha = hashlib.sha256(_json(rows).encode("ascii")).hexdigest()
    source_id = metadata.get("source_fingerprint") or metadata.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        source_id = "observations-" + observations_sha
    title = metadata.get("video_title") or metadata.get("title")
    stem = safe_video_stem(title, source_id=source_id)
    source_digest = hashlib.sha256(_json(source_id).encode("ascii")).hexdigest()[:12]
    outputs = []
    for index, record in enumerate(rows):
        provenance = {"source_id_claim": source_id, "source_original_bytes_verified": False,
                      "video_title_claim": title, "diagnostics_file": diagnostics_file,
                      "record_index": index, "record_sha256": record_hashes[index],
                      "frame_file": record.get("frame_file"), "frame_sha256_claim": record.get("frame_sha256"),
                      "time_seconds": record.get("time"), "board_id_claim": record.get("board_id"),
                      "engine_version": record.get("engine_version"),
                      "recognizer_versions": [x.get("detector") for x in record.get("candidates", []) if isinstance(x, Mapping)]}
        if record_hashes[index] in external:
            provenance["external_provenance_claim"] = dict(external[record_hashes[index]])
        if record_hashes[index] in qualified:
            data, info = render_qualified_recovery(qualified[record_hashes[index]], record, provenance)
        else:
            data, info = render_frame_pbn(record, provenance=provenance)
        digest = hashlib.sha256(data).hexdigest()
        time = record.get("time")
        # Keep filenames bounded even for untrusted but finite numeric metadata.
        time_label = str(round(time * 1000)) if isinstance(time, (int, float)) and not isinstance(time, bool) and 0 <= time <= 10**12 else "UNKNOWN"
        # Hash covers the exact PBN including source/frame/board/version/index.
        name = f"{stem}-src{source_digest}-f{index:06d}-t{time_label}-{digest[:24]}.pbn"
        target = folder / name
        status = _write_exclusive(target, data)
        outputs.append({**info, "path": str(Path("pbn") / name), "sha256": digest,
                        "bytes": len(data), "record_index": index, "write_status": status})
    return outputs
