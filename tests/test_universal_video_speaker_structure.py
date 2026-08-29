from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from universal_video.result_conformance import ResultConformanceError, verify_result
from universal_video.speaker_structure import MIN_TEST_LABEL_COVERAGE, TEST_SCHEMA, run_speaker_structure


def _asr_rows() -> list[dict]:
    return [
        {"start": 0.0, "end": 1.0, "text": "original one", "chunk": 0, "unreliable": False},
        {"start": 1.0, "end": 2.0, "text": "original two", "chunk": 0, "unreliable": False},
    ]


def _diarized(label_a: str = "Diana Veksler", label_b: str = "Student"):
    return [
        {
            **_asr_rows()[0],
            "text": "tampered text must not survive",
            "speaker": label_a,
            "speaker_confidence": 0.82,
            "speaker_role_candidate": "teacher",
            "speaker_role_confidence": 0.73,
        },
        {
            **_asr_rows()[1],
            "speaker": label_b,
            "speaker_confidence": 0.79,
            "speaker_role_candidate": "student",
            "speaker_role_confidence": 0.71,
        },
    ]


def _patch_diarizer(monkeypatch, rows, report):
    import bridge_speaker_diarization
    import bridge_speaker_diarization_v3

    monkeypatch.setattr(bridge_speaker_diarization, "diarize_transcript", lambda *_args, **_kwargs: (rows, report))
    monkeypatch.setattr(bridge_speaker_diarization_v3, "diarize_transcript", lambda *_args, **_kwargs: (rows, report))


def test_success_reanonymizes_source_labels_and_preserves_asr(monkeypatch, tmp_path: Path):
    _patch_diarizer(
        monkeypatch,
        _diarized(),
        {
            "revision": "diarization-test-v1",
            "status": "DIARIZED_ROLE_MAPPED",
            "role_mapping_supported": True,
        },
    )
    rows, report = run_speaker_structure(tmp_path / "lesson.mp4", _asr_rows(), tmp_path)

    assert [row["text"] for row in rows] == ["original one", "original two"]
    assert [row["speaker"] for row in rows] == ["SPEAKER_A", "SPEAKER_B"]
    assert "Diana" not in json.dumps({"rows": rows, "report": report})
    assert "Student" not in json.dumps({"rows": rows, "report": report})
    assert report["status"] == "DIARIZED_ROLE_MAPPED"
    assert report["schema"] == "universal-video-speaker-structure-v1"
    assert "label_coverage" not in report
    assert report["quality_gate"] == "PASS"
    assert report["speaker_clusters"] == {"SPEAKER_A": 1, "SPEAKER_B": 1}
    assert report["teacher_student_attribution"] == "SUGGESTION_ONLY"


def test_mapped_status_without_explicit_role_evidence_degrades_to_unmapped(monkeypatch, tmp_path: Path):
    _patch_diarizer(
        monkeypatch,
        _diarized(),
        {
            "revision": "bridge-sherpa-onnx-diarization-v3",
            "status": "DIARIZED_ROLE_MAPPED",
            "mapping_supported": True,
        },
    )

    rows, report = run_speaker_structure(
        tmp_path / "lesson.mp4",
        _asr_rows(),
        tmp_path,
        min_label_coverage=0.8,
    )

    assert [row["speaker"] for row in rows] == ["SPEAKER_A", "SPEAKER_B"]
    assert all(row["speaker_role_candidate"] == "unknown" for row in rows)
    assert report["status"] == "DIARIZED_UNMAPPED"
    assert report["role_mapping_supported"] is False
    assert report["teacher_student_attribution"] == "UNAVAILABLE"


def test_test_profile_preserves_bounded_open_set_count_evidence(monkeypatch, tmp_path: Path):
    evidence = {
        "mode": "OPEN_SET",
        "candidate_counts": [{"candidate_count": 1}, {"candidate_count": 2}],
        "selected_count": 2,
        "selection_margin": 0.4,
        "collapse_check": "PASS",
        "fragmentation_check": "PASS",
        "mixing_check": "PASS",
    }
    _patch_diarizer(
        monkeypatch,
        _diarized(),
        {
            "revision": "bridge-sherpa-onnx-diarization-v3",
            "status": "DIARIZED_ROLE_MAPPED",
            "role_mapping_supported": True,
            "speaker_count_evidence": evidence,
        },
    )
    _, report = run_speaker_structure(
        tmp_path / "lesson.mp4", _asr_rows(), tmp_path, min_label_coverage=0.8
    )
    assert report["speaker_count_evidence"] == evidence


