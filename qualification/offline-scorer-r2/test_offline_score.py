"""Synthetic-only controls. Authored; execution remains NOT_RUN."""
import copy
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

SOURCE = Path(__file__).with_name("offline_score.py")
spec = importlib.util.spec_from_file_location("private_offline_score", SOURCE)
score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score)

def png_bytes():
    # Tiny artificial PNG; no real lesson image or video processing.
    return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5ioAAAAASUVORK5CYII=")

def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def fixture():
    # Invented identifiers are deliberately synthetic, never real lesson evidence.
    source, clip, frame = "1" * 64, "2" * 64, "3" * 64
    case = {"occurrence_id": "synthetic-board-a", "start_pts": 10000, "end_pts": 11000,
            "cards_scorable": True, "complete_visible_deal": False,
            "hands": {"N": ["AS", "KH"], "E": [], "S": [], "W": []},
            "source_session": "synthetic-session", "resolution_group": "synthetic-resolution",
            "deal_id": "synthetic-deal-a",
            "auction": {"scorable": True, "complete": True, "dealer": "N",
                        "association_verified": True,
                        "calls": [{"seat": seat, "call": "PASS"} for seat in score.SEATS]}}
    gold = {"schema": score.TAXONOMY, "dataset_kind": "SYNTHETIC_CONTROL",
            "source_sha256": source, "clip_sha256": clip,
            "time_base": [1, 1000], "occurrences": [case]}
    index = {"schema": "bridge-source-pts-index/v1", "independently_verified": True,
             "source_sha256": source, "clip_sha256": clip, "time_base": [1, 1000],
             "frames": [{"frame_sha256": frame, "requested_timestamp_ms": 0,
                         "source_pts": 10000, "source_frame_index": 250}]}
    raw = {"version": "synthetic-control", "status": "PRIMARY_COMPLETE",
           "deals": [{"timestamp_ms": 0, "screenshot_sha256": frame,
                      "hands": copy.deepcopy(case["hands"])}],
           "auction_recognition": {"status": "OBSERVED", "source_id": clip,
               "auctions": [{"board_occurrence_id": "synthetic-candidate-a", "complete": True,
                            "dealer": "N", "contract": None, "declarer": None,
                            "ordered_calls": copy.deepcopy(case["auction"]["calls"]),
                            "observations": [{"timestamp_ms": 0, "frame_sha256": frame}]}]}}
    freeze = {"schema": "bridge-independent-gold-freeze/v1",
              "gold_sha256": digest_json(gold), "pts_index_sha256": digest_json(index),
              "frozen_before_outputs": True, "outputs_seen_before_freeze": False,
              "independent_review": True, "reviewer": "synthetic-reviewer",
              "frozen_utc": "2026-01-01T00:00:00Z"}
    auction = raw["auction_recognition"]["auctions"][0]
    auction.update(start_ms=0, end_ms=0, latest_observation_index=0, latest_observation_timestamp_ms=0)
    auction["observations"][0]["frame_path"] = "/synthetic/auction.png"
    attach_evidence(auction)
    return raw, gold, index, freeze

def attach_evidence(auction):
    for call in auction["ordered_calls"]:
        call["evidence"] = [copy.deepcopy(auction["observations"][0])]

def run(raw, gold, index, freeze, *, boundary_events=None):
    return score.evaluate(raw, gold, index, freeze,
                          gold_hash=digest_json(gold), index_hash=digest_json(index),
                          boundary_events=boundary_events)

def refresh_freeze(gold, index, freeze):
    freeze["gold_sha256"], freeze["pts_index_sha256"] = digest_json(gold), digest_json(index)

def test_exact_smoke_scores_but_never_promotes():
    report = run(*fixture())
    assert report["cards"] == {
        "tp": 2, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0,
        "seat_errors": 0, "false_complete_deals": 0,
        "duplicate_deal_outputs": 0, "abstain_occurrences": 0}
    assert report["auction"]["complete_sequence_correct"] == 1
    assert report["auction"]["pass_x_xx"]["PASS"]["tp"] == 4
    assert report["promotion_allowed"] is False
    assert "MISSING_OR_UNFROZEN_INDEPENDENT_HOLDOUT" in report["promotion_blockers"]
    # PTS alignment must never turn into factual auction/deal association.
    assert report["board_association"]["factual_deal_links"]["fn"] == 1
    assert report["board_association"]["verified_factual_link_predictions"] == 0

