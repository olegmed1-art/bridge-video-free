"""Synthetic producer-corruption regressions; no media, model or worker used."""
from copy import deepcopy
from pathlib import Path

import pytest

from universal_video.speaker_structure import run_speaker_structure


def fixture():
    transcript = [
        {'segment_id': f's{i}', 'start': float(i), 'end': float(i + 1),
         'text': f'synthetic source text {i}', 'chunk': 0}
        for i in range(4)
    ]
    producer = [dict(row, speaker='S' if i < 2 else 'T',
                     speaker_confidence=.99,
                     speaker_role_candidate='student' if i < 2 else 'teacher',
                     speaker_role_confidence=.99)
                for i, row in enumerate(transcript)]
    report = {'revision': 'synthetic-v1', 'status': 'DIARIZED_ROLE_MAPPED',
              'role_mapping_supported': True,
              'speaker_count_evidence': {'mode': 'OPEN_SET', 'selected_count': 2,
                   'collapse_check': 'PASS', 'fragmentation_check': 'PASS', 'mixing_check': 'PASS'}}
    return transcript, producer, report


def patch_producer(monkeypatch, producer, report):
    import bridge_speaker_diarization
    import bridge_speaker_diarization_v3
    def stub(*_args, **_kwargs):
        return producer, report
    monkeypatch.setattr(bridge_speaker_diarization, 'diarize_transcript', stub)
    monkeypatch.setattr(bridge_speaker_diarization_v3, 'diarize_transcript', stub)


@pytest.mark.parametrize('minimum', [None, .8])
@pytest.mark.parametrize('corruption', ['reordered', 'duplicated', 'shifted_start', 'shifted_end',
                                       'missing_start', 'boolean_start', 'nan_end', 'infinite_end',
                                       'changed_id', 'missing_id'])
def test_producer_annotations_require_exact_source_alignment(monkeypatch, tmp_path: Path, minimum, corruption):
    transcript, producer, report = fixture()
    original = deepcopy(transcript)
    if corruption == 'reordered': producer.reverse()
    elif corruption == 'duplicated': producer[1] = deepcopy(producer[0])
    elif corruption == 'shifted_start': producer[0]['start'] = .25
    elif corruption == 'shifted_end': producer[0]['end'] = 1.25
    elif corruption == 'missing_start': producer[0].pop('start')
    elif corruption == 'boolean_start': producer[0]['start'] = False
    elif corruption == 'nan_end': producer[0]['end'] = float('nan')
    elif corruption == 'infinite_end': producer[0]['end'] = float('inf')
    elif corruption == 'changed_id': producer[0]['segment_id'] = 'other'
    elif corruption == 'missing_id': producer[0].pop('segment_id')
    patch_producer(monkeypatch, producer, report)
    output, proof = run_speaker_structure(tmp_path / 'never-opened.mp4', transcript, tmp_path,
                                         min_label_coverage=minimum)
    assert transcript == original
    assert output == original
    assert proof['status'] == 'UNAVAILABLE'
    assert proof['quality_gate'] == 'INCONCLUSIVE'
    assert proof['reason'] == 'SEGMENT_SOURCE_MISMATCH'
    assert proof['role_mapping_supported'] is False
    assert proof['teacher_student_attribution'] == 'UNAVAILABLE'
    assert proof.get('role_mapping_proof_status') != 'PASS'


@pytest.mark.parametrize('minimum', [None, .8])
@pytest.mark.parametrize('with_ids', [False, True])
def test_aligned_annotations_preserve_source_and_keep_valid_roles(monkeypatch, tmp_path, minimum, with_ids):
    transcript, producer, report = fixture()
    if not with_ids:
        for row in transcript + producer: row.pop('segment_id')
    for row in producer:
        row['start'], row['end'] = int(row['start']), int(row['end'])
        row['text'] = 'producer text must not replace source'
    original = deepcopy(transcript)
    patch_producer(monkeypatch, producer, report)
    output, proof = run_speaker_structure(tmp_path / 'never-opened.mp4', transcript, tmp_path,
                                         min_label_coverage=minimum)
    assert transcript == original
    assert [row['text'] for row in output] == [row['text'] for row in original]
    assert [row['speaker_role_candidate'] for row in output] == ['student', 'student', 'teacher', 'teacher']
    assert proof['quality_gate'] == 'PASS'
    if minimum is not None: assert proof['role_mapping_proof_status'] == 'PASS'


@pytest.mark.parametrize('minimum', [None, .8])
@pytest.mark.parametrize('identity_mode', ['no_ids', 'mixed_ids', 'duplicate_ids', 'distinct_ids'])
def test_duplicate_intervals_require_unambiguous_source_identity(monkeypatch, tmp_path, minimum, identity_mode):
    transcript, producer, report = fixture()
    for i in range(4):
        for row in (transcript[i], producer[i]):
            row['start'], row['end'] = float(i // 2), float(i // 2 + 1)
            if identity_mode == 'no_ids' or (identity_mode == 'mixed_ids' and i % 2 == 0): row.pop('segment_id')
            elif identity_mode == 'duplicate_ids': row['segment_id'] = f'pair{i // 2}'
        producer[i]['speaker'] = 'S' if i % 2 == 0 else 'T'
        producer[i]['speaker_role_candidate'] = 'student' if i % 2 == 0 else 'teacher'
    if identity_mode != 'distinct_ids': producer = [producer[i] for i in (1, 0, 3, 2)]
    patch_producer(monkeypatch, producer, report)
    output, proof = run_speaker_structure(tmp_path / 'never-opened.mp4', transcript, tmp_path,
                                         min_label_coverage=minimum)
    if identity_mode == 'distinct_ids':
        assert proof['quality_gate'] == 'PASS'
        assert [row['speaker_role_candidate'] for row in output] == ['student', 'teacher', 'student', 'teacher']
    else:
        assert output == transcript
        assert proof['quality_gate'] == 'INCONCLUSIVE'
        assert proof['reason'] == 'SEGMENT_SOURCE_MISMATCH'
        assert proof.get('role_mapping_proof_status') != 'PASS'


@pytest.mark.parametrize('bad_id', [None, '', [], False])
def test_supplied_source_identity_must_be_nonempty_string(monkeypatch, tmp_path, bad_id):
    transcript, producer, report = fixture()
    transcript[0]['segment_id'] = producer[0]['segment_id'] = bad_id
    patch_producer(monkeypatch, producer, report)
    output, proof = run_speaker_structure(tmp_path / 'never-opened.mp4', transcript, tmp_path,
                                         min_label_coverage=.8)
    assert output == transcript
    assert proof['quality_gate'] == 'INCONCLUSIVE'
    assert proof['reason'] == 'SEGMENT_SOURCE_MISMATCH'
