"""Synthetic glyphs test data flow and abstention, never real-video accuracy."""
import copy
import hashlib
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from bridge_vision.auction_observer import AuctionObserver, PROFILE_SCHEMA, speech_mentions, pixel_sha


CALLS = ["1S", "X", "XX", "PASS", "2H", "4H", "1C", "2C"]


def fixture():
    ref = np.full((360, 400, 3), 255, dtype=np.uint8)
    def label(text, box):
        x, y, w, h = box
        cv2.putText(ref, text, (x+3, y+20), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 1)
    templates = []
    for i, call in enumerate(CALLS):
        box = [i % 4 * 90, 240 + i // 4 * 35, 80, 28]
        label(call, box)
        templates.append({"call": call, "box": box})
    headers = []
    for i, seat in enumerate("NESW"):
        box = [i*90, 30, 80, 28]
        label(seat, box)
        headers.append({"seat": seat, "box": box})
    label("TOP", [0, 0, 80, 28])
    label("BOARD A", [100, 0, 180, 28])
    cells = [[col*90, 70+row*35, 80, 28] for row in range(4) for col in range(4)]
    config = {"schema": PROFILE_SCHEMA, "frame_size": [400, 360],
              "cells": cells, "headers": headers, "templates": templates,
              "start_marker": [0, 0, 80, 28], "board_anchor": [100, 0, 180, 28],
              "blank": [0, 320, 80, 28], "reference_pixel_sha256": pixel_sha(ref)}
    return ref, config


def frame(ref, config, calls, *, offset=0, counter=0, board=None, top=True):
    image = ref.copy()
    for i, call in enumerate(calls, offset):
        if call is None:
            continue
        x, y, w, h = config["cells"][i]
        image[y:y+h, x:x+w] = 255
        if call == "?":
            cv2.putText(image, "?", (x+3, y+20), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 1)
        else:
            template = next(t for t in config["templates"] if t["call"] == call)
            tx, ty, _, _ = template["box"]
            image[y:y+h, x:x+w] = ref[ty:ty+h, tx:tx+w]
    image[359, 399] = counter
    if board is not None:
        image[0:28, 100:280] = 255
        cv2.putText(image, board, (103, 20), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 1)
    if not top:
        image[0:28, 0:80] = 255
    return image


def observer(tmp_path):
    ref, config = fixture()
    return AuctionObserver(config, ref, source_id="synthetic-source", output_dir=tmp_path), ref, config


def test_pixels_to_ordered_calls_double_redouble_and_evidence(tmp_path):
    obs, ref, cfg = observer(tmp_path)
    calls = ["1S", "X", "XX", "PASS", "PASS", "PASS"]
    for i in range(2):
        obs.observe(frame(ref, cfg, calls, counter=i), i*1000)
    auction = obs.result()["auctions"][0]
    assert auction["complete"] and auction["contract"] == "1SXX" and auction["declarer"] == "N"
    assert [c["call"] for c in auction["ordered_calls"]] == calls
    assert [c["seat"] for c in auction["ordered_calls"]] == ["N", "E", "S", "W", "N", "E"]
    assert all(len(c["evidence"]) == 2 for c in auction["ordered_calls"])
    for item in auction["observations"]:
        from pathlib import Path
        payload = Path(item["frame_path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["frame_sha256"]
        assert np.array_equal(cv2.imdecode(np.frombuffer(payload, np.uint8), 1),
                              frame(ref, cfg, calls, counter=item["timestamp_ms"]//1000))


@pytest.mark.parametrize("calls,offset,expected", [
    (["PASS"]*4, 0, None),
    (["1S", "PASS", "PASS", "PASS"], 1, "E"),
])
def test_passout_and_dealer_from_visible_first_cell(tmp_path, calls, offset, expected):
    obs, ref, cfg = observer(tmp_path)
    for i in range(2):
        obs.observe(frame(ref, cfg, calls, offset=offset, counter=i), i*1000)
    result = obs.result()["auctions"][0]
    assert result["complete"]
    assert result["declarer"] == expected


@pytest.mark.parametrize("calls,top", [
    (["1S", "PASS"], True),
    (["1S", "?", "PASS", "PASS"], True),
    (["1S", None, "PASS", "PASS"], True),
    (["XX", "PASS", "PASS", "PASS"], False),
])
def test_partial_scrolling_gap_and_unreadable_never_fill_calls(tmp_path, calls, top):
    obs, ref, cfg = observer(tmp_path)
    for i in range(2):
        obs.observe(frame(ref, cfg, calls, counter=i, top=top), i*1000)
    result = obs.result()["auctions"][0]
    assert not result["complete"] and result["contract"] is None
    assert len(result["visible_fragments"]) == 2
    assert not any(c["call"] for c in result["observations"][0]["cells"] if c["status"] != "READ")


def test_repeated_pixels_never_confirm_complete_auction(tmp_path):
    obs, ref, cfg = observer(tmp_path)
    image = frame(ref, cfg, ["1S", "PASS", "PASS", "PASS"])
    obs.observe(image, 0)
    obs.observe(image.copy(), 1000)
    result = obs.result()["auctions"][0]
    assert result["status"] == "COMPLETE_NEEDS_CONFIRMATION"
    assert result["contract"] is None and len(result["observations"]) == 1


def test_unknown_or_changed_header_retains_calls_without_inferred_seats(tmp_path):
    obs, ref, cfg = observer(tmp_path)
    image = frame(ref, cfg, ["1S", "PASS", "PASS", "PASS"])
    image[30:58, 0:80] = 255
    obs.observe(image, 0)
    result = obs.result()["auctions"][0]
    assert result["contract"] is None and result["dealer"] is None
    assert result["visible_fragments"][0]["calls"][0]["seat"] is None
    assert result["visible_fragments"][0]["calls"][0]["call"] == "1S"


def test_board_change_and_return_do_not_combine_temporal_support(tmp_path):
    obs, ref, cfg = observer(tmp_path)
    for i, board in enumerate(["BOARD A", "BOARD B", "BOARD A"]):
        obs.observe(frame(ref, cfg, ["1S", "PASS", "PASS", "PASS"], counter=i, board=board), i*1000)
    result = obs.result()["auctions"]
    assert len(result) == 3 and len({a["board_occurrence_id"] for a in result}) == 3
    assert all(not a["complete"] for a in result)


def test_context_loss_and_new_job_do_not_reuse_prior_evidence(tmp_path):
    obs, ref, cfg = observer(tmp_path)
    image = frame(ref, cfg, ["1S", "PASS", "PASS", "PASS"])
    obs.observe(image, 0)
    obs.observe(None, 1000)
    obs.observe(image, 2000)
    assert len(obs.result()["auctions"]) == 2
    other = AuctionObserver(cfg, ref, source_id="other-source", output_dir=tmp_path/"other")
    other.observe(image, 0)
    assert other.result()["auctions"][0]["board_occurrence_id"] != obs.result()["auctions"][0]["board_occurrence_id"]


def test_incompatible_prefixes_are_qc_conflict_not_repaired(tmp_path):
    obs, ref, cfg = observer(tmp_path)
    obs.observe(frame(ref, cfg, ["1S", "PASS"], counter=1), 0)
    obs.observe(frame(ref, cfg, ["2H", "PASS", "PASS", "PASS"], counter=2), 1000)
    result = obs.result()["auctions"][0]
    assert result["status"] == "CONFLICT" and result["contract"] is None


def test_illegal_display_is_retained_for_review(tmp_path):
    obs, ref, cfg = observer(tmp_path)
    for i in range(2):
        obs.observe(frame(ref, cfg, ["1S", "PASS", "X"], counter=i), i*1000)
    result = obs.result()["auctions"][0]
    assert result["status"] == "REVIEW" and result["contract"] is None
    assert [c["call"] for c in result["ordered_calls"]] == ["1S", "PASS", "X"]


def test_truncation_clears_completion_and_preserves_existing_evidence(tmp_path, monkeypatch):
    import bridge_vision.auction_observer as module
    monkeypatch.setattr(module, "MAX_SNAPSHOTS", 2)
    obs, ref, cfg = observer(tmp_path)
    for i in range(3):
        obs.observe(frame(ref, cfg, ["1S", "PASS", "PASS", "PASS"], counter=i), i*1000)
    result = obs.result()
    assert result["status"] == "TRUNCATED" and not result["auctions"][0]["complete"]


@pytest.mark.parametrize("text,kind", [
    ("Не говорим 1S, здесь PASS.", "NEGATED"),
    ("Если 1S, то XX?", "HYPOTHESIS"),
    ("Например 1S X XX PASS.", "HYPOTHESIS"),
    ("Он сказал 1S PASS.", "UNVERIFIED_MENTION"),
])
def test_speech_never_enters_actual_auction(text, kind):
    result = speech_mentions([{"text": text, "start": 1, "end": 2}])[0]
    assert result["statement_type"] == kind
    assert result["actual_auction_evidence"] is False
    assert result["seat"] is result["board_occurrence_id"] is None


def test_adapter_persists_partial_auctions_without_full_hands_and_isolates_job(monkeypatch, tmp_path):
    from bridge_vision import bridgit_primary_production_candidate as adapter
    auction = {"board_occurrence_id": "synthetic", "complete": False}
    monkeypatch.setattr(adapter, "_run_primary", lambda *args: (
        [], [], {"status": "NO_FULL_LAYOUT_ACCEPTED", "auction_recognition": {"auctions": [auction]}}))
    base = SimpleNamespace(visual=lambda *args: (None, None, []),
        derive_deals_decisions=lambda *args: ([], []),
        master_analysis_payload=lambda **kwargs: {})
    adapter.install(base, lambda: "unused")
    base.visual(tmp_path/"video", tmp_path, 1, [], "one")
    own = base.master_analysis_payload(job_id="one", transcript=[{"text": "Если 1S"}])
    assert own["auction_observations"] == [auction]
    assert own["auction_speech_mentions"][0]["statement_type"] == "HYPOTHESIS"
    other = base.master_analysis_payload(job_id="other", transcript=[{"text": "1S"}])
    assert other["auction_observations"] == other["auction_speech_mentions"] == []


def test_profile_from_existing_rank_file_is_optional_and_socket_free(tmp_path, monkeypatch):
    from bridge_vision.auction_observer import observer_from_profile
    profile = tmp_path/"profile.json"
    profile.write_text("{}")
    ref, cfg = fixture()
    assert observer_from_profile(profile, ref, video_path=tmp_path/"absent", output_dir=tmp_path) is None
    profile.write_text(json.dumps({"auction": cfg}))
    video = tmp_path/"synthetic-source.bin"
    video.write_bytes(b"synthetic fixture identity only")
    result = observer_from_profile(profile, ref, video_path=video, output_dir=tmp_path)
    result.observe(frame(ref, cfg, ["1S", "PASS"]), 0)
    assert result.result()["auctions"]


def test_invalid_geometry_and_empty_markers_fail_closed(tmp_path):
    ref, cfg = fixture()
    cfg["cells"][0] = [-1, 0, 80, 28]
    with pytest.raises(ValueError, match="outside"):
        AuctionObserver(cfg, ref, source_id="s", output_dir=tmp_path)
    ref, cfg = fixture()
    cfg["start_marker"] = cfg["blank"]
    with pytest.raises(ValueError, match="visible marks"):
        AuctionObserver(cfg, ref, source_id="s", output_dir=tmp_path)


def test_real_decoded_frames_reach_auction_even_without_card_geometry(monkeypatch, tmp_path):
    from bridge_vision import bridgit_primary_video_candidate as primary
    from bridge_vision.gambler_reference_authority import pinned_sprite_sha256
    ref, cfg = fixture()
    reference = tmp_path/"reference.png"
    assert cv2.imwrite(str(reference), ref)
    profile = tmp_path/"profile.json"
    profile.write_text(json.dumps({"auction": cfg}))
    video = tmp_path/"synthetic.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"FFV1"), 1, (400, 360))
    assert writer.isOpened()
    for i in range(3):
        writer.write(frame(ref, cfg, ["1S", "X", "XX", "PASS", "PASS", "PASS"], counter=i))
    writer.release()
    monkeypatch.setattr(primary.rank_layout, "load_profile", lambda *args: SimpleNamespace(width=400, height=360))
    monkeypatch.setattr(primary.rank_layout, "_template_bank", lambda *args: {})
    monkeypatch.setattr(primary, "derive_original_asset_reference", lambda *args, **kwargs: ref)
    monkeypatch.setattr(primary, "resolve_original_gambler_asset",
                        lambda *args, **kwargs: (5, tmp_path/"sprite", pinned_sprite_sha256(5)))
    monkeypatch.setattr(primary, "load_sprite",
                        lambda *args, **kwargs: SimpleNamespace(card_width=109, card_height=147))
    monkeypatch.setattr(primary, "frame_signature", lambda *args: (_ for _ in ()).throw(ValueError("no full hand")))
    result = primary.recognize_video_primary(video, reference_frame=reference, profile_path=profile,
        output_dir=tmp_path/"output", gambler_asset_root=tmp_path, scan_ms=1000)
    assert result["deals"] == []
    auction = result["auction_recognition"]["auctions"][0]
    assert auction["complete"] and auction["contract"] == "1SXX"
    assert len(auction["observations"]) == 3


def test_auction_pixel_backend_runs_under_unmodified_pr2120_offline_guard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    runner_root = os.environ.get("RECOGNIZER_COMPARISON_RUNNER_ROOT")
    if not runner_root:
        pytest.skip("pinned PR2120 checkout not supplied")
    script = """
import importlib.util, pathlib, sys
root, runner_root, output = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))
spec = importlib.util.spec_from_file_location("runner", runner_root / "tools/recognizer_compare.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from test_visual_auction_extraction import fixture, frame
from bridge_vision.auction_observer import AuctionObserver
sys.addaudithook(runner.offline_audit)
ref, cfg = fixture()
observer = AuctionObserver(cfg, ref, source_id="synthetic", output_dir=output)
for i in range(2):
    observer.observe(frame(ref, cfg, ["1S", "X", "XX", "PASS", "PASS", "PASS"], counter=i), i*1000)
assert observer.result()["auctions"][0]["contract"] == "1SXX"
print("OFFLINE_AUCTION_PIXELS_PASS")
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script,
        str(Path(__file__).parents[1]), runner_root, str(tmp_path)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OFFLINE_AUCTION_PIXELS_PASS"


def test_sparse_marker_cannot_match_blank_by_average_error():
    from bridge_vision.auction_observer import _same_mark
    expected = np.full((100, 100, 3), 255, np.uint8)
    expected[30:35, 30:35] = 0
    blank = np.full_like(expected, 255)
    assert float(expected.std()) > 2
    assert not _same_mark(blank, expected)
    assert _same_mark(expected, expected)


def test_sparse_faded_call_is_unknown_not_blank(tmp_path):
    obs, ref, cfg = observer(tmp_path)
    image = ref.copy()
    x, y, w, h = cfg["cells"][0]
    image[y+3:y+5, x+3:x+5] = 240
    reading = obs._read_cell(image, cfg["cells"][0])
    assert reading["status"] == "UNREADABLE"


def test_auction_runtime_has_new_opt_in_revision():
    import bridge_runtime_hardening_r26_candidate as runtime
    assert runtime.REVISION == "3.1-free-r26.3-auction-candidate3"
    assert runtime.PRODUCTION_ALLOWED is False


def test_wrong_calibration_reference_is_rejected(tmp_path):
    ref, cfg = fixture()
    ref[359, 399] = 0
    with pytest.raises(ValueError, match="reference pixels"):
        AuctionObserver(cfg, ref, source_id="s", output_dir=tmp_path)


@pytest.mark.parametrize("repeat_old_pixels", [False, True])
def test_completed_auction_cannot_survive_rewind(tmp_path, repeat_old_pixels):
    obs, ref, cfg = observer(tmp_path)
    early = frame(ref, cfg, ["1S"], counter=0)
    obs.observe(early, 0)
    for i in (1, 2):
        obs.observe(frame(ref, cfg, ["1S", "PASS", "PASS", "PASS"], counter=i), i*1000)
    assert obs.result()["auctions"][0]["complete"]
    obs.observe(early if repeat_old_pixels else frame(ref, cfg, ["1S"], counter=3), 3000)
    result = obs.result()["auctions"][0]
    assert result["status"] == "REVIEW"
    assert not result["complete"] and result["contract"] is result["declarer"] is None
    assert result["latest_observation_timestamp_ms"] == 3000


@pytest.mark.parametrize("change", ["blank", "header", "start"])
def test_complete_result_loses_confirmation_when_visible_context_is_lost(tmp_path, change):
    obs, ref, cfg = observer(tmp_path)
    calls = ["1S", "PASS", "PASS", "PASS"]
    for i in range(2):
        obs.observe(frame(ref, cfg, calls, counter=i), i*1000)
    changed = frame(ref, cfg, [] if change == "blank" else calls, counter=2, top=change != "start")
    if change == "header":
        changed[30:58, 0:80] = 255
    obs.observe(changed, 2000)
    assert not obs.result()["auctions"][0]["complete"]


def test_unsupported_auction_dimensions_are_reported(tmp_path):
    obs, ref, cfg = observer(tmp_path)
    obs.observe(np.pad(ref, ((0, 20), (0, 0), (0, 0)), constant_values=255), 0)
    result = obs.result()
    assert result["status"] == "PARTIAL_COVERAGE"
    assert result["coverage_issues"] == ["UNSUPPORTED_AUCTION_DIMENSIONS"]


def test_auction_only_pdf_retains_pixels_and_calibration_after_work_cleanup(monkeypatch, tmp_path):
    import fitz
    import tempfile
    from pathlib import Path
    from bridge_vision import bridgit_primary_production_candidate as adapter

    def original_embed(pdf, master):
        raw = json.dumps(master).encode()
        with fitz.open(pdf) as doc:
            doc.embfile_add("master_analysis.json", raw)
            doc.saveIncr()
        return hashlib.sha256(raw).hexdigest()

    pdf = tmp_path/"result.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(pdf)
    base = SimpleNamespace(visual=lambda *args: (None, None, []),
        derive_deals_decisions=lambda *args: ([], []),
        master_analysis_payload=lambda **kwargs: {"job_id": kwargs["job_id"]},
        embed_master=original_embed)
    with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
        work = Path(directory)
        obs, ref, cfg = observer(work)
        reference = work/"reference.png"
        assert cv2.imwrite(str(reference), ref)
        for i in range(2):
            obs.observe(frame(ref, cfg, ["1S", "PASS", "PASS", "PASS"], counter=i), i*1000)
        raw = obs.result()
        exported, files = adapter._prepare_auction_export(raw, reference)
        assert "frame_path" not in json.dumps(exported)
        expected = {f["name"]: f["sha256"] for f in files}
        monkeypatch.setattr(adapter, "_run_primary", lambda *args: (
            [], [], {"auction_recognition": exported, "_auction_export_files": files}))
        adapter.install(base, lambda: "unused")
        base.visual(work/"unused", work, 3, [], "one")
        master = base.master_analysis_payload(job_id="one")
        assert master["auction_observations"] and master["content_quality"]["primary_visual_deals"] == 0
        assert "_auction_export_files" not in json.dumps(master)
        assert str(work) not in json.dumps(master)
        with pytest.raises(adapter.CandidateInstallationError, match="another job"):
            base.embed_master(pdf, dict(master, auction_evidence_manifest=[]))
        base.embed_master(pdf, master)
        with pytest.raises(adapter.CandidateInstallationError, match="another job"):
            base.embed_master(pdf, dict(master, job_id="foreign"))

    assert not work.exists()
    with fitz.open(pdf) as doc:
        embedded_master = json.loads(doc.embfile_get("master_analysis.json"))
        assert embedded_master["auction_evidence_manifest"] == master["auction_evidence_manifest"]
        assert len(expected) == 4  # Two snapshots, calibration PNG, profile JSON.
        for name, sha in expected.items():
            assert hashlib.sha256(doc.embfile_get(name)).hexdigest() == sha


def test_tampered_or_missing_auction_evidence_stops_export_before_master(tmp_path):
    from bridge_vision import bridgit_primary_production_candidate as adapter
    path = tmp_path/"frame.png"
    path.write_bytes(b"changed")
    files = [{"name": "frame.png", "path": str(path), "sha256": hashlib.sha256(b"original").hexdigest()}]
    called = []
    with pytest.raises(adapter.CandidateInstallationError, match="changed"):
        adapter._embed_auction_evidence(tmp_path/"unused.pdf", {}, files, lambda *args: called.append(True))
    path.unlink()
    with pytest.raises(FileNotFoundError):
        adapter._embed_auction_evidence(tmp_path/"unused.pdf", {}, files, lambda *args: called.append(True))
    assert not called
