#!/usr/bin/env python3
"""Private, offline evaluation of published comparison outputs; never promotion."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

SCHEMA = "bridge-offline-score/v1"
SCORER_VERSION = "offline-score-capture-repairs-r2"
PINNED_API_SHA = "3335e0e87814b1f646ece5bc99ee669ec76c755f"
TAXONOMY = "bridge-manual-occurrence-gold/v1"
SEATS = ("N", "E", "S", "W")
SPECIAL = ("PASS", "X", "XX")

class ScoringError(ValueError):
    pass

def require(value, message):
    if not value:
        raise ScoringError(message)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def hex64(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

def integer(value):
    return type(value) is int

def ratio(tp, denominator):
    # No-support ratios stay null, never a vacuous 100% accuracy claim.
    return tp / denominator if denominator else None

def tally(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn)}

def APIs():
    from bridge_vision.gold import evaluate_card_detector
    from bridge_contracts.video_deal import canonicalize_video_deal
    from bridge_contracts.video_auction import normalize_call, validate_auction_prefix
    return evaluate_card_detector, canonicalize_video_deal, normalize_call, validate_auction_prefix

def hands(value):
    _, canonicalize, _, _ = APIs()
    normalized = canonicalize({"hands": value}, derive_fourth_hand=False).to_dict()
    return {seat: normalized["hands"][seat]["cards"] for seat in SEATS}

def pairs(value):
    return {(seat, card) for seat, cards in value.items() for card in cards}

def calls(value):
    _, _, normalize, _ = APIs()
    require(isinstance(value, list), "calls must be an array")
    output = []
    for i, item in enumerate(value):
        require(isinstance(item, dict), "call must be an object")
        require(integer(item.get("index", i)) and item.get("index", i) == i,
                "call indices must be contiguous integers")
        if item.get("status") in {"UNREADABLE", "ABSTAIN", "UNRESOLVED"}:
            output.append(None)
            continue
        require(item.get("seat") in SEATS, "call seat must be verified")
        output.append((item["seat"], normalize(item["call"])))
    return output

class Alignment:
    """Hash + requested scan time -> independently verified source frame PTS."""
    def __init__(self, index, gold, boundary_events=None):
        require(index.get("schema") == "bridge-source-pts-index/v1", "PTS index schema")
        require(index.get("independently_verified") is True, "independent PTS binding required")
        require(index.get("source_sha256") == gold["source_sha256"] and
                index.get("clip_sha256") == gold["clip_sha256"], "source/clip identity mismatch")
        time_base = index.get("time_base")
        require(isinstance(time_base, list) and len(time_base) == 2 and
                all(integer(x) and x > 0 for x in time_base), "rational source time_base")
        require(time_base == gold.get("time_base"), "gold/index time_base mismatch")
        self.boundary_events = set()
        for event in boundary_events or []:
            require(hex64(event.get("frame_sha256")) and integer(event.get("timestamp_ms")) and
                    event["timestamp_ms"] >= 0, "invalid captured boundary event")
            self.boundary_events.add((event["frame_sha256"], event["timestamp_ms"]))
        self.frames = {}
        for row in index.get("frames", []):
            require(hex64(row.get("frame_sha256")) and integer(row.get("requested_timestamp_ms")) and
                    row["requested_timestamp_ms"] >= 0 and integer(row.get("source_pts")) and
                    integer(row.get("source_frame_index")) and row["source_frame_index"] >= 0,
                    "invalid independently decoded frame binding")
            key = (row["frame_sha256"], row["requested_timestamp_ms"])
            require(key not in self.frames, "duplicate or ambiguous frame/time binding")
            self.frames[key] = row
        require(self.frames, "empty PTS index")
        self.occurrences = gold["occurrences"]
        self.by_id = {}
        previous_end = None
        for case in sorted(self.occurrences, key=lambda c: c["start_pts"]):
            oid = case.get("occurrence_id")
            require(isinstance(oid, str) and oid and oid not in self.by_id, "unique gold occurrence")
            require(integer(case.get("start_pts")) and integer(case.get("end_pts")) and
                    case["start_pts"] < case["end_pts"], "half-open gold interval")
            require(previous_end is None or case["start_pts"] >= previous_end,
                    "overlapping gold intervals are unresolved, not automatically matched")
            previous_end = case["end_pts"]
            self.by_id[oid] = case

    def locate(self, frame_sha256, timestamp_ms):
        if not hex64(frame_sha256) or not integer(timestamp_ms) or timestamp_ms < 0:
            return None, "INVALID_FRAME_HASH_OR_REQUESTED_TIMESTAMP"
        row = self.frames.get((frame_sha256, timestamp_ms))
        if row is None:
            return None, "NO_VERIFIED_SOURCE_PTS"
        found = [c["occurrence_id"] for c in self.occurrences
                 if c["start_pts"] <= row["source_pts"] < c["end_pts"]]
        return (found[0], None) if len(found) == 1 else (None, "OUTSIDE_FROZEN_GOLD_COVERAGE")

    def auction(self, value):
        observations = value.get("observations")
        if not isinstance(observations, list) or not observations:
            return None, "NO_AUCTION_OBSERVATION_EVIDENCE"
        ids, references = [], set()
        previous_time = -1
        for obs in observations:
            if not isinstance(obs, dict) or not isinstance(obs.get("frame_path"), str):
                return None, "MISSING_AUCTION_SNAPSHOT_LOCATOR"
            timestamp = obs.get("timestamp_ms")
            if not integer(timestamp) or timestamp <= previous_time:
                return None, "INVALID_AUCTION_SNAPSHOT_ORDER"
            previous_time = timestamp
            oid, reason = self.locate(obs.get("frame_sha256"), timestamp)
            if reason:
                return None, reason
            ids.append(oid)
            references.add((timestamp, obs["frame_sha256"], obs["frame_path"]))
        if len(set(ids)) != 1:
            return None, "AUCTION_SPANS_MULTIPLE_GOLD_OCCURRENCES"
        start, end = value.get("start_ms"), value.get("end_ms")
        latest = value.get("latest_observation_timestamp_ms")
        latest_index = value.get("latest_observation_index")
        if (not all(integer(t) and t >= 0 for t in (start, end, latest)) or
                not integer(latest_index) or not 0 <= latest_index < len(observations) or
                start != observations[0]["timestamp_ms"] or start > end or latest != end or
                observations[latest_index]["timestamp_ms"] > latest or
                any(obs["timestamp_ms"] > end for obs in observations)):
            return None, "INVALID_OR_MISSING_AUCTION_BOUNDARY_METADATA"
        # Producer may extend latest/end on repeated pixels without retaining a PNG.
        # Require a frozen decoded-frame event AND independent hash->source PTS;
        # never interpolate PTS from the observer's logical millisecond timestamps.
        for timestamp, sample in ((start, observations[0]), (end, observations[latest_index]),
                                  (latest, observations[latest_index])):
            key = (sample["frame_sha256"], timestamp)
            retained = any(obs["timestamp_ms"] == timestamp and obs["frame_sha256"] == key[0]
                           for obs in observations)
            if not retained and key not in self.boundary_events:
                return None, "MISSING_HASH_BOUND_CAPTURE_REPEAT_OR_BOUNDARY_EVENT"
            boundary_oid, reason = self.locate(*key)
            if reason:
                return None, reason
            if boundary_oid != ids[0]:
                return None, "AUCTION_SPANS_MULTIPLE_GOLD_OCCURRENCES"
        for call in value.get("ordered_calls", []):
            evidence = call.get("evidence") if isinstance(call, dict) else None
            if not isinstance(evidence, list) or not evidence:
                return None, "MISSING_AUCTION_CALL_EVIDENCE"
            for ref in evidence:
                if (not isinstance(ref, dict) or
                        not integer(ref.get("timestamp_ms")) or not hex64(ref.get("frame_sha256")) or
                        not isinstance(ref.get("frame_path"), str) or
                        (ref["timestamp_ms"], ref["frame_sha256"], ref["frame_path"]) not in references):
                    return None, "UNBOUND_AUCTION_CALL_EVIDENCE"
        return ids[0], None

def validate_gold(gold):
    require(gold.get("schema") == TAXONOMY, "gold taxonomy mismatch")
    require(gold.get("dataset_kind") in {"SYNTHETIC_CONTROL", "REAL_FROZEN_SMOKE", "REAL_FROZEN_HOLDOUT"},
            "explicit dataset kind required")
    require(hex64(gold.get("source_sha256")) and hex64(gold.get("clip_sha256")),
            "actual source and clip hashes required")
    require(isinstance(gold.get("occurrences"), list) and gold["occurrences"], "gold cannot be empty")
    _, _, _, validator = APIs()
    for case in gold["occurrences"]:
        require(type(case.get("cards_scorable")) is bool, "explicit cards_scorable required")
        require(type(case.get("complete_visible_deal")) is bool, "explicit deal completeness required")
        if case["cards_scorable"]:
            case_hands = hands(case["hands"])
            require(not case["complete_visible_deal"] or
                    all(len(case_hands[s]) == 13 for s in SEATS), "complete gold must be 52 unique cards")
        auction = case.get("auction")
        require(isinstance(auction, dict) and type(auction.get("scorable")) is bool,
                "explicit auction scorable/abstain gold required")
        if auction["scorable"]:
            sequence = calls(auction["calls"])
            require(all(c is not None for c in sequence), "gold call uncertainty must be unscorable")
            require(type(auction.get("complete")) is bool, "explicit auction completeness required")
            require(auction.get("dealer") in SEATS, "verified gold dealer required")
            if sequence:
                law = validator([c[1] for c in sequence], dealer=auction["dealer"])
                require([c[0] for c in sequence] == [x["seat"] for x in law["history"]],
                        "gold seats disagree with verified dealer/order")
                require(law["terminated"] == auction["complete"], "gold termination mismatch")
            else:
                require(not auction["complete"], "empty gold auction cannot be complete")
        require(case.get("deal_id") is None or isinstance(case["deal_id"], str), "gold deal_id type")

def frozen(gold_hash, index_hash, receipt):
    return (isinstance(receipt, dict) and receipt.get("schema") == "bridge-independent-gold-freeze/v1" and
            receipt.get("gold_sha256") == gold_hash and receipt.get("pts_index_sha256") == index_hash and
            receipt.get("frozen_before_outputs") is True and
            receipt.get("outputs_seen_before_freeze") is False and
            receipt.get("independent_review") is True and
            bool(receipt.get("reviewer")) and bool(receipt.get("frozen_utc")))

def blocked(reason):
    return {"schema": SCHEMA, "status": "BLOCKED", "reasons": [reason],
            "accuracy_evaluated": False, "real_video_accuracy_evaluated": False, "promotion_allowed": False}

def score_raw(raw, gold, index, *, boundary_events=None):
    validate_gold(gold)
    alignment = Alignment(index, gold, boundary_events)
    require(isinstance(raw.get("deals", []), list), "raw deals must be an array")
    grouped = {oid: [] for oid in alignment.by_id}
    unresolved = []
    if raw.get("status") == "PRIMARY_PARTIAL_COVERAGE":
        unresolved.append({"kind": "coverage", "reason": "RAW_CARD_PARTIAL_COVERAGE"})
    for number, deal in enumerate(raw.get("deals", [])):
        normalized = hands(deal["hands"])
        oid, reason = alignment.locate(deal.get("screenshot_sha256"), deal.get("timestamp_ms"))
        if reason:
            unresolved.append({"kind": "deal", "index": number, "reason": reason,
                               "emitted_cards": len(pairs(normalized))})
        else:
            grouped[oid].append((number, normalized))
    card_tp = card_fp = card_fn = seat_errors = false_complete = duplicates = abstentions = 0
    rows = []
    evaluate, _, _, _ = APIs()
    for oid, case in alignment.by_id.items():
        predictions = grouped[oid]
        if not case["cards_scorable"]:
            unresolved.append({"kind": "gold_cards", "occurrence_id": oid, "reason": "UNSCORABLE_GOLD"})
            continue
        expected = hands(case["hands"])
        predicted = predictions[0][1] if predictions else {s: [] for s in SEATS}
        # Reuse existing card API; one canonical observation per independent occurrence.
        metrics = evaluate(lambda _: {"hands": predicted},
                           [{"frame": oid + ".png", "hands": expected}])
        card_tp += metrics.true_positive_cards
        card_fp += metrics.predicted_cards - metrics.true_positive_cards
        card_fn += metrics.expected_cards - metrics.true_positive_cards
        seat_errors += metrics.seat_errors
        abstentions += not predictions
        by_card = {card: seat for seat, card in pairs(expected)}
        for _, extra in predictions[1:]:
            duplicates += 1
            card_fp += len(pairs(extra))
            seat_errors += sum(card in by_card and by_card[card] != seat for seat, card in pairs(extra))
        for _, emission in predictions:
            if all(len(emission[s]) == 13 for s in SEATS) and not case["complete_visible_deal"]:
                false_complete += 1
        rows.append({"occurrence_id": oid, "predicted_deal_indices": [n for n, _ in predictions],
                     "state": "OBSERVED" if predictions else "ABSTAIN",
                     "exact_hands": bool(predictions) and pairs(predicted) == pairs(expected)})
    auctions = raw.get("auction_recognition") or {}
    require(isinstance(auctions, dict) and isinstance(auctions.get("auctions", []), list), "auction result shape")
    observed = {oid: [] for oid in alignment.by_id}
    board_merge_errors = 0
    auction_ids = {}
    if auctions.get("status") in {"TRUNCATED", "PARTIAL_COVERAGE"}:
        unresolved.append({"kind": "coverage", "reason": "RAW_AUCTION_COVERAGE_INCOMPLETE"})
    for number, auction in enumerate(auctions.get("auctions", [])):
        require(type(auction.get("complete")) is bool, "raw auction complete must be boolean")
        oid, reason = alignment.auction(auction)
        if reason:
            board_merge_errors += reason == "AUCTION_SPANS_MULTIPLE_GOLD_OCCURRENCES"
            unresolved.append({"kind": "auction", "index": number, "reason": reason})
        else:
            candidate_id = auction.get("board_occurrence_id")
            require(isinstance(candidate_id, str) and candidate_id, "candidate auction identity required")
            auction_ids.setdefault(candidate_id, set()).add(oid)
            observed[oid].append((number, auction))
    for candidate_id, gold_ids in auction_ids.items():
        if len(gold_ids) > 1:
            board_merge_errors += 1
            unresolved.append({"kind": "auction_identity", "candidate_id": candidate_id,
                               "reason": "CANDIDATE_AUCTION_ID_REUSED_ACROSS_GOLD_OCCURRENCES",
                               "gold_occurrences": sorted(gold_ids)})
    token_tp = token_fp = token_fn = call_seat_errors = false_auction_complete = 0
    exact_full = full_targets = auction_abstentions = auction_duplicates = 0
    call_abstentions = 0
    contract_correct = declarer_correct = factual_link_targets = 0
    special = {token: Counter(tp=0, fp=0, fn=0) for token in SPECIAL}
    auction_rows = []
    for oid, case in alignment.by_id.items():
        truth = case["auction"]
        emissions = observed[oid]
        if not truth["scorable"]:
            unresolved.append({"kind": "gold_auction", "occurrence_id": oid, "reason": "UNSCORABLE_GOLD"})
            continue
        expected = calls(truth["calls"])
        first = emissions[0][1] if emissions else None
        predicted = calls(first.get("ordered_calls", [])) if first else []
        auction_abstentions += not emissions
        call_abstentions += sum(c is None for c in predicted)
        full_targets += truth["complete"]
        factual_link_targets += bool(case.get("deal_id")) and truth.get("association_verified") is True
        if truth["complete"]:
            law = APIs()[3]([c[1] for c in expected], dealer=truth["dealer"])
            contract_correct += bool(first) and first["complete"] and first.get("contract") == law["contract"]
            declarer_correct += bool(first) and first["complete"] and first.get("declarer") == law["declarer"]
        is_exact = bool(first) and first["complete"] and truth["complete"] and (
            predicted == expected and first.get("dealer") == truth["dealer"])
        exact_full += is_exact
        for position in range(max(len(predicted), len(expected))):
            p = predicted[position] if position < len(predicted) else None
            e = expected[position] if position < len(expected) else None
            same = p is not None and p == e
            token_tp += same
            token_fp += p is not None and not same
            token_fn += e is not None and not same
            call_seat_errors += p is not None and e is not None and p[0] != e[0]
            for token in SPECIAL:
                special[token]["tp"] += same and e[1] == token
                special[token]["fp"] += p is not None and p[1] == token and not same
                special[token]["fn"] += e is not None and e[1] == token and not same
        for _, emission in emissions:
            if emission["complete"] and (not truth["complete"] or
                    calls(emission.get("ordered_calls", [])) != expected or
                    emission.get("dealer") != truth["dealer"]):
                false_auction_complete += 1
        for _, extra in emissions[1:]:
            auction_duplicates += 1
            extra_calls = calls(extra.get("ordered_calls", []))
            token_fp += sum(c is not None for c in extra_calls)
            call_abstentions += sum(c is None for c in extra_calls)
            for position, c in enumerate(extra_calls):
                e = expected[position] if position < len(expected) else None
                call_seat_errors += c is not None and e is not None and c[0] != e[0]
                if c is not None and c[1] in SPECIAL:
                    special[c[1]]["fp"] += 1
        # Source-PTS alignment measures board occurrence accuracy only.
        # Never promote temporal coincidence into an observed auction->hand fact.
        auction_rows.append({"occurrence_id": oid, "predicted_auction_indices": [n for n, _ in emissions],
                             "state": "OBSERVED" if emissions else "ABSTAIN",
                             "exact_complete_sequence": is_exact,
                             "gold_deal_id": case.get("deal_id"),
                             "factual_deal_link": "UNVERIFIED",
                             "link_basis": "SOURCE_PTS_ALIGNMENT_ONLY"})
    card_metrics = tally(card_tp, card_fp, card_fn)
    card_metrics.update(seat_errors=seat_errors, false_complete_deals=false_complete,
                        duplicate_deal_outputs=duplicates, abstain_occurrences=abstentions)
    auction_metrics = tally(token_tp, token_fp, token_fn)
    auction_metrics.update(seat_errors=call_seat_errors, false_complete_auctions=false_auction_complete,
                           complete_sequence_correct=exact_full, complete_sequence_targets=full_targets,
                           complete_sequence_accuracy=ratio(exact_full, full_targets),
                           contract_correct=contract_correct, declarer_correct=declarer_correct,
                           contract_declarer_targets=full_targets,
                           abstain_occurrences=auction_abstentions, unreadable_or_abstain_calls=call_abstentions,
                           duplicate_auction_outputs=auction_duplicates,
                           pass_x_xx={c: dict(tally(**special[c]), support_state=(
                               "POSITIVE_SUPPORT" if special[c]["tp"] + special[c]["fn"] else
                               "NEGATIVE_ONLY" if special[c]["fp"] else "NOT_COVERED")) for c in SPECIAL})
    associations = {"aligned_deal_outputs": sum(len(x) for x in grouped.values()),
                    "aligned_auction_outputs": sum(len(x) for x in observed.values()),
                    "cross_board_auction_merges": board_merge_errors,
                    "unresolved_identity_groups": sum(x["kind"] == "auction_identity" for x in unresolved),
                    "unresolved_outputs": sum(x["kind"] in {"deal", "auction"} for x in unresolved),
                    "verified_factual_link_predictions": 0,
                    "factual_deal_links": tally(0, 0, factual_link_targets),
                    "factual_link_abstentions": factual_link_targets,
                    "factual_link_state": "ABSTAIN_NO_VERIFIED_LINK_IN_RAW_RUNNER_SCHEMA"}
    return {"schema": SCHEMA, "status": "SCORED_WITH_UNRESOLVED" if unresolved else "SCORED_REVIEW_ONLY",
            "accuracy_evaluated": True, "dataset_kind": gold["dataset_kind"],
            "real_video_accuracy_evaluated": gold["dataset_kind"] != "SYNTHETIC_CONTROL",
            "metric_scope": "CONDITIONAL_ON_ALIGNED_OUTPUTS_AND_SCORABLE_GOLD",
            "coverage": {"emitted_deals": len(raw.get("deals", [])),
                         "emitted_auctions": len(auctions.get("auctions", [])),
                         "scorable_card_occurrences": sum(c["cards_scorable"] for c in alignment.by_id.values()),
                         "scorable_auction_occurrences": sum(c["auction"]["scorable"] for c in alignment.by_id.values()),
                         "unresolved_entries": len(unresolved)},
            "cards": card_metrics, "auction": auction_metrics,
            "board_association": associations, "card_occurrences": rows, "auction_occurrences": auction_rows,
            "unresolved": unresolved, "promotion_allowed": False,
            "canonical_promotion_state": "BLOCKED_REVIEW_ONLY"}

def holdout_reasons(gold, report, receipt):
    # Existing r263 holdout support is necessary, never sufficient for candidate3 authority.
    holdout = receipt.get("holdout") or {}
    occurrences = gold["occurrences"]
    reasons = []
    if holdout.get("frozen") is not True or holdout.get("independent_split_verified") is not True:
        reasons.append("MISSING_OR_UNFROZEN_INDEPENDENT_HOLDOUT")
    # This adapter scores one original source/clip. Four string tags cannot fake four sessions.
    if len(occurrences) < 24:
        reasons.append("INSUFFICIENT_24_CASE_SUPPORT")
    reasons.append("SINGLE_SOURCE_REPORT_CANNOT_SATISFY_4_SESSION_HOLDOUT")
    if sum(x["complete_visible_deal"] for x in occurrences) < 12 or sum(
            not x["complete_visible_deal"] for x in occurrences) < 12:
        reasons.append("INSUFFICIENT_COMPLETE_AND_NONCOMPLETE_STRATA")
    if len({x.get("resolution_group") for x in occurrences if x.get("resolution_group")}) < 3:
        reasons.append("INSUFFICIENT_RESOLUTION_STRATA")
    if sum(x.get("complete_visible_deal") and x.get("void_hand_verified") is True for x in occurrences) < 4:
        reasons.append("INSUFFICIENT_VOID_SUPPORT")
    if report["unresolved"]:
        reasons.append("UNRESOLVED_EVIDENCE_OR_GOLD")
    c, a = report["cards"], report["auction"]
    if (c["precision"] is None or c["recall"] is None or c["precision"] < .995 or
            c["recall"] < .95 or c["seat_errors"] or c["false_complete_deals"]):
        reasons.append("CARD_SAFETY_OR_ACCURACY_GATE_FAILED")
    if a["seat_errors"] or a["false_complete_auctions"] or report["board_association"]["cross_board_auction_merges"]:
        reasons.append("AUCTION_SAFETY_OR_BOARD_ASSOCIATION_FAILED")
    reasons.append("MULTI_SOURCE_STRATIFIED_HOLDOUT_AND_INDEPENDENT_I2_RECOMPUTATION_REQUIRED")
    reasons.append("CANDIDATE3_AUCTION_PROMOTION_CRITERIA_AND_AUTHORITY_NOT_GRANTED")
    return reasons

def evaluate(raw, gold, index, receipt, *, gold_hash, index_hash, boundary_events=None):
    if not frozen(gold_hash, index_hash, receipt):
        return blocked("MISSING_OR_INVALID_PRE_OUTPUT_GOLD_FREEZE")
    report = score_raw(raw, gold, index, boundary_events=boundary_events)
    report["promotion_blockers"] = holdout_reasons(gold, report, receipt)
    report["input_binding"] = {"gold_sha256": gold_hash, "pts_index_sha256": index_hash,
                               "source_sha256": gold["source_sha256"], "clip_sha256": gold["clip_sha256"]}
    return report

def contained_file(root, value, digest):
    require(isinstance(value, str) and hex64(digest), "evidence path/hash required")
    p = Path(value)
    if not p.is_absolute():
        p = root / p
    resolved = p.resolve(strict=True)
    require(resolved.is_relative_to(root.resolve(strict=True)) and resolved.is_file(),
            "evidence path escapes preserved worker output")
    require(sha(resolved) == digest, "emitted evidence bytes changed")
    with resolved.open("rb") as stream:
        require(stream.read(8) == b"\x89PNG\r\n\x1a\n", "emitted evidence PNG signature")

class CaptureSnapshot:
    """Out-of-band manifest pin authenticates bytes before any capture JSON parsing."""
    def __init__(self, root, manifest_path, expected_hash, gold_hash, index_hash, clip_hash):
        from datetime import datetime, timezone
        self.root = Path(root).resolve(strict=True)
        require(self.root.is_dir(), "comparison must be a directory")
        manifest_path = Path(manifest_path).resolve(strict=True)
        require(not manifest_path.is_relative_to(self.root), "capture freeze must be outside comparison")
        manifest = sealed_json(manifest_path, expected_hash)
        require(manifest.get("schema") == "bridge-independent-capture-freeze/v1",
                "independent capture freeze required")
        require(all(manifest.get(k) is True for k in
                    ("independent_review", "frozen_before_scoring", "outputs_finalized")),
                "independent finalized capture freeze required")
        require(isinstance(manifest.get("reviewer"), str) and manifest["reviewer"].strip(),
                "capture reviewer required")
        require(manifest.get("gold_sha256") == gold_hash and
                manifest.get("pts_index_sha256") == index_hash and
                manifest.get("clip_sha256") == clip_hash, "capture input binding mismatch")
        self.frozen_time = datetime.fromisoformat(manifest["frozen_utc"].replace("Z", "+00:00"))
        require(self.frozen_time.utcoffset() is not None and
                self.frozen_time <= datetime.now(timezone.utc), "capture freeze time invalid")
        entries = manifest.get("files")
        require(isinstance(entries, list) and entries, "complete capture inventory required")
        declared = {}
        for entry in entries:
            value = entry.get("path")
            require(isinstance(value, str) and value and "\\" not in value and ":" not in value
                    and not value.startswith("/") and
                    all(part not in {"", ".", ".."} for part in value.split("/")),
                    "capture inventory path invalid")
            require(value not in declared and hex64(entry.get("sha256")) and
                    integer(entry.get("bytes")) and entry["bytes"] >= 0,
                    "capture inventory entry invalid")
            declared[value] = entry
        actual = set()
        for path in self.root.rglob("*"):
            require(not path.is_symlink(), "capture symlink forbidden")
            require(path.is_dir() or path.is_file(), "capture must contain regular files")
            if path.is_file():
                actual.add(path.relative_to(self.root).as_posix())
        require(actual == set(declared), "capture inventory missing or extra files")
        self.records = {}
        # JSON/JSONL are retained as the identical authenticated byte snapshots.
        # Binary evidence is streamed: only verified header/size/digest are retained.
        for value, entry in declared.items():
            path = self.path(value)
            h, size, prefix = hashlib.sha256(), 0, b""
            chunks = [] if path.suffix in {".json", ".jsonl"} else None
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    if not prefix:
                        prefix = block[:8]
                    h.update(block)
                    size += len(block)
                    if chunks is not None:
                        chunks.append(block)
            require(size == entry["bytes"] and h.hexdigest() == entry["sha256"],
                    "frozen capture bytes changed: " + value)
            self.records[value] = {"sha256": h.hexdigest(), "prefix": prefix,
                                   "data": b"".join(chunks) if chunks is not None else None}

    def path(self, value):
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        # Reject symlinks even when their target remains inside the capture.
        relative = path.relative_to(self.root) if path.is_relative_to(self.root) else None
        require(relative is not None, "capture path escapes comparison")
        cursor = self.root
        for part in relative.parts:
            cursor = cursor / part
            require(not cursor.is_symlink(), "capture symlink forbidden")
        resolved = path.resolve(strict=True)
        require(resolved.is_relative_to(self.root) and resolved.is_file(),
                "capture path escapes comparison or is not regular file")
        return resolved

    def record(self, value):
        key = self.path(value).relative_to(self.root).as_posix()
        require(key in self.records, "file absent from frozen inventory")
        return self.records[key]

    def json(self, value):
        data = self.record(value)["data"]
        require(data is not None, "authenticated JSON bytes required")
        return json.loads(data.decode("utf-8"))

    def png(self, directory, value, digest):
        require(isinstance(value, str) and hex64(digest), "evidence path/hash required")
        path = Path(value)
        if not path.is_absolute():
            path = directory / path
        resolved = self.path(path)
        require(resolved.is_relative_to(directory), "evidence path escapes preserved worker output")
        record = self.record(resolved)
        require(record["sha256"] == digest, "emitted evidence bytes changed")
        require(record["prefix"] == b"\x89PNG\r\n\x1a\n", "emitted evidence PNG signature")

def sealed_json(path, expected_hash):
    require(hex64(expected_hash), "out-of-band SHA256 required")
    require(Path(path).is_file() and not Path(path).is_symlink(), "regular sealed JSON required")
    data = Path(path).read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected_hash, "JSON seal mismatch")
    return json.loads(data.decode("utf-8"))

def load_comparison(root, gold_hash, clip_hash, receipt, *,
                    capture_path, capture_hash, index_hash):
    from datetime import datetime
    snapshot = CaptureSnapshot(root, capture_path, capture_hash, gold_hash, index_hash, clip_hash)
    root = snapshot.root
    summary, seal = snapshot.json(root / "comparison.json"), snapshot.json(root / "seal.json")
    require(summary.get("status") == "CAPTURED_UNSCORED", "incomplete replay: score blocked")
    freeze_time = datetime.fromisoformat(receipt["frozen_utc"].replace("Z", "+00:00"))
    require(freeze_time.utcoffset() is not None and
            type(seal.get("sealed_at_unix")) in {int, float} and
            freeze_time.timestamp() < seal["sealed_at_unix"], "freeze must precede runner seal")
    require(snapshot.frozen_time.timestamp() >= seal["sealed_at_unix"],
            "capture freeze must follow runner seal")
    require(summary.get("schema") == seal.get("schema") == "recognizer-comparison-v1", "runner schema")
    require(summary.get("runner_version") == seal.get("runner_version") ==
            "recognizer-comparison-v1-auction-scope-r3", "r3 runner required")
    require(summary.get("scope") == seal.get("scope") ==
            "PRIMARY_VISUAL_WITH_OPTIONAL_EMBEDDED_PROFILE_AUCTION; NO_ASR_DDS_OR_PUBLISHER",
            "runner scope mismatch")
    require(summary.get("gold_sha256") == seal.get("gold_sha256") == gold_hash, "runner gold seal mismatch")
    require(seal["inputs"]["video"]["sha256"] == clip_hash, "clip identity mismatch")
    require(summary.get("manifest_sha256") == seal.get("manifest_sha256"), "manifest seal mismatch")
    require(summary.get("accuracy_evaluated") is False and summary.get("promotion_allowed") is False,
            "capture cannot attest accuracy or promotion")
    output = {}
    for variant in ("baseline", "candidate"):
        config = snapshot.json(root / (variant + "-config.json"))
        directory = Path(config["output"]).resolve(strict=True)
        require(directory.is_relative_to(root), "worker outside preserved comparison")
        worker = snapshot.json(directory / "worker-status.json")
        run = summary["runs"][variant]
        require(run.get("status") == "RETURNED" and run.get("exit_code") == 0 and
                run.get("source_sha") == worker.get("source_sha"), "summary/worker mismatch")
        require(worker.get("status") == "RETURNED" and worker.get("source_sha") == seal["runtimes"][variant]["sha"],
                "worker/source receipt mismatch")
        raw = snapshot.json(directory / "result.json")
        for deal in raw.get("deals", []):
            snapshot.png(directory, deal.get("screenshot"), deal.get("screenshot_sha256"))
        auction = raw.get("auction_recognition") or {}
        if auction.get("auctions"):
            require(auction.get("source_id") == clip_hash, "auction clip source mismatch")
        for occurrence in auction.get("auctions", []):
            observations = occurrence.get("observations", [])
            locators = set()
            for observation in observations:
                snapshot.png(directory, observation.get("frame_path"), observation.get("frame_sha256"))
                require(integer(observation.get("timestamp_ms")) and observation["timestamp_ms"] >= 0,
                        "observation timestamp required")
                locators.add((observation["frame_sha256"], observation["timestamp_ms"],
                              observation["frame_path"]))
            for call in occurrence.get("ordered_calls", []):
                evidence = call.get("evidence")
                require(isinstance(evidence, list) and evidence, "per-call evidence required")
                for ref in evidence:
                    require(isinstance(ref, dict) and
                            (ref.get("frame_sha256"), ref.get("timestamp_ms"), ref.get("frame_path")) in locators,
                            "per-call evidence not bound to verified stored observation")
        events = []
        event_path = directory / "evidence" / "events.jsonl"
        if event_path.relative_to(root).as_posix() in snapshot.records:
            data = snapshot.record(event_path)["data"]
            for line in data.decode("utf-8").splitlines():
                event = json.loads(line)
                if event.get("event") != "FRAME_DECODED":
                    continue
                evidence = event.get("evidence") or {}
                ms = event.get("requested_ms")
                require(integer(ms) and ms >= 0, "capture decode event timestamp required")
                snapshot.png(event_path.parent, evidence.get("path"), evidence.get("sha256"))
                events.append({"timestamp_ms": ms, "frame_sha256": evidence["sha256"]})
        output[variant] = {"raw": raw, "result_sha256": snapshot.record(directory / "result.json")["sha256"],
                           "runtime_sha": worker["source_sha"], "boundary_events": events}
    return output

def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("comparison", "gold", "index", "freeze", "capture", "repo", "output"):
        p.add_argument("--" + name, required=True, type=Path)
    for name in ("gold", "index", "freeze", "capture"):
        p.add_argument("--" + name + "-sha256", required=True)
    p.add_argument("--repo-sha", required=True)
    a = p.parse_args()
    inputs = {name: sealed_json(getattr(a, name), getattr(a, name + "_sha256"))
              for name in ("gold", "index", "freeze")}
    comparison = a.comparison.resolve(strict=True)
    require(not a.output.resolve().is_relative_to(comparison), "score output must be outside frozen capture")
    import subprocess
    require(a.repo_sha == PINNED_API_SHA, "exact pinned scorer API source SHA required")
    repo = a.repo.resolve(strict=True)
    actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    clean = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain",
                                     "--untracked-files=normal"], text=True).strip()
    require(actual == a.repo_sha and not clean, "scorer API checkout must be clean and exact")
    sys.path.insert(0, str(repo))
    gold, index, receipt = inputs["gold"], inputs["index"], inputs["freeze"]
    if not frozen(a.gold_sha256, a.index_sha256, receipt):
        result = blocked("MISSING_OR_INVALID_PRE_OUTPUT_GOLD_FREEZE")
    else:
        runs = load_comparison(comparison, a.gold_sha256, gold["clip_sha256"], receipt,
                               capture_path=a.capture, capture_hash=a.capture_sha256,
                               index_hash=a.index_sha256)
        result = {"schema": SCHEMA, "scorer_version": SCORER_VERSION, "scorer_sha256": sha(__file__),
                  "freeze_sha256": a.freeze_sha256, "capture_sha256": a.capture_sha256,
                  "promotion_allowed": False, "variants": {}}
        for variant, run in runs.items():
            report = evaluate(run["raw"], gold, index, receipt,
                              gold_hash=a.gold_sha256, index_hash=a.index_sha256,
                              boundary_events=run["boundary_events"])
            report["input_binding"].update(result_sha256=run["result_sha256"], runtime_sha=run["runtime_sha"],
                                           capture_sha256=a.capture_sha256)
            result["variants"][variant] = report
    with a.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    return 2 if result.get("status") == "BLOCKED" else 0

if __name__ == "__main__":
    raise SystemExit(main())