@pytest.mark.parametrize(
    ("rows", "status", "reason"),
    [
        (_diarized("same", "same"), "DIARIZED_UNMAPPED", "SPEAKER_COLLAPSE_RISK"),
        (_diarized(), "unexpected internal status", "UNSUPPORTED_STATUS"),
        (_diarized()[:1], "DIARIZED_UNMAPPED", "SEGMENT_COUNT_MISMATCH"),
    ],
)
def test_failed_gate_removes_all_speaker_annotations(monkeypatch, tmp_path: Path, rows, status, reason):
    _patch_diarizer(monkeypatch, rows, {"revision": "test-v1", "status": status})
    output, report = run_speaker_structure(tmp_path / "lesson.mp4", _asr_rows(), tmp_path)

    assert all("speaker" not in row for row in output)
    assert report["status"] == "UNAVAILABLE"
    assert report["quality_gate"] == "INCONCLUSIVE"
    assert report["reason"] == reason
    assert report["speaker_count"] == 0
    assert report["segments_labeled"] == 0


def test_invalid_confidence_fails_closed(monkeypatch, tmp_path: Path):
    rows = _diarized()
    rows[0]["speaker_confidence"] = float("nan")
    _patch_diarizer(monkeypatch, rows, {"revision": "test-v1", "status": "DIARIZED_UNMAPPED"})
    output, report = run_speaker_structure(tmp_path / "lesson.mp4", _asr_rows(), tmp_path)
    assert all("speaker" not in row for row in output)
    assert report["reason"] == "INVALID_SPEAKER_ANNOTATION"


@pytest.mark.parametrize(
    ("raw_report", "expected_reason"),
    [
        (
            {
                "revision": "bridge-local-diarization-v1",
                "status": "UNAVAILABLE",
                "reason": "RuntimeError",
                "diagnostic_code": "ACOUSTIC_CLUSTERS_NOT_SEPARATED",
                "detail": "must not be exported",
            },
            "ACOUSTIC_CLUSTERS_NOT_SEPARATED",
        ),
        (
            {
                "revision": "bridge-local-diarization-v1",
                "status": "UNAVAILABLE",
                "reason": "RuntimeError",
                "detail": "unbounded internal detail",
            },
            "DIARIZATION_ENGINE_FAILED",
        ),
        (
            {
                "revision": "bridge-local-diarization-v1",
                "status": "UNAVAILABLE",
                "reason": "ModuleNotFoundError",
            },
            "OPTIONAL_RUNTIME_UNAVAILABLE",
        ),
    ],
)
def test_unavailable_producer_exposes_only_bounded_reason(
    monkeypatch, tmp_path: Path, raw_report: dict, expected_reason: str
):
    _patch_diarizer(monkeypatch, _asr_rows(), raw_report)
    output, report = run_speaker_structure(
        tmp_path / "lesson.mp4",
        _asr_rows(),
        tmp_path,
        min_label_coverage=MIN_TEST_LABEL_COVERAGE,
    )

    assert all("speaker" not in row for row in output)
    assert report["status"] == "UNAVAILABLE"
    assert report["quality_gate"] == "INCONCLUSIVE"
    assert report["reason"] == expected_reason
    assert "detail" not in report


def test_v3_collapse_status_fails_closed_with_specific_reason(monkeypatch, tmp_path: Path):
    _patch_diarizer(
        monkeypatch,
        _diarized(),
        {
            "revision": "bridge-sherpa-onnx-diarization-v3",
            "status": "DIARIZED_COLLAPSE_RISK",
        },
    )
    output, report = run_speaker_structure(
        tmp_path / "lesson.mp4",
        _asr_rows(),
        tmp_path,
        min_label_coverage=MIN_TEST_LABEL_COVERAGE,
    )

    assert all("speaker" not in row for row in output)
    assert report["status"] == "UNAVAILABLE"
    assert report["reason"] == "SPEAKER_COLLAPSE_RISK"