@pytest.mark.parametrize("field,value", [
    ("frozen_before_outputs", False), ("outputs_seen_before_freeze", True),
    ("independent_review", False), ("gold_sha256", "0" * 64),
    ("pts_index_sha256", "0" * 64), ("reviewer", "")])
def test_invalid_freeze_has_no_accuracy_metrics(field, value):
    raw, gold, index, freeze = fixture()
    freeze[field] = value
    report = run(raw, gold, index, freeze)
    assert report["status"] == "BLOCKED"
    assert report["accuracy_evaluated"] is False
    assert "cards" not in report and report["promotion_allowed"] is False

def test_abstain_counts_false_negatives_and_not_covered_classes_are_null():
    raw, gold, index, freeze = fixture()
    raw["deals"] = []
    raw["auction_recognition"] = {"status": "UNAVAILABLE", "auctions": []}
    report = run(raw, gold, index, freeze)
    assert report["cards"]["fn"] == 2 and report["cards"]["recall"] == 0
    assert report["cards"]["abstain_occurrences"] == 1
    assert report["auction"]["fn"] == 4
    assert report["auction"]["pass_x_xx"]["X"]["precision"] is None
    assert report["auction"]["pass_x_xx"]["XX"]["recall"] is None

def test_wrong_card_seat_is_fp_and_fn():
    raw, gold, index, freeze = fixture()
    raw["deals"][0]["hands"] = {"N": ["KH"], "E": ["AS"]}
    report = run(raw, gold, index, freeze)
    assert report["cards"]["tp"] == 1
    assert report["cards"]["fp"] == report["cards"]["fn"] == report["cards"]["seat_errors"] == 1

def test_repeated_output_cannot_inflate_true_positives():
    raw, gold, index, freeze = fixture()
    raw["deals"].append(copy.deepcopy(raw["deals"][0]))
    raw["auction_recognition"]["auctions"].append(copy.deepcopy(raw["auction_recognition"]["auctions"][0]))
    report = run(raw, gold, index, freeze)
    assert report["cards"]["tp"] == 2 and report["cards"]["fp"] == 2
    assert report["cards"]["duplicate_deal_outputs"] == 1
    assert report["auction"]["tp"] == 4 and report["auction"]["fp"] == 4
    assert report["auction"]["complete_sequence_correct"] == 1

def test_pass_double_redouble_are_exact_position_and_seat_metrics():
    raw, gold, index, freeze = fixture()
    tokens = ["1S", "X", "XX", "PASS", "PASS", "PASS"]
    sequence = [{"seat": score.SEATS[i % 4], "call": c} for i, c in enumerate(tokens)]
    gold["occurrences"][0]["auction"]["calls"] = sequence
    auction = raw["auction_recognition"]["auctions"][0]
    auction.update(ordered_calls=copy.deepcopy(sequence), contract="1SXX", declarer="N")
    attach_evidence(auction)
    refresh_freeze(gold, index, freeze)
    good = run(raw, gold, index, freeze)
    assert good["auction"]["pass_x_xx"]["X"]["tp"] == 1
    assert good["auction"]["pass_x_xx"]["XX"]["tp"] == 1
    assert good["auction"]["contract_correct"] == good["auction"]["declarer_correct"] == 1
    auction["ordered_calls"][2]["call"] = "X"
    bad = run(raw, gold, index, freeze)
    assert bad["auction"]["pass_x_xx"]["XX"]["fn"] == 1
    assert bad["auction"]["pass_x_xx"]["X"]["fp"] == 1
    assert bad["auction"]["false_complete_auctions"] == 1
    assert bad["auction"]["complete_sequence_correct"] == 0

def test_wrong_auction_seat_counts_fp_fn_and_seat_error():
    raw, gold, index, freeze = fixture()
    raw["auction_recognition"]["auctions"][0]["ordered_calls"][0]["seat"] = "E"
    report = run(raw, gold, index, freeze)
    assert report["auction"]["seat_errors"] == 1
    assert report["auction"]["fp"] == report["auction"]["fn"] == 1

