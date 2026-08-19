import numpy as np

from bridge_speaker_diarization_v3 import (
    EMBEDDING_URL,
    NEMO_EMBEDDING_URL,
    THREED_EMBEDDING_URL,
    _cluster_embeddings_two,
    _collapse_diagnostics,
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
