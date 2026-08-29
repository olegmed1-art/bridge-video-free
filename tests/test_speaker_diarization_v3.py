from pathlib import Path

import numpy as np

import bridge_speaker_diarization_v3 as diarization

from bridge_speaker_diarization_v3 import (
    EMBEDDING_URL,
    NEMO_EMBEDDING_URL,
    THREED_EMBEDDING_URL,
    _cluster_embeddings_two,
    _cluster_embeddings_open_set,
    _collapse_diagnostics,
    _candidate_rank,
    _coverage_diagnostics,
    _hypothesis_score,
)


def _turns(count_a: int, count_b: int, duration_a: float = 1.0, duration_b: float = 1.0):
    turns = []
    t = 0.0
    for speaker, count, duration in ((0, count_a, duration_a), (1, count_b, duration_b)):
        for _ in range(count):
            turns.append({"start": t, "end": t + duration, "speaker": speaker})
            t += duration + 0.01
    return turns


def test_field_like_952_6_collapse_is_detected():
    turns = _turns(952, 6, 1.0, 1.0)
    assignments = [0] * 952 + [1] * 6
    report = _collapse_diagnostics(turns, assignments)
    assert report["cluster_collapse_detected"] is True
    assert "MINOR_SEGMENT_CLUSTER_NEAR_EMPTY" in report["collapse_reasons"]
    assert report["speaker_segment_counts"] == {"0": 952, "1": 6}


def test_naturally_imbalanced_90_10_is_not_forced_to_balance():
    turns = _turns(90, 10, 1.0, 1.0)
    assignments = [0] * 90 + [1] * 10
    report = _collapse_diagnostics(turns, assignments)
    assert report["cluster_collapse_detected"] is False
    assert report["minor_segment_ratio"] == 0.1


def test_noncollapsed_hypothesis_scores_above_collapsed_one():
    collapsed = _collapse_diagnostics(_turns(952, 6), [0] * 952 + [1] * 6)
    sane = _collapse_diagnostics(_turns(90, 10), [0] * 90 + [1] * 10)
    assert _hypothesis_score(sane) > _hypothesis_score(collapsed)


def test_candidate_selection_prefers_coverage_without_accepting_collapse():
    low_coverage = {
        "cluster_collapse_detected": False,
        "segment_coverage": 0.63,
        "speech_duration_coverage": 0.71,
        "score": 3.2,
    }
    field_ready = {
        "cluster_collapse_detected": False,
        "segment_coverage": 0.91,
        "speech_duration_coverage": 0.94,
        "score": 3.0,
    }
    collapsed = {
        "cluster_collapse_detected": True,
        "segment_coverage": 0.99,
        "speech_duration_coverage": 0.99,
        "score": 9.0,
    }
    assert _candidate_rank(field_ready) > _candidate_rank(low_coverage)
    assert _candidate_rank(low_coverage) > _candidate_rank(collapsed)


def test_candidate_coverage_is_recomputed_from_transcript_durations():
    segments = [
        {"start": 0.0, "end": 1.0},
        {"start": 1.0, "end": 5.0},
        {"start": 5.0, "end": 10.0},
    ]
    report = _coverage_diagnostics(segments, [0, None, 1])
    assert report == {
        "segments_total": 3,
        "segment_coverage": 0.666667,
        "speech_duration_coverage": 0.6,
    }


def test_end_to_end_selector_does_not_discard_higher_coverage_sherpa_candidate(
    monkeypatch, tmp_path: Path
):
    segments = [
        {
            "start": float(index),
            "end": float(index + 1),
            "text": f"segment {index}",
            "chunk": 0,
            "unreliable": False,
        }
        for index in range(20)
    ]
    model = tmp_path / "model.onnx"
    model.write_bytes(b"bounded-test-model")

    def extract(_video, wav):
        wav.write_bytes(b"bounded-test-wav")

    def turns(count):
        return [
            {"start": float(index), "end": float(index + 1), "speaker": index % 2}
            for index in range(count)
        ]

    count_evidence = {
        "mode": "OPEN_SET",
        "candidate_counts": [],
        "selected_count": 2,
        "selection_margin": 0.2,
        "collapse_check": "PASS",
        "fragmentation_check": "PASS",
        "mixing_check": "PASS",
    }
    monkeypatch.setattr(diarization.previous, "_extract_pcm", extract)
    monkeypatch.setattr(diarization, "_ensure_segmentation", lambda _cache: model)
    monkeypatch.setattr(diarization, "_ensure_embedding", lambda _cache, _kind: model)
    monkeypatch.setattr(
        diarization,
        "diarize_with_open_set_embeddings",
        lambda *_args, **_kwargs: (
            turns(12),
            {
                "engine": "segment-embedding-open-set",
                "model_id": "3dspeaker-segment-open-set",
                "speaker_count_evidence": count_evidence,
            },
        ),
    )
    monkeypatch.setattr(
        diarization,
        "_run_sherpa_model",
        lambda *_args, **_kwargs: (
            turns(20),
            {
                "engine": "sherpa-onnx",
                "model_id": "pyannote+3dspeaker",
                "num_speakers_requested": 2,
            },
        ),
    )

    output, report = diarization.diarize_transcript(
        tmp_path / "lesson.mp4",
        segments,
        tmp_path / "work",
        minimum_segments_per_cluster=2,
    )

    assert report["status"] == "DIARIZED_UNMAPPED"
    assert report["selected_hypothesis"] == "pyannote+3dspeaker"
    assert report["segments_labeled"] == 20
    assert report["segment_coverage"] == 1.0
    assert report["speech_duration_coverage"] == 1.0
    assert report["speaker_count_evidence"] == count_evidence
    assert sum(bool(item.get("speaker")) for item in output) == 20


