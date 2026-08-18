from bridge_speaker_diarization_v2 import (
    DIARIZATION_REVISION,
    EMBEDDING_URL,
    SEGMENTATION_URL,
    _assign_speakers_from_turns,
    _map_roles_v2,
)


def test_clear_turn_overlap_maps_two_anonymous_speakers():
    segments = [
        {"start": 0.1, "end": 1.9, "text": "Диана, давай посмотрим на эту сдачу."},
        {"start": 2.1, "end": 3.8, "text": "Я думаю, у меня пять червей."},
        {"start": 4.1, "end": 5.8, "text": "Почему ты выбрала эту заявку?"},
        {"start": 6.1, "end": 7.8, "text": "Я не помню правило."},
    ]
    turns = [
        {"start": 0.0, "end": 2.0, "speaker": 7},
        {"start": 2.0, "end": 4.0, "speaker": 12},
        {"start": 4.0, "end": 6.0, "speaker": 7},
        {"start": 6.0, "end": 8.0, "speaker": 12},
    ]
    enriched, report, assignments = _assign_speakers_from_turns(segments, turns)
    assert report["segments_labeled"] == 4
    assert [item["speaker"] for item in enriched] == [
        "SPEAKER_A", "SPEAKER_B", "SPEAKER_A", "SPEAKER_B"
    ]
    assert assignments == [0, 1, 0, 1]
    assert all(item["speaker_assignment_revision"] == DIARIZATION_REVISION for item in enriched)


def test_mixed_speaker_span_fails_closed():
    segments = [{"start": 0.0, "end": 2.0, "text": "перебивают друг друга"}]
    turns = [
        {"start": 0.0, "end": 1.0, "speaker": 0},
        {"start": 1.0, "end": 2.0, "speaker": 1},
    ]
    enriched, report, assignments = _assign_speakers_from_turns(segments, turns)
    assert assignments == [None]
    assert report["segments_labeled"] == 0
    assert "speaker" not in enriched[0]


def test_teacher_student_role_mapping_requires_cluster_level_evidence():
    segments = []
    assignments = []
    for _ in range(12):
        segments.append({"text": "Диана, давай посмотрим. Почему ты выбрала эту заявку? Скажи, что у тебя."})
        assignments.append(0)
        segments.append({"text": "Я думаю, у меня фит. Я не помню точно, но мне кажется так."})
        assignments.append(1)
    mapping, report = _map_roles_v2(segments, assignments)
    assert report["mapping_supported"] is True
    assert mapping == {0: "teacher", 1: "student"}


def test_models_are_public_token_free_release_assets():
    assert SEGMENTATION_URL.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
    assert EMBEDDING_URL.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
    assert "token" not in SEGMENTATION_URL.lower()
    assert "token" not in EMBEDDING_URL.lower()
