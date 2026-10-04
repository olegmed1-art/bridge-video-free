"""Synthetic control-flow regressions; no claim of pixel-recognition accuracy."""
from dataclasses import dataclass
from contextlib import nullcontext
import hashlib
from types import SimpleNamespace

import pytest

from bridge_vision import bridgit_primary_production_candidate as adapter
from bridge_vision import bridgit_primary_video_candidate as primary
from bridge_vision import bridgit_rank_layout as ranks
from bridge_vision.bridgit_primary_compat import infer_horizontal_fan_model
from bridge_vision.gambler_reference_authority import pinned_sprite_sha256


@dataclass(frozen=True)
class Frame:
    time: int
    state: int
    shape: tuple = (1010, 1920, 3)

    def copy(self):
        return self


def recognized(state):
    deck = [r + s for s in "HCDS" for r in "AKQJT98765432"]
    hands = {seat: {s: [c[0] for c in deck[(index + state) % 4::4] if c[1] == s]
                    for s in "HCDS"} for index, seat in enumerate("NESW")}
    return {"status": "SHADOW_FULL_LAYOUT_CANDIDATE", "hands": hands,
            "integrity": {"cards": 52, "unique": 52, "seat_counts": dict.fromkeys("NESW", 13)},
            "evidence": {"per_frame_deal_agreement": True, "minimum_assigned_score": .9}}


@pytest.fixture
def video_pass(monkeypatch, tmp_path):
    # Lightweight contract CI omits pixel dependencies; the dedicated guard
    # workflow installs numpy and explicitly rejects any skipped guard case.
    np = pytest.importorskip("numpy")
    state = SimpleNamespace(duration=40000, scenes=[(0, 0)], attempts=[], written={},
                            released=False, duplicate=set(), rejected=set(), fault=None,
                            missing=set(), geometry_change=set(), geometry_unknown=set(), processed_at=[], now=0)
    def frame_at(capture, timestamp):
        # Only integer scan seconds update the processing-clock observation.
        if timestamp % 1000 == 0:
            state.now = timestamp
        if timestamp in state.missing:
            return None
        scene = next(value for start, value in reversed(state.scenes) if timestamp >= start)
        return Frame(timestamp, scene)
    capture = SimpleNamespace(isOpened=lambda: True,
        get=lambda key: {1: 1000.0, 2: state.duration, 3: 1920, 4: 1010}[key],
        release=lambda: setattr(state, "released", True))
    cv = SimpleNamespace(IMREAD_COLOR=1, CAP_PROP_FPS=1, CAP_PROP_FRAME_COUNT=2,
                         CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
                         imread=lambda *a: Frame(0, 0), VideoCapture=lambda *a: capture)
    profile = SimpleNamespace(width=1920, height=1010)
    monkeypatch.setattr(ranks, "_pixel_runtime", lambda: (cv, np))
    monkeypatch.setattr(ranks, "load_profile", lambda *a: profile)
    monkeypatch.setattr(ranks, "_template_bank", lambda *a: {})
    monkeypatch.setattr(primary, "resolve_original_gambler_asset", lambda *a, **k: (5, tmp_path / "sprite", pinned_sprite_sha256(5)))
    monkeypatch.setattr(primary, "load_sprite", lambda *a, **k: SimpleNamespace(card_width=109, card_height=147))
    monkeypatch.setattr(primary, "derive_original_asset_reference", lambda *a, **k: Frame(0, 0))
    monkeypatch.setattr(primary, "_frame_at", frame_at)
    monkeypatch.setattr(primary, "frame_signature", lambda frame, *a: np.full((120, 96), frame.state * 60, dtype=np.uint8))
    monkeypatch.setattr(primary, "_full_geometry_gate", lambda frame, *a:
                        None if frame.time in state.geometry_unknown else {"geometry": frame.time in state.geometry_change})
    monkeypatch.setattr(primary, "native_gambler_geometry", nullcontext)
    def write(path, frame):
        state.written[str(path)] = frame
        payload = f"synthetic:{frame.time}:{frame.state}".encode()
        path.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(primary, "_write_png", write)
    def backend(reference, paths, profile, **kwargs):
        first, second = (state.written[str(path)] for path in paths)
        state.attempts.append((first.time, second.time, first.state, second.state))
        state.processed_at.append(state.now)
        if state.fault is not None:
            raise state.fault
        if first.time in state.duplicate:
            ranks._validate_temporal_identities("a" * 64, "c" * 64, ["b" * 64] * 2, ["d" * 64] * 2)
        if first.time in state.rejected or first.state != second.state:
            return {"status": "AMBIGUOUS"}
        return recognized(first.state)
    monkeypatch.setattr(primary, "recognize_frames_with_original_gambler_deck", backend)
    def run():
        return primary.recognize_video_primary(tmp_path / "video", reference_frame=tmp_path / "ref",
            profile_path=tmp_path / "profile", output_dir=tmp_path / "output", gambler_asset_root=tmp_path,
            scan_ms=1000, attempt_gap_ms=15000)
    state.run = run
    return state