def test_partial_auction_cannot_become_complete():
    raw, gold, index, freeze = fixture()
    prefix = [{"seat": "N", "call": "1S"}, {"seat": "E", "call": "PASS"}]
    gold["occurrences"][0]["auction"].update(calls=prefix, complete=False)
    raw["auction_recognition"]["auctions"][0].update(ordered_calls=copy.deepcopy(prefix))
    attach_evidence(raw["auction_recognition"]["auctions"][0])
    refresh_freeze(gold, index, freeze)
    report = run(raw, gold, index, freeze)
    assert report["auction"]["false_complete_auctions"] == 1
    assert report["auction"]["complete_sequence_targets"] == 0
    assert report["auction"]["complete_sequence_accuracy"] is None

def test_full_card_claim_on_partial_gold_is_false_complete():
    raw, gold, index, freeze = fixture()
    deck = [r + s for s in "SHDC" for r in "AKQJT98765432"]
    raw["deals"][0]["hands"] = {seat: deck[i * 13:(i + 1) * 13]
                                for i, seat in enumerate(score.SEATS)}
    assert run(raw, gold, index, freeze)["cards"]["false_complete_deals"] == 1

def test_unknown_source_pts_is_unresolved_never_guessed_from_timestamp():
    raw, gold, index, freeze = fixture()
    raw["deals"][0]["screenshot_sha256"] = "9" * 64
    report = run(raw, gold, index, freeze)
    assert report["cards"]["fn"] == 2
    assert report["status"] == "SCORED_WITH_UNRESOLVED"
    assert report["unresolved"][0]["reason"] == "NO_VERIFIED_SOURCE_PTS"

def test_half_open_interval_and_identical_pixels_are_not_deduplicated():
    raw, gold, index, freeze = fixture()
    case = copy.deepcopy(gold["occurrences"][0])
    case.update(occurrence_id="synthetic-board-b", start_pts=11000, end_pts=12000)
    gold["occurrences"].append(case)
    row = copy.deepcopy(index["frames"][0])
    row.update(requested_timestamp_ms=1000, source_pts=11000, source_frame_index=275)
    index["frames"].append(row)  # Same PNG hash, different real source frame/time.
    adapter = score.Alignment(index, gold)
    assert adapter.locate(row["frame_sha256"], 1000) == ("synthetic-board-b", None)
    assert adapter.locate(row["frame_sha256"], True)[1] == "INVALID_FRAME_HASH_OR_REQUESTED_TIMESTAMP"

def test_cross_board_auction_is_unresolved_and_not_completed():
    raw, gold, index, freeze = fixture()
    case = copy.deepcopy(gold["occurrences"][0])
    case.update(occurrence_id="synthetic-board-b", start_pts=11000, end_pts=12000)
    gold["occurrences"].append(case)
    row = copy.deepcopy(index["frames"][0])
    row.update(requested_timestamp_ms=1000, source_pts=11000, source_frame_index=275)
    index["frames"].append(row)
    raw["auction_recognition"]["auctions"][0]["observations"].append(
        {"frame_sha256": row["frame_sha256"], "timestamp_ms": 1000,
         "frame_path": "/synthetic/second.png"})
    refresh_freeze(gold, index, freeze)
    report = run(raw, gold, index, freeze)
    assert report["board_association"]["cross_board_auction_merges"] == 1
    assert report["auction"]["complete_sequence_correct"] == 0
    assert report["auction"]["fn"] == 8

def test_ambiguous_index_and_overlapping_gold_are_rejected():
    raw, gold, index, freeze = fixture()
    index["frames"].append(copy.deepcopy(index["frames"][0]))
    with pytest.raises(score.ScoringError, match="ambiguous"):
        score.Alignment(index, gold)
    index["frames"].pop()
    case = copy.deepcopy(gold["occurrences"][0])
    case["occurrence_id"] = "synthetic-overlap"
    gold["occurrences"].append(case)
    with pytest.raises(score.ScoringError, match="overlapping"):
        score.Alignment(index, gold)

def test_unscorable_gold_is_explicit_not_silently_dropped():
    raw, gold, index, freeze = fixture()
    gold["occurrences"][0]["cards_scorable"] = False
    refresh_freeze(gold, index, freeze)
    report = run(raw, gold, index, freeze)
    assert report["cards"]["precision"] is None
    assert any(row["reason"] == "UNSCORABLE_GOLD" for row in report["unresolved"])

