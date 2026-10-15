from database.video_candidate_persistence import _versioned_stable_key
from database.video_candidate_persistence import validate_replay, persist_quality_candidates
from bridge_contracts.video_canon_replay import digest
import pytest


def test_video_canon_staging_identity_is_content_addressed_and_idempotent():
    first_hash = "a" * 64
    second_hash = "b" * 64
    first = _versioned_stable_key("video_school_canon_candidate", "assertion-7", first_hash)
    second = _versioned_stable_key("video_school_canon_candidate", "assertion-7", second_hash)
    assert first != second
    assert first.endswith(first_hash)
    assert _versioned_stable_key("video_school_canon_candidate", first, first_hash) == first


def test_other_staging_identities_remain_unchanged():
    assert _versioned_stable_key("EXPLANATION_CANDIDATE", "why:7", "c" * 64) == "why:7"


def test_unbound_generated_result_cannot_request_database_connection():
    assert persist_quality_candidates('never-use-this-dsn', {'persisted_result': {'persisted': True}}) == {
        'status': 'BLOCKED', 'gaps': ['EXACT_PERSISTED_RESULT_REQUIRED'], 'authoritative_tables_modified': False}


@pytest.mark.parametrize('change', ['duplicate','hash','status','promotion'])
def test_full_receipt_contract_rejects_unsafe_or_ambiguous_input(change):
    member={'payload': {'example':'unit-only'}, 'payload_hash': digest({'example':'unit-only'})}
    replay={'schema':'video-canon-staging-replay-v1','status':'BLOCKED','candidates':[member],
            'authoritative_write_performed':False,'promotion_commands':[]}
    if change=='duplicate': replay['candidates'].append(member)
    if change=='status': replay['status']={'private':'must not project'}
    if change=='promotion': replay['promotion_commands']=[{'write':True}]
    replay['replay_sha256']=digest(replay)
    if change=='hash': replay['replay_sha256']='0'*64
    with pytest.raises(ValueError): validate_replay(replay)
