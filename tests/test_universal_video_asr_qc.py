from pathlib import Path

import pytest

from universal_video import runner
from universal_video.runner import (
    _dedupe_segments,
    _qc_summary,
    _repetition_ratio,
    _transcribe_chunk,
    pathological_nonspeech_hallucination,
)


def _segments(text: str):
    return [] if not text else [{"start": 0.0, "end": 5.0, "text": text}]


def test_common_words_do_not_look_like_an_asr_loop():
    text = (
        "это было очень важно потому что это помогло понять общий план и затем мы "
        "проверили ещё один вариант где это слово встречается снова но фразы остаются разными"
    )
    assert _repetition_ratio(text) == 0.0


def test_repeated_phrase_loop_is_detected():
    text = " ".join(["one two three four"] * 12)
    assert _repetition_ratio(text) > 0.50


def test_occasional_nonspeech_marker_is_allowed():
    text = "Начало занятия [Аплодисменты] после этого преподаватель продолжает объяснение"
    assert pathological_nonspeech_hallucination(text) is False


def test_dense_repeated_nonspeech_marker_is_blocked():
    text = " ".join(["[Аплодисменты]"] * 12)
    assert pathological_nonspeech_hallucination(text) is True


def test_overlap_dedupe_uses_proven_similarity_rule_and_keeps_reliable_evidence():
    segments = [
        {
            "start": 298.7,
            "end": 301.2,
            "text": "we compare the two possible plans now",
            "chunk": 0,
            "unreliable": True,
        },
        {
            "start": 299.9,
            "end": 302.4,
            "text": "we compare the two possible plans now",
            "chunk": 1,
            "unreliable": False,
        },
    ]
    result, removed = _dedupe_segments(segments)
    assert removed == 1
    assert len(result) == 1
    assert result[0]["start"] == pytest.approx(298.7)
    assert result[0]["end"] == pytest.approx(302.4)
    assert result[0]["unreliable"] is False
    assert result[0]["deduped_from_chunks"] == [0, 1]


def test_confirmed_no_speech_block_does_not_consume_failure_budget():
    transcript = [{"start": 10.0, "end": 20.0, "text": "real speech"}]
    qc = [
        {"ok": True, "no_speech": True},
        {"ok": True, "no_speech": False},
    ]
    passed, failed, allowed = _qc_summary(transcript, qc)
    assert passed is True
    assert failed == 0
    assert allowed == 0


def test_fully_silent_video_still_cannot_be_completed():
    passed, failed, allowed = _qc_summary([], [{"ok": True, "no_speech": True}])
    assert passed is False
    assert failed == 0
    assert allowed == 0


def test_primary_strict_disagreement_can_be_rescued_by_consensus_retry(monkeypatch, tmp_path: Path):
    def fake_asr(path, *, strict=False, retry=False, initial_prompt=None):
        if retry:
            text = "alpha beta gamma delta zeta"
            return _segments(text), "en", {"duration_after_vad": None, "language_probability": 0.9}
        if strict:
            text = "red green blue yellow orange"
            return _segments(text), "en", {"duration_after_vad": 5.0, "language_probability": 0.9}
        text = "alpha beta gamma delta epsilon"
        return _segments(text), "en", {"duration_after_vad": 5.0, "language_probability": 0.9}

    monkeypatch.setattr(runner, "_asr", fake_asr)
    selected, language, qc = _transcribe_chunk(tmp_path / "x.wav", initial_prompt=None)
    assert language == "en"
    assert qc["retry_used"] is True
    assert qc["ok"] is True
    assert qc["selected_consensus"] >= 0.30
    assert "alpha beta gamma delta" in selected[0]["text"]
    assert qc["failure_reasons"] == []


def test_three_mutually_inconsistent_attempts_remain_unreliable(monkeypatch, tmp_path: Path):
    def fake_asr(path, *, strict=False, retry=False, initial_prompt=None):
        if retry:
            return _segments("cat dog mouse horse"), "en", {"duration_after_vad": None}
        if strict:
            return _segments("red green blue yellow"), "en", {"duration_after_vad": 5.0}
        return _segments("alpha beta gamma delta"), "en", {"duration_after_vad": 5.0}

    monkeypatch.setattr(runner, "_asr", fake_asr)
    selected, _, qc = _transcribe_chunk(tmp_path / "x.wav", initial_prompt=None)
    assert selected
    assert qc["retry_used"] is True
    assert qc["ok"] is False
    assert qc["critical"] is True
    assert "LOW_CROSS_ATTEMPT_CONSENSUS" in qc["failure_reasons"]


def test_vad_confirmed_silence_skips_third_asr_attempt(monkeypatch, tmp_path: Path):
    calls = []

    def fake_asr(path, *, strict=False, retry=False, initial_prompt=None):
        calls.append((strict, retry))
        if retry:
            pytest.fail("confirmed silence must not trigger non-VAD retry")
        return [], None, {"duration_after_vad": 0.0, "language_probability": None}

    monkeypatch.setattr(runner, "_asr", fake_asr)
    selected, language, qc = _transcribe_chunk(tmp_path / "x.wav", initial_prompt=None)
    assert selected == []
    assert language is None
    assert qc["no_speech"] is True
    assert qc["ok"] is True
    assert qc["critical"] is False
    assert calls == [(False, False), (True, False)]