def test_evidence_tampering_and_path_escape_rejected(tmp_path):
    path = tmp_path / "synthetic.bin"
    path.write_bytes(png_bytes())
    digest = score.sha(path)
    score.contained_file(tmp_path, str(path), digest)
    path.write_bytes(b"changed")
    with pytest.raises(score.ScoringError, match="changed"):
        score.contained_file(tmp_path, str(path), digest)
    outside = tmp_path.parent / (tmp_path.name + "-outside.bin")
    outside.write_bytes(b"synthetic")
    with pytest.raises(score.ScoringError, match="escapes"):
        score.contained_file(tmp_path, str(outside), score.sha(outside))

def test_boolean_call_index_is_rejected():
    with pytest.raises(score.ScoringError, match="integers"):
        score.calls([{"seat": "N", "call": "PASS", "index": False}])

def test_coverage_loss_is_unresolved_even_when_emitted_labels_match():
    raw, gold, index, freeze = fixture()
    raw["auction_recognition"]["status"] = "TRUNCATED"
    report = run(raw, gold, index, freeze)
    assert report["status"] == "SCORED_WITH_UNRESOLVED"
    assert report["real_video_accuracy_evaluated"] is False
    assert any(x["reason"] == "RAW_AUCTION_COVERAGE_INCOMPLETE" for x in report["unresolved"])

def test_reused_candidate_board_identity_is_an_association_error():
    raw, gold, index, freeze = fixture()
    case = copy.deepcopy(gold["occurrences"][0])
    case.update(occurrence_id="synthetic-board-b", start_pts=11000, end_pts=12000)
    gold["occurrences"].append(case)
    row = copy.deepcopy(index["frames"][0])
    row.update(requested_timestamp_ms=1000, source_pts=11000, source_frame_index=275)
    index["frames"].append(row)
    second = copy.deepcopy(raw["auction_recognition"]["auctions"][0])
    second["observations"] = [{"timestamp_ms": 1000, "frame_sha256": row["frame_sha256"],
                               "frame_path": "/synthetic/second.png"}]
    second.update(start_ms=1000, end_ms=1000, latest_observation_timestamp_ms=1000)
    attach_evidence(second)
    raw["auction_recognition"]["auctions"].append(second)
    refresh_freeze(gold, index, freeze)
    report = run(raw, gold, index, freeze)
    assert report["board_association"]["unresolved_identity_groups"] == 1
    assert report["board_association"]["cross_board_auction_merges"] == 1

def test_unreadable_call_is_explicit_abstain_not_pass():
    raw, gold, index, freeze = fixture()
    raw["auction_recognition"]["auctions"][0]["ordered_calls"][0] = {"status": "UNREADABLE",
        "evidence": copy.deepcopy(raw["auction_recognition"]["auctions"][0]["ordered_calls"][0]["evidence"])}
    report = run(raw, gold, index, freeze)
    assert report["auction"]["fn"] == 1 and report["auction"]["fp"] == 0
    assert report["auction"]["unreadable_or_abstain_calls"] == 1
    assert report["auction"]["pass_x_xx"]["PASS"]["fn"] == 1
    assert report["auction"]["false_complete_auctions"] == 1

