from pathlib import Path


def test_standard_transcription_routes_through_r29_overlay_before_downstream_analysis():
    workflow = Path('.github/workflows/bridge-video-3.1-free.yml').read_text(encoding='utf-8')
    route = Path('route_drive_job_outputs.py').read_text(encoding='utf-8')
    stage = Path('bridge_speaker_identity_postprocess.py').read_text(encoding='utf-8')
    probe = Path('r29_identity_overlay_probe.py').read_text(encoding='utf-8')

    assert workflow.index('Process one opaque Drive job') < workflow.index('Route derived outputs away from master media')
    assert workflow.index('Route derived outputs away from master media') < workflow.index('Build quality-first Diana longitudinal candidates')
    assert 'bridge_speaker_identity_postprocess' in route
    assert '_run_identity_overlay(token)' in route
    assert 'SPEAKER_MAPPING_ANONYMOUS_ONLY' in stage
    assert '_autodiscover_private_ids' in stage
    assert 'IDENTITY_EVIDENCE_NOT_FOUND' in stage
    assert 'r29.main()' in stage
    assert 'personSpecificWritesAllowed' in stage
    assert 'rawAsrMutated' in stage
    assert 'heavyVideoReprocessed' in stage
    assert 'paidApi' in stage and 'paidCloud' in stage
    assert 'realNamesLogged' in probe
    assert 'speakerEmbeddingsPersisted' in probe
    assert 'temporaryAudioAnchorsPersisted' in probe


def test_identity_overlay_never_uses_semantics_or_filename_as_named_identity_fallback():
    stage = Path('bridge_speaker_identity_postprocess.py').read_text(encoding='utf-8')
    mapping = Path('bridge_speaker_mapping_r29.py').read_text(encoding='utf-8')
    tests = Path('tests/test_speaker_mapping_r29.py').read_text(encoding='utf-8')

    assert 'semantic_role_never_creates_named_identity' in tests
    assert 'visual_and_acoustic_anchors_confirm_identity' in tests
    assert 'identity_conflict_fails_closed' in tests
    assert 'probable_identity_cannot_write_profile' in tests
    assert 'overlap_is_not_forced_to_one_person' in tests
    assert 'participant_status' in mapping
    assert 'UNKNOWN_PARTICIPANT' in mapping
    assert 'PERSON_CONFIRMED' in mapping
    # Integration discovers only private evidence documents whose content matches
    # the exact job/source. It has no filename/role/name based participant fallback.
    assert 'source_drive_id' in stage
    assert 'job_id' in stage
    assert 'role_if_confirmed' not in stage
    assert 'display_name' not in stage
