from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/issue-840-server-transcript-replay.yml"


def test_replay_is_pinned_read_only_and_does_not_start_media():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "StrictHostKeyChecking=yes" in text
    assert "SHA256:NXmGcng3fzof9b6Hs5Xgh4yYnzxGyVwa/EcfOxu0WPk" in text
    assert "sudo -n bash -s" in text
    assert "spool/results" in text
    assert "transcript.jsonl" in text
    assert "speaker_diarization.json" in text
    assert "bridge-video" not in text
    assert "ffmpeg" not in text
    assert "submit-drive-base64" not in text
    assert "systemctl start" not in text


def test_replay_reports_both_coverage_metrics_and_honest_roles():
    text = WORKFLOW.read_text(encoding="utf-8")
    for token in (
        "coverage_by_segments",
        "coverage_by_duration",
        "observed_clusters",
        "open_set_count_proved",
        "role_mapping_supported",
        "teacher_student_attribution",
        "result_scope':'SHADOW_ONLY",
    ):
        assert token in text