def write_comparison(root, raw, gold, index, freeze):
    def write(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")
    summary = {"schema": "recognizer-comparison-v1", "runner_version": "recognizer-comparison-v1-auction-scope-r3",
               "status": "CAPTURED_UNSCORED", "gold_sha256": digest_json(gold),
               "manifest_sha256": "7" * 64, "accuracy_evaluated": False, "promotion_allowed": False,
               "scope": "PRIMARY_VISUAL_WITH_OPTIONAL_EMBEDDED_PROFILE_AUCTION; NO_ASR_DDS_OR_PUBLISHER",
               "runs": {v: {"status": "RETURNED", "exit_code": 0, "source_sha": str(i + 5) * 40}
                        for i, v in enumerate(("baseline", "candidate"))}}
    seal = dict(summary)
    seal.update(inputs={"video": {"sha256": gold["clip_sha256"]}},
                runtimes={v: {"sha": str(i + 5) * 40}
                          for i, v in enumerate(("baseline", "candidate"))},
                sealed_at_unix=1767312000)
    write(root / "comparison.json", summary)
    write(root / "seal.json", seal)
    for variant in ("baseline", "candidate"):
        directory = root / variant / ("synthetic-" + variant)
        directory.mkdir(parents=True)
        picture = directory / "synthetic.png"
        picture.write_bytes(png_bytes())
        local = copy.deepcopy(raw)
        local["deals"][0].update(screenshot=str(picture), screenshot_sha256=score.sha(picture))
        obs = local["auction_recognition"]["auctions"][0]["observations"][0]
        obs.update(frame_path=str(picture), frame_sha256=score.sha(picture))
        attach_evidence(local["auction_recognition"]["auctions"][0])
        write(directory / "result.json", local)
        write(directory / "worker-status.json",
              {"status": "RETURNED", "source_sha": seal["runtimes"][variant]["sha"]})
        write(root / (variant + "-config.json"), {"output": str(directory)})
    return summary, seal


def capture_fixture(root, gold, index):
    """Synthetic-only independent attestation; never auto-freeze real outputs."""
    value = {"schema": "bridge-independent-capture-freeze/v1",
             "independent_review": True, "frozen_before_scoring": True,
             "outputs_finalized": True, "reviewer": "synthetic-capture-reviewer",
             "frozen_utc": "2026-01-04T00:00:00Z",
             "gold_sha256": digest_json(gold), "pts_index_sha256": digest_json(index),
             "clip_sha256": gold["clip_sha256"],
             "files": [{"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
                        "sha256": score.sha(p)}
                       for p in sorted(root.rglob("*")) if p.is_file()]}
    path = root.parent / (root.name + "-capture.json")
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, score.sha(path)

def load_capture(root, gold, index, freeze, capture):
    return score.load_comparison(root, digest_json(gold), gold["clip_sha256"], freeze,
                                 capture_path=capture[0], capture_hash=capture[1],
                                 index_hash=digest_json(index))

def test_published_shape_with_independent_capture_pin(tmp_path):
    raw, gold, index, freeze = fixture()
    index["frames"][0]["frame_sha256"] = hashlib.sha256(png_bytes()).hexdigest()
    refresh_freeze(gold, index, freeze)
    write_comparison(tmp_path, raw, gold, index, freeze)
    capture = capture_fixture(tmp_path, gold, index)
    runs = load_capture(tmp_path, gold, index, freeze, capture)
    report = run(runs["candidate"]["raw"], gold, index, freeze,
                 boundary_events=runs["candidate"]["boundary_events"])
    assert report["cards"]["tp"] == 2 and report["auction"]["complete_sequence_correct"] == 1
    assert report["metric_scope"] == "CONDITIONAL_ON_ALIGNED_OUTPUTS_AND_SCORABLE_GOLD"

@pytest.mark.parametrize("relative", ["comparison.json", "seal.json", "candidate-config.json",
    "candidate/synthetic-candidate/worker-status.json", "candidate/synthetic-candidate/result.json",
    "candidate/synthetic-candidate/synthetic.png"])
def test_capture_tamper_rejected_before_scoring(tmp_path, relative):
    raw, gold, index, freeze = fixture()
    write_comparison(tmp_path, raw, gold, index, freeze)
    capture = capture_fixture(tmp_path, gold, index)
    path = tmp_path / relative
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(score.ScoringError, match="frozen capture bytes changed"):
        load_capture(tmp_path, gold, index, freeze, capture)

def test_edited_predictions_with_unchanged_png_are_rejected(tmp_path):
    raw, gold, index, freeze = fixture()
    write_comparison(tmp_path, raw, gold, index, freeze)
    capture = capture_fixture(tmp_path, gold, index)
    png = tmp_path / "candidate/synthetic-candidate/synthetic.png"
    before = score.sha(png)
    result = tmp_path / "candidate/synthetic-candidate/result.json"
    value = json.loads(result.read_text())
    value["deals"][0]["hands"]["N"] = ["QS"]
    result.write_text(json.dumps(value))
    assert score.sha(png) == before
    with pytest.raises(score.ScoringError, match="frozen capture bytes changed"):
        load_capture(tmp_path, gold, index, freeze, capture)

@pytest.mark.parametrize("change", ["missing", "extra", "resigned"])
def test_complete_capture_inventory_and_external_pin_required(tmp_path, change):
    raw, gold, index, freeze = fixture()
    write_comparison(tmp_path, raw, gold, index, freeze)
    capture = capture_fixture(tmp_path, gold, index)
    if change == "missing":
        (tmp_path / "candidate-config.json").unlink()
    elif change == "extra":
        (tmp_path / "unsealed.json").write_text("{}")
    else:
        value = json.loads(capture[0].read_text())
        value["reviewer"] = "replacement"
        capture[0].write_text(json.dumps(value))
    with pytest.raises(score.ScoringError):
        load_capture(tmp_path, gold, index, freeze, capture)

def test_authenticated_json_snapshot_does_not_reread_changed_file(tmp_path):
    raw, gold, index, freeze = fixture()
    write_comparison(tmp_path, raw, gold, index, freeze)
    capture = capture_fixture(tmp_path, gold, index)
    snapshot = score.CaptureSnapshot(tmp_path, *capture, digest_json(gold),
                                     digest_json(index), gold["clip_sha256"])
    old = snapshot.json(tmp_path / "comparison.json")
    (tmp_path / "comparison.json").write_text("malformed")
    assert snapshot.json(tmp_path / "comparison.json") == old

@pytest.mark.parametrize("relative", ["comparison.json", "seal.json", "candidate-config.json",
    "candidate/synthetic-candidate/worker-status.json", "candidate/synthetic-candidate/result.json"])
def test_json_symlink_outside_capture_rejected(tmp_path, relative):
    raw, gold, index, freeze = fixture()
    write_comparison(tmp_path, raw, gold, index, freeze)
    capture = capture_fixture(tmp_path, gold, index)
    path = tmp_path / relative
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)  # CI must support symlinks; no silent skip.
    with pytest.raises(score.ScoringError, match="symlink"):
        load_capture(tmp_path, gold, index, freeze, capture)