def test_test_profile_selects_v3_without_changing_stable_backend(monkeypatch, tmp_path: Path):
    import bridge_speaker_diarization
    import bridge_speaker_diarization_v3

    calls: list[str] = []

    def stable(*_args, **_kwargs):
        calls.append("v1")
        return _asr_rows(), {
            "revision": "bridge-local-diarization-v1",
            "status": "UNAVAILABLE",
            "diagnostic_code": "INSUFFICIENT_VOICED_SEGMENTS",
        }

    def test_backend(*_args, **_kwargs):
        calls.append("v3")
        return _asr_rows(), {
            "revision": "bridge-sherpa-onnx-diarization-v3",
            "status": "UNAVAILABLE",
            "diagnostic_code": "ACOUSTIC_CLUSTERS_NOT_SEPARATED",
        }

    monkeypatch.setattr(bridge_speaker_diarization, "diarize_transcript", stable)
    monkeypatch.setattr(bridge_speaker_diarization_v3, "diarize_transcript", test_backend)

    _, stable_report = run_speaker_structure(
        tmp_path / "lesson.mp4", _asr_rows(), tmp_path
    )
    _, test_report = run_speaker_structure(
        tmp_path / "lesson.mp4",
        _asr_rows(),
        tmp_path,
        min_label_coverage=MIN_TEST_LABEL_COVERAGE,
    )

    assert calls == ["v1", "v3"]
    assert stable_report["revision"] == "bridge-local-diarization-v1"
    assert test_report["revision"] == "bridge-sherpa-onnx-diarization-v3"


def test_large_lesson_coverage_fixture(monkeypatch, tmp_path: Path):
    """Exercises the reported 980/934 coverage shape without inventing field labels."""
    transcript = [
        {"start": float(index), "end": float(index) + 0.8, "text": f"segment {index}", "chunk": 0, "unreliable": False}
        for index in range(980)
    ]
    raw = [dict(row) for row in transcript]
    for index in range(400):
        raw[index].update(
            {
                "speaker": "source-cluster-0",
                "speaker_confidence": 0.9794,
                "speaker_role_candidate": "student",
                "speaker_role_confidence": 0.95,
            }
        )
    for index in range(400, 934):
        raw[index].update(
            {
                "speaker": "source-cluster-1",
                "speaker_confidence": 0.9794,
                "speaker_role_candidate": "teacher",
                "speaker_role_confidence": 0.95,
            }
        )
    _patch_diarizer(
        monkeypatch,
        raw,
        {
            "revision": "bridge-sherpa-onnx-diarization-v3",
            "status": "DIARIZED_ROLE_MAPPED",
            "role_mapping_supported": True,
        },
    )
    output, report = run_speaker_structure(
        tmp_path / "diana2.mp4",
        transcript,
        tmp_path,
        min_label_coverage=MIN_TEST_LABEL_COVERAGE,
    )

    assert report["schema"] == TEST_SCHEMA
    assert report["quality_gate"] == "PASS"
    assert report["speaker_clusters"] == {"SPEAKER_A": 400, "SPEAKER_B": 534}
    assert report["segments_labeled"] == 934
    assert round(report["label_coverage"], 4) == 0.9531
    assert round(report["speech_duration_coverage"], 4) == 0.9531
    assert report["minimum_label_coverage"] == 0.80
    assert sum("speaker" not in row for row in output) == 46


def test_diana2_public_field_receipt_is_not_a_segment_fixture():
    """Public aggregates use different denominators; raw map is required for replay."""
    transcript_segments = 980
    speaker_labeled_segments = 934
    cluster_counts = (308, 646)
    accepted_embedding_segments = 932
    embedding_segments = 954

    assert sum(cluster_counts) == embedding_segments
    assert sum(cluster_counts) != speaker_labeled_segments
    assert accepted_embedding_segments != speaker_labeled_segments
    assert speaker_labeled_segments / transcript_segments == pytest.approx(0.9531, abs=0.0001)