def test_short_new_board_is_buffered_during_cooldown(video_pass):
    video_pass.scenes = [(0, 0), (5000, 1), (10000, 2)]
    result = video_pass.run()
    assert [a[0] for a in video_pass.attempts] == [1000, 6000, 11000]
    assert video_pass.processed_at == [1000, 16000, 31000]
    assert [a[2:] for a in video_pass.attempts] == [(0, 0), (1, 1), (2, 2)]
    assert len(result["deals"]) == 3
    assert video_pass.released


def test_retry_survives_cooldown_and_is_bounded(video_pass):
    video_pass.rejected = {1000, 3000}
    result = video_pass.run()
    assert [a[0] for a in video_pass.attempts] == [1000, 3000]
    assert video_pass.processed_at == [1000, 16000]
    assert result["deals"] == []
    assert result["rejections"]["AMBIGUOUS"] == 2


@pytest.mark.parametrize("field,failed_time,reason", [
    ("missing", 1600, "retry_decode"),
    ("geometry_unknown", 1000, "full_geometry_not_proven"),
    ("geometry_change", 1600, "geometry_not_stable"),
])
def test_incomplete_pair_retries_without_inventing_a_result(video_pass, field, failed_time, reason):
    getattr(video_pass, field).add(failed_time)
    result = video_pass.run()
    assert result["rejections"][reason] == 1
    assert [a[0] for a in video_pass.attempts] == [3000]
    assert [d["timestamp_ms"] for d in result["deals"]] == [3000]


def test_rejected_old_board_does_not_retry_as_the_new_board(video_pass):
    video_pass.scenes = [(0, 0), (5000, 1), (10000, 2)]
    video_pass.rejected = {6000}
    result = video_pass.run()
    assert [a[0] for a in video_pass.attempts] == [1000, 6000, 11000]
    assert [d["timestamp_ms"] for d in result["deals"]] == [1000, 11000]


def test_success_does_not_schedule_a_redundant_short_retry(video_pass):
    result = video_pass.run()
    assert len(video_pass.attempts) == len(result["deals"]) == 1


def test_duplicate_observation_preserves_already_accepted_deal(video_pass):
    video_pass.scenes = [(0, 0), (20000, 1)]
    video_pass.duplicate = {21000, 23000}
    result = video_pass.run()
    assert result["rejections"]["duplicate_observation"] == 2
    assert [d["timestamp_ms"] for d in result["deals"]] == [1000]
    assert video_pass.released


@pytest.mark.parametrize("message", sorted(primary.DUPLICATE_OBSERVATION_ERRORS))
def test_each_known_duplicate_error_rejects_only_the_observation(video_pass, message):
    video_pass.fault = ranks.BridgitRankLayoutError(message)
    result = video_pass.run()
    assert result["deals"] == []
    assert result["rejections"]["duplicate_observation"] == 2


@pytest.mark.parametrize("fault", [RuntimeError("backend bug"), ranks.BridgitRankLayoutError("observation frame changed before recognition")])
def test_program_or_integrity_failure_is_not_disguised_as_missing_evidence(video_pass, fault):
    video_pass.fault = fault
    with pytest.raises(type(fault), match=str(fault)):
        video_pass.run()
    assert video_pass.released


def test_board_changes_inside_pair_cannot_become_complete(video_pass):
    video_pass.scenes = [(0, 0), (1200, 1)]
    result = video_pass.run()
    assert result["rejections"]["AMBIGUOUS"] == 1
    assert all(d["timestamp_ms"] != 1000 for d in result["deals"])
    assert video_pass.attempts[0][2:] == (0, 1)


def test_no_zero_duration_pair_at_end_of_video(video_pass):
    video_pass.duration = 1001
    result = video_pass.run()
    assert not video_pass.attempts
    assert result["rejections"]["geometry_not_stable"] == 1


def test_final_short_board_is_drained_with_original_timestamps(video_pass):
    video_pass.duration = 8000
    video_pass.scenes = [(0, 0), (5000, 1)]
    result = video_pass.run()
    assert [a[:2] for a in video_pass.attempts] == [(1000, 1600), (6000, 6600)]
    assert len(result["deals"]) == 2


def test_queue_overflow_is_explicit_and_bounded(video_pass):
    video_pass.duration = 15000
    video_pass.scenes = [(t, (t // 2000) % 4) for t in range(0, 15000, 2000)]
    result = video_pass.run()
    assert result["max_pending_pairs"] == 4
    assert len(video_pass.attempts) <= 1 + primary.MAX_PENDING_PAIRS
    assert result["rejections"]["observation_queue_overflow"] > 0
    assert result["status"] == "PRIMARY_PARTIAL_COVERAGE"


def test_adapter_propagates_internal_error(monkeypatch, tmp_path):
    def broken(*args):
        raise RuntimeError("synthetic internal defect")
    monkeypatch.setattr(adapter, "_prepare_profile_seed", broken)
    with pytest.raises(RuntimeError, match="internal defect"):
        adapter._run_primary(None, "unused", tmp_path / "video", tmp_path, "synthetic")


def test_void_hand_still_abstains_until_geometry_is_separately_supported():
    assert infer_horizontal_fan_model([209, 184, 184]) is None
    assert infer_horizontal_fan_model([209, 184, 184, 0]) is None