@pytest.mark.parametrize("status", ["UNREADABLE", "ABSTAIN", "UNRESOLVED"])
@pytest.mark.parametrize("index", [True, False, "0", -1, 1])
def test_abstention_cannot_bypass_contiguous_integer_index(status, index):
    with pytest.raises(score.ScoringError, match="integers"):
        score.calls([{"status": status, "index": index}])

def test_duplicate_auction_counts_extra_seat_errors_and_abstentions():
    raw, gold, index, freeze = fixture()
    extra = copy.deepcopy(raw["auction_recognition"]["auctions"][0])
    extra["ordered_calls"][0]["seat"] = "E"
    extra["ordered_calls"][1]["status"] = "UNREADABLE"
    raw["auction_recognition"]["auctions"].append(extra)
    report = run(raw, gold, index, freeze)
    assert report["auction"]["tp"] == 4 and report["auction"]["fp"] == 3
    assert report["auction"]["seat_errors"] == 1
    assert report["auction"]["unreadable_or_abstain_calls"] == 1
    assert report["auction"]["duplicate_auction_outputs"] == 1

@pytest.mark.parametrize("proof", [False, True])
@pytest.mark.parametrize("cross_board", [False, True])
def test_repeat_boundary_requires_frozen_event_and_independent_pts(proof, cross_board):
    raw, gold, index, freeze = fixture()
    auction = raw["auction_recognition"]["auctions"][0]
    auction.update(end_ms=1000, latest_observation_timestamp_ms=1000)
    case = copy.deepcopy(gold["occurrences"][0])
    case.update(occurrence_id="synthetic-board-b", start_pts=11000, end_pts=12000)
    gold["occurrences"].append(case)
    index["frames"].append(dict(index["frames"][0], requested_timestamp_ms=1000,
                               source_pts=11000 if cross_board else 10500, source_frame_index=275))
    refresh_freeze(gold, index, freeze)
    events = [{"frame_sha256": "3"*64, "timestamp_ms": 1000}] if proof else None
    report = run(raw, gold, index, freeze, boundary_events=events)
    if not proof:
        assert any(x["reason"] == "MISSING_HASH_BOUND_CAPTURE_REPEAT_OR_BOUNDARY_EVENT"
                   for x in report["unresolved"])
    elif cross_board:
        assert report["board_association"]["cross_board_auction_merges"] == 1
    else:
        assert report["auction"]["complete_sequence_correct"] == 1

def test_repeat_event_without_independent_pts_cannot_be_interpolated():
    raw, gold, index, freeze = fixture()
    raw["auction_recognition"]["auctions"][0].update(end_ms=1000, latest_observation_timestamp_ms=1000)
    report = run(raw, gold, index, freeze,
                 boundary_events=[{"frame_sha256": "3"*64, "timestamp_ms": 1000}])
    assert any(x["reason"] == "NO_VERIFIED_SOURCE_PTS" for x in report["unresolved"])