def test_test_profile_rejects_two_labels_with_low_lesson_coverage(monkeypatch, tmp_path: Path):
    transcript = [
        {"start": float(index), "end": float(index) + 0.8, "text": f"segment {index}", "chunk": 0, "unreliable": False}
        for index in range(10)
    ]
    raw = [dict(row) for row in transcript]
    raw[0].update(
        {
            "speaker": "cluster-a",
            "speaker_confidence": 0.95,
            "speaker_role_candidate": "unknown",
            "speaker_role_confidence": 0.0,
        }
    )
    raw[1].update(
        {
            "speaker": "cluster-b",
            "speaker_confidence": 0.95,
            "speaker_role_candidate": "unknown",
            "speaker_role_confidence": 0.0,
        }
    )
    _patch_diarizer(monkeypatch, raw, {"revision": "test-v2", "status": "DIARIZED_UNMAPPED"})

    output, report = run_speaker_structure(
        tmp_path / "lesson.mp4",
        transcript,
        tmp_path,
        min_label_coverage=MIN_TEST_LABEL_COVERAGE,
    )

    assert all("speaker" not in row for row in output)
    assert report["schema"] == TEST_SCHEMA
    assert report["quality_gate"] == "INCONCLUSIVE"
    assert report["reason"] == "INSUFFICIENT_LABEL_COVERAGE"
    assert report["label_coverage"] == 0.0
    assert report["speech_duration_coverage"] == 0.0
    assert report["minimum_label_coverage"] == 0.80


def test_test_profile_rejects_short_segment_coverage_with_long_unlabeled_speech(monkeypatch, tmp_path: Path):
    transcript = [
        {"start": float(index), "end": float(index) + 0.5, "text": f"short {index}", "chunk": 0, "unreliable": False}
        for index in range(8)
    ]
    transcript.extend(
        [
            {"start": 8.0, "end": 18.0, "text": "long unlabeled one", "chunk": 0, "unreliable": False},
            {"start": 18.0, "end": 28.0, "text": "long unlabeled two", "chunk": 0, "unreliable": False},
        ]
    )
    raw = [dict(row) for row in transcript]
    for index in range(8):
        raw[index].update(
            {
                "speaker": "cluster-a" if index % 2 == 0 else "cluster-b",
                "speaker_confidence": 0.95,
                "speaker_role_candidate": "unknown",
                "speaker_role_confidence": 0.0,
            }
        )
    _patch_diarizer(monkeypatch, raw, {"revision": "test-v2", "status": "DIARIZED_UNMAPPED"})

    output, report = run_speaker_structure(
        tmp_path / "lesson.mp4",
        transcript,
        tmp_path,
        min_label_coverage=MIN_TEST_LABEL_COVERAGE,
    )

    assert all("speaker" not in row for row in output)
    assert report["reason"] == "INSUFFICIENT_LABEL_COVERAGE"
    assert report["quality_gate"] == "INCONCLUSIVE"


def _bundle(tmp_path: Path):
    return runpy.run_path(
        str(Path(__file__).with_name("test_universal_video_result_conformance.py"))
    )["_bundle"](tmp_path)


def _verify(job_dir: Path):
    return verify_result(
        job_dir,
        expected_job_id="exact-video-job",
        expected_profile="bridge_lesson",
        expected_job_hash="c" * 64,
        expected_source_file_id="1AbCdEfGhIjKlMnOpQrStUvWxYz",
    )