def test_failed_comparison_candidate_does_not_discard_valid_open_set_result(
    monkeypatch, tmp_path: Path
):
    segments = [
        {
            "start": float(index),
            "end": float(index + 1),
            "text": f"segment {index}",
            "chunk": 0,
            "unreliable": False,
        }
        for index in range(20)
    ]
    model = tmp_path / "model.onnx"
    model.write_bytes(b"bounded-test-model")
    open_turns = [
        {"start": float(index), "end": float(index + 1), "speaker": index % 2}
        for index in range(20)
    ]
    count_evidence = {
        "mode": "OPEN_SET",
        "candidate_counts": [],
        "selected_count": 2,
        "selection_margin": 0.2,
        "collapse_check": "PASS",
        "fragmentation_check": "PASS",
        "mixing_check": "PASS",
    }

    monkeypatch.setattr(
        diarization.previous,
        "_extract_pcm",
        lambda _video, wav: wav.write_bytes(b"bounded-test-wav"),
    )
    monkeypatch.setattr(diarization, "_ensure_segmentation", lambda _cache: model)
    monkeypatch.setattr(diarization, "_ensure_embedding", lambda _cache, _kind: model)
    monkeypatch.setattr(
        diarization,
        "diarize_with_open_set_embeddings",
        lambda *_args, **_kwargs: (
            open_turns,
            {
                "engine": "segment-embedding-open-set",
                "model_id": "3dspeaker-segment-open-set",
                "speaker_count_evidence": count_evidence,
            },
        ),
    )
    monkeypatch.setattr(
        diarization,
        "_run_sherpa_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("candidate failed")),
    )

    output, report = diarization.diarize_transcript(
        tmp_path / "lesson.mp4",
        segments,
        tmp_path / "work",
        minimum_segments_per_cluster=2,
    )

    assert report["status"] == "DIARIZED_UNMAPPED"
    assert report["selected_hypothesis"] == "3dspeaker-segment-open-set"
    assert report["segments_labeled"] == 20
    assert any(
        item.get("model_id") == "pyannote+3dspeaker"
        and item.get("status") == "FAILED_SOFT"
        for item in report["hypotheses"]
    )
    assert sum(bool(item.get("speaker")) for item in output) == 20


def test_embedding_recluster_separates_two_clouds_deterministically():
    rng = np.random.default_rng(12345)
    a = rng.normal(loc=0.0, scale=0.02, size=(16, 12)).astype("float32")
    b = rng.normal(loc=0.0, scale=0.02, size=(16, 12)).astype("float32")
    a[:, 0] += 1.0
    b[:, 1] += 1.0
    matrix = np.vstack([a, b])
    labels1, report1 = _cluster_embeddings_two(matrix)
    labels2, report2 = _cluster_embeddings_two(matrix)
    assert labels1 == labels2
    assert report1 == report2
    assert report1["repair_validation_passed"] is True
    assert sorted(report1["cluster_counts"].values()) == [16, 16]
    assert len(set(labels1[:16])) == 1
    assert len(set(labels1[16:])) == 1
    assert labels1[0] != labels1[16]


def test_public_embedding_assets_are_token_free_and_primary_is_3dspeaker():
    assert EMBEDDING_URL == THREED_EMBEDDING_URL
    assert "3dspeaker" in THREED_EMBEDDING_URL.lower()
    assert "nemo" in NEMO_EMBEDDING_URL.lower()
    for url in (THREED_EMBEDDING_URL, NEMO_EMBEDDING_URL):
        assert url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
        assert "token" not in url.lower()


def _speaker_clouds(count: int):
    rng = np.random.default_rng(20260829 + count)
    rows = []
    for index in range(count):
        cloud = rng.normal(0.0, 0.025, size=(24, 12)).astype("float32")
        cloud[:, index] += 1.0
        rows.append(cloud)
    return np.vstack(rows)


def test_open_set_selects_two_without_fixed_two_request():
    labels, evidence = _cluster_embeddings_open_set(_speaker_clouds(2))
    assert evidence["mode"] == "OPEN_SET"
    assert evidence["selected_count"] == 2
    assert len(set(labels)) == 2
    assert evidence["collapse_check"] == "PASS"
    assert evidence["fragmentation_check"] == "PASS"
    assert evidence["mixing_check"] == "PASS"


def test_open_set_detects_third_speaker_instead_of_forcing_two():
    labels, evidence = _cluster_embeddings_open_set(_speaker_clouds(3))
    assert evidence["selected_count"] == 3
    assert len(set(labels)) == 3
    candidates = {item["candidate_count"]: item for item in evidence["candidate_counts"]}
    assert candidates[2]["candidate_valid"] is True
    assert candidates[3]["selection_score"] > candidates[2]["selection_score"]