@pytest.mark.parametrize("field,value", [
    ("frame_sha256", "9"*64), ("timestamp_ms", 1000), ("frame_path", "/synthetic/orphan.png")])
def test_orphan_call_evidence_is_unresolved(field, value):
    raw, gold, index, freeze = fixture()
    raw["auction_recognition"]["auctions"][0]["ordered_calls"][0]["evidence"][0][field] = value
    report = run(raw, gold, index, freeze)
    assert any(x["reason"] == "UNBOUND_AUCTION_CALL_EVIDENCE" for x in report["unresolved"])

def test_capture_error_and_late_gold_freeze_cannot_score(tmp_path):
    raw, gold, index, freeze = fixture()
    summary, seal = write_comparison(tmp_path, raw, gold, index, freeze)
    summary["status"] = "REPLAY_ERROR"
    (tmp_path / "comparison.json").write_text(json.dumps(summary))
    capture = capture_fixture(tmp_path, gold, index)
    with pytest.raises(score.ScoringError, match="incomplete"):
        load_capture(tmp_path, gold, index, freeze, capture)
    summary["status"] = "CAPTURED_UNSCORED"
    (tmp_path / "comparison.json").write_text(json.dumps(summary))
    capture = capture_fixture(tmp_path, gold, index)
    freeze["frozen_utc"] = "2026-01-03T00:00:00Z"
    with pytest.raises(score.ScoringError, match="precede"):
        load_capture(tmp_path, gold, index, freeze, capture)

def test_frozen_decode_event_supplies_repeat_boundary_but_not_pts(tmp_path):
    raw, gold, index, freeze = fixture()
    index["frames"][0]["frame_sha256"] = hashlib.sha256(png_bytes()).hexdigest()
    index["frames"].append(dict(index["frames"][0], requested_timestamp_ms=1000,
                               source_pts=10500, source_frame_index=275))
    refresh_freeze(gold, index, freeze)
    write_comparison(tmp_path, raw, gold, index, freeze)
    for variant in ("baseline", "candidate"):
        directory = tmp_path / variant / ("synthetic-" + variant)
        result = directory / "result.json"
        value = json.loads(result.read_text())
        value["auction_recognition"]["auctions"][0].update(
            end_ms=1000, latest_observation_timestamp_ms=1000)
        result.write_text(json.dumps(value))
        evidence = directory / "evidence"
        evidence.mkdir()
        (evidence / "repeat.png").write_bytes(png_bytes())
        event = {"event": "FRAME_DECODED", "requested_ms": 1000,
                 "decoder_reported_position_ms": 999999, "pts_verified": False,
                 "evidence": {"path": "repeat.png", "sha256": score.sha(evidence / "repeat.png")}}
        (evidence / "events.jsonl").write_text(json.dumps(event) + "\n")
    capture = capture_fixture(tmp_path, gold, index)
    runs = load_capture(tmp_path, gold, index, freeze, capture)
    report = run(runs["candidate"]["raw"], gold, index, freeze,
                 boundary_events=runs["candidate"]["boundary_events"])
    assert report["auction"]["complete_sequence_correct"] == 1
    index["frames"].pop()
    refresh_freeze(gold, index, freeze)
    report = run(runs["candidate"]["raw"], gold, index, freeze,
                 boundary_events=runs["candidate"]["boundary_events"])
    assert any(x["reason"] == "NO_VERIFIED_SOURCE_PTS" for x in report["unresolved"])

def test_per_call_reference_must_match_verified_stored_observation(tmp_path):
    raw, gold, index, freeze = fixture()
    write_comparison(tmp_path, raw, gold, index, freeze)
    path = tmp_path / "candidate/synthetic-candidate/result.json"
    value = json.loads(path.read_text())
    value["auction_recognition"]["auctions"][0]["ordered_calls"][0]["evidence"][0]["frame_path"] = "/orphan.png"
    path.write_text(json.dumps(value))
    # Deliberately authentic capture of semantically invalid synthetic output.
    capture = capture_fixture(tmp_path, gold, index)
    with pytest.raises(score.ScoringError, match="not bound"):
        load_capture(tmp_path, gold, index, freeze, capture)