def _install_two_speaker_evidence(job_dir: Path, manifest: dict, *, first_label: str = "SPEAKER_A"):
    first = json.loads((job_dir / "transcript.jsonl").read_text(encoding="utf-8"))
    first.update(
        {
            "speaker": first_label,
            "speaker_cluster": first_label,
            "speaker_confidence": 0.72,
            "speaker_role_candidate": "unknown",
            "speaker_role_confidence": 0.0,
            "speaker_assignment_revision": "bridge-local-diarization-v1",
        }
    )
    second = {
        "start": 2.0,
        "end": 3.5,
        "text": "Closing pass",
        "chunk": 0,
        "unreliable": False,
        "speaker": "SPEAKER_B",
        "speaker_cluster": "SPEAKER_B",
        "speaker_confidence": 0.74,
        "speaker_role_candidate": "unknown",
        "speaker_role_confidence": 0.0,
        "speaker_assignment_revision": "bridge-local-diarization-v1",
    }
    (job_dir / "transcript.jsonl").write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8"
    )
    (job_dir / "transcript.txt").write_text(
        "[0.0-1.5] Opening bid\n[2.0-3.5] Closing pass", encoding="utf-8"
    )
    speaker = json.loads((job_dir / "speaker_diarization.json").read_text(encoding="utf-8"))
    speaker.update(
        {
            "status": "DIARIZED_UNMAPPED",
            "quality_gate": "PASS",
            "reason": "NONE",
            "segments_total": 2,
            "segments_labeled": 2,
            "speaker_count": 2,
            "speaker_labels": [first_label, "SPEAKER_B"],
            "speaker_clusters": {first_label: 1, "SPEAKER_B": 1},
        }
    )
    (job_dir / "speaker_diarization.json").write_text(json.dumps(speaker), encoding="utf-8")
    manifest["speaker_structure"].update(
        {"status": "DIARIZED_UNMAPPED", "speaker_count": 2, "segments_labeled": 2}
    )
    manifest["transcript"].update({"segments": 2, "words": 4})
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_conformance_accepts_bounded_anonymous_speaker_fields(tmp_path: Path):
    job_dir, manifest = _bundle(tmp_path)
    _install_two_speaker_evidence(job_dir, manifest)
    assert _verify(job_dir)["state"] == "PASS"


def test_producer_output_passes_independent_conformance_checker(monkeypatch, tmp_path: Path):
    _patch_diarizer(
        monkeypatch,
        _diarized("private-name-a", "private-name-b"),
        {
            "revision": "diarization-test-v1",
            "status": "DIARIZED_ROLE_MAPPED",
            "role_mapping_supported": True,
        },
    )
    rows, speaker = run_speaker_structure(tmp_path / "lesson.mp4", _asr_rows(), tmp_path)
    job_dir, manifest = _bundle(tmp_path / "bundle")
    (job_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (job_dir / "transcript.txt").write_text(
        "[0.0-1.0] original one\n[1.0-2.0] original two", encoding="utf-8"
    )
    (job_dir / "speaker_diarization.json").write_text(json.dumps(speaker), encoding="utf-8")
    manifest["transcript"].update({"segments": 2, "words": 4})
    manifest["speaker_structure"].update(
        {
            "status": speaker["status"],
            "speaker_count": speaker["speaker_count"],
            "segments_labeled": speaker["segments_labeled"],
        }
    )
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    verified = _verify(job_dir)
    assert verified["state"] == "PASS"
    assert "private-name" not in (job_dir / "transcript.jsonl").read_text(encoding="utf-8")


def test_conformance_rejects_real_person_label(tmp_path: Path):
    job_dir, manifest = _bundle(tmp_path)
    _install_two_speaker_evidence(job_dir, manifest, first_label="Diana Veksler")
    with pytest.raises(ResultConformanceError, match="anonymous speaker"):
        _verify(job_dir)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda report: report.update({"unexpected": True}), "report shape"),
        (
            lambda report: report["privacy"].update({"real_person_identity_claimed": True}),
            "privacy boundary",
        ),
        (
            lambda report: report.update(
                {
                    "speaker_count": 1,
                    "segments_labeled": 1,
                    "speaker_labels": ["SPEAKER_A"],
                    "speaker_clusters": {"SPEAKER_A": 1},
                }
            ),
            "unavailable speaker report retains labels",
        ),
    ],
)
def test_conformance_rejects_tampered_speaker_report(tmp_path: Path, mutation, message: str):
    job_dir, _manifest = _bundle(tmp_path)
    path = job_dir / "speaker_diarization.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    mutation(report)
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ResultConformanceError, match=message):
        _verify(job_dir)
