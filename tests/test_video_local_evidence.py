"""Local-byte contract tests. Synthetic positives are not real media proof."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import pytest

from bridge_contracts.video_canon_replay import digest, replay_result_bundle
from bridge_contracts.video_local_evidence import LocalEvidenceReader, reader_from_environment
from bridge_contracts.video_replay_input import build_replay_input, sha256
from tests.test_video_canon_replay import synthetic_bundle
from tools.replay_video_canon import stage_local


def raw(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode() + b'\n'


def fixture(root):
    """A deliberately synthetic store plus an out-of-band resolver test double."""
    bundle = synthetic_bundle()
    observation = bundle['learning_observation']
    source, frame = observation['source'], observation['frame_evidence'][0]
    source['source_sha256'] = sha256(b'synthetic media')
    frame['frame_sha256'] = sha256(b'synthetic frame')
    parent = {'source': {'driveId': source['video_file_id'], 'sha256': source['source_sha256']},
        'transcript': [{'segment_id': 'synthetic#1', 'start': 0, 'end': 2,
            'text': bundle['teacher_assertions'][0]['statement'], 'speaker_role_candidate': 'teacher'}],
        'screenshots': [{'evidence_id': 'f1', 'time': 1, 'sha256': frame['frame_sha256']}]}
    (root/'parent.json').write_bytes(raw(parent))
    (root/'media.bin').write_bytes(b'synthetic media')
    (root/'frame.bin').write_bytes(b'synthetic frame')
    parent_sha = sha256(raw(parent))
    bundle['replay_provenance']['parent_artifact_sha256'] = parent_sha
    index = {'schema': 'video-local-evidence-index-v1',
        'parent': {'path': 'parent.json', 'raw_sha256': parent_sha, 'canonical_sha256': digest(parent)},
        'source': {'path': 'media.bin', 'sha256': source['source_sha256'], 'video_file_id': source['video_file_id']},
        'frames': [{'path': 'frame.bin', 'sha256': frame['frame_sha256'], 'evidence_id': 'f1'}]}
    manifest = {'schema': 'video-canon-replay-parent-evidence-v1', 'evidence_class': 'REAL',
        'parent_artifact_sha256': parent_sha, 'parent_hash_domain': 'RAW_BYTES_SHA256',
        'source': deepcopy(source), 'transcripts': [{**observation['transcript_evidence'][0],
            'speaker_role': 'teacher', 'frame_evidence': deepcopy(frame)}], 'source_authorizations': []}
    path, pin = write_index(root, index)
    return bundle, parent, index, manifest, path, pin


def write_index(root, index):
    path = root/'index.json'
    path.write_bytes(raw(index))
    return path, sha256(raw(index))


def codes(result):
    return {g['code'] for g in result['gaps']}


def test_file_labels_and_pins_never_establish_authority(tmp_path):
    bundle, _, _, manifest, path, pin = fixture(tmp_path)
    bundle['trusted_manifest'] = manifest
    result = LocalEvidenceReader(path, pin).replay(bundle)
    assert result['status'] == 'BLOCKED'
    assert not result['video_canon_inputs'] and not result['promotion_commands']
    assert 'INDEPENDENT_SOURCE_IDENTITY_ROLE_POLICY_UNAVAILABLE' in codes(result)
    assert 'INDEPENDENT_PARENT_EVIDENCE_UNAVAILABLE' in codes(result)
    assert result['candidates'][-1]['payload']['byte_integrity_status'] == 'VERIFIED_AGAINST_LOCAL_PINS'


def test_existing_trusted_callable_still_requires_bytes_and_preserves_staging(tmp_path):
    bundle, _, _, manifest, path, pin = fixture(tmp_path)
    before = deepcopy(bundle)
    reader = LocalEvidenceReader(path, pin, trusted_resolver=lambda _: manifest)
    result = reader.replay(bundle)
    assert result['status'] == 'STAGING_READY'  # simulated external store only
    assert not result['promotion_commands'] and result['authoritative_write_performed'] is False
    assert bundle == before
    assert result == reader.replay(bundle)
    with sqlite3.connect(':memory:') as connection:
        assert stage_local(connection, result)['inserted'] == len(result['candidates'])
        assert stage_local(connection, reader.replay(bundle))['inserted'] == 0


@pytest.mark.parametrize('target,code', [('parent.json','PARENT_RAW_DIGEST_MISMATCH'),
    ('frame.bin','FRAME_DIGEST_MISMATCH'), ('media.bin','SOURCE_MEDIA_DIGEST_MISMATCH'),
    ('index.json','INDEX_DIGEST_MISMATCH')])
def test_tampering_after_success_is_not_cached(tmp_path, target, code):
    bundle, _, _, manifest, path, pin = fixture(tmp_path)
    reader = LocalEvidenceReader(path, pin, trusted_resolver=lambda _: manifest)
    assert reader.replay(bundle)['status'] == 'STAGING_READY'
    with (tmp_path/target).open('ab') as handle:
        handle.write(b' ')
    result = reader.replay(bundle)
    assert result['status'] == 'BLOCKED' and code in codes(result)


@pytest.mark.parametrize('mutation,code', [
    ('canonical', 'PARENT_CANONICAL_DIGEST_MISMATCH'), ('source_id','PARENT_SOURCE_ID_MISMATCH'),
    ('missing_frame','FRAME_UNREADABLE'), ('no_frames','FRAME_BYTES_MISSING'),
    ('missing_media','SOURCE_MEDIA_BYTES_MISSING'), ('frame_id','PARENT_FRAME_BINDING_MISMATCH')])
def test_missing_or_wrong_independent_bindings_block(tmp_path, mutation, code):
    bundle, _, index, manifest, _, _ = fixture(tmp_path)
    if mutation == 'canonical': index['parent']['canonical_sha256'] = index['parent']['raw_sha256']
    if mutation == 'source_id': index['source']['video_file_id'] = 'wrong'
    if mutation == 'missing_frame': index['frames'][0]['path'] = 'missing.jpg'
    if mutation == 'no_frames': index['frames'] = []
    if mutation == 'missing_media': index['source']['path'] = None
    if mutation == 'frame_id': index['frames'][0]['evidence_id'] = 'different'
    path, pin = write_index(tmp_path,index)
    result = LocalEvidenceReader(path, pin, trusted_resolver=lambda _: manifest).replay(bundle)
    assert result['status'] == 'BLOCKED' and code in codes(result)


@pytest.mark.parametrize('field,value', [('speaker_role','student'), ('speaker_identity_status','UNKNOWN'),
    ('text_sha256','d'*64), ('start',.5), ('locator','wrong')])
def test_external_roles_identity_text_and_intervals_are_not_inferred(tmp_path, field, value):
    bundle, _, _, manifest, path, pin = fixture(tmp_path)
    manifest['transcripts'][0][field] = value
    assert LocalEvidenceReader(path,pin,trusted_resolver=lambda _:manifest).replay(bundle)['status'] == 'BLOCKED'


def test_authorization_claim_requires_external_policy_store(tmp_path):
    bundle, _, _, manifest, path, pin = fixture(tmp_path)
    source = bundle['learning_observation']['source']
    bundle['teacher_assertions'][0]['source_authorization'] = {
        'status':'APPROVED','decision_ref':'synthetic-decision','policy_version':'synthetic-policy',
        'authorized_source_sha256':source['source_sha256'],'authorized_video_file_id':source['video_file_id'],
        'authorized_teacher_ids':['synthetic-teacher'],'approved_semantic_scopes':['synthetic/scope'],
        'authorization_evidence_sha256':'e'*64}
    result = LocalEvidenceReader(path,pin,trusted_resolver=lambda _:manifest).replay(bundle)
    assert 'SOURCE_AUTHORIZATION_UNPROVEN' in codes(result)


@pytest.mark.parametrize('path_value', ['../outside.json','C:/outside.json','file.bin:stream','/absolute','a\\b'])
def test_inventory_cannot_escape_local_root(tmp_path,path_value):
    _, _, index, _, _, _ = fixture(tmp_path)
    index['parent']['path'] = path_value
    path,pin = write_index(tmp_path,index)
    with pytest.raises(ValueError,match='PATH_'):
        LocalEvidenceReader(path,pin)


def test_index_pins_unknown_fields_and_output_aliases(tmp_path):
    _, _, index, _, path, pin = fixture(tmp_path)
    with pytest.raises(ValueError,match='DIGEST_MISMATCH'):
        LocalEvidenceReader(path,'0'*64)
    reader=LocalEvidenceReader(path,pin)
    with pytest.raises(ValueError,match='OUTPUT_COLLISION'):
        reader.protect_outputs([tmp_path/'frame.bin'])
    index['trusted_manifest'] = {'evidence_class':'REAL'}
    path,pin=write_index(tmp_path,index)
    with pytest.raises(ValueError,match='SCHEMA_INVALID'):
        LocalEvidenceReader(path,pin)


def test_cli_opt_in_and_repeat_do_not_change_source_files(tmp_path):
    bundle, _, _, _, index,pin=fixture(tmp_path)
    path=tmp_path/'bundle.json'
    path.write_bytes(raw(bundle))
    before={p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    cmd=[sys.executable,'-m','tools.replay_video_canon',str(path),
        '--evidence-index',str(index),'--evidence-index-sha256',pin,
        '--local-staging-db',str(tmp_path/'stage.sqlite')]
    for name in ('out1.json','out2.json'):
        subprocess.run([*cmd,'--output',str(tmp_path/name)],check=True,capture_output=True)
    assert (tmp_path/'out1.json').read_bytes()==(tmp_path/'out2.json').read_bytes()
    assert all((tmp_path/k).read_bytes()==v for k,v in before.items())
    with sqlite3.connect(tmp_path/'stage.sqlite') as db:
        assert db.execute('select count(*) from replay_staging').fetchone()[0]==2
    bad=subprocess.run([*cmd,'--output',str(tmp_path/'frame.bin')],capture_output=True)
    assert bad.returncode!=0


def test_opt_in_configuration_and_loaded_master_are_pinned(tmp_path):
    _,parent,_,_,path,pin=fixture(tmp_path)
    assert reader_from_environment({}) is None
    with pytest.raises(ValueError,match='PATH_AND_PIN_REQUIRED'):
        reader_from_environment({'BRIDGE_VIDEO_REPLAY_EVIDENCE_INDEX_PATH':str(path)})
    reader=reader_from_environment({'BRIDGE_VIDEO_REPLAY_EVIDENCE_INDEX_PATH':str(path),
        'BRIDGE_VIDEO_REPLAY_EVIDENCE_INDEX_SHA256':pin})
    reader.bind_master(raw(parent))
    with pytest.raises(ValueError,match='LOADED_MASTER_MISMATCH'):
        reader.bind_master(raw(parent)+b' ')


@pytest.mark.parametrize('suffix',['-journal','-wal','-shm'])
def test_sqlite_sidecars_cannot_overwrite_evidence(tmp_path,suffix):
    bundle,_,index,_,_,_=fixture(tmp_path)
    evidence=tmp_path/('stage.sqlite'+suffix)
    evidence.write_bytes(b'synthetic frame')
    index['frames'][0]['path']=evidence.name
    path,pin=write_index(tmp_path,index)
    (tmp_path/'bundle.json').write_bytes(raw(bundle))
    result=subprocess.run([sys.executable,'-m','tools.replay_video_canon',str(tmp_path/'bundle.json'),
        '--evidence-index',str(path),'--evidence-index-sha256',pin,
        '--local-staging-db',str(tmp_path/'stage.sqlite'),'--output',str(tmp_path/'out.json')],capture_output=True)
    assert result.returncode!=0 and b'LOCAL_EVIDENCE_OUTPUT_COLLISION' in result.stderr
    assert evidence.read_bytes()==b'synthetic frame'
    assert not (tmp_path/'stage.sqlite').exists() and not (tmp_path/'out.json').exists()


def test_sqlite_symlink_uses_checked_canonical_sidecar_name(tmp_path):
    bundle,_,index,_,_,_=fixture(tmp_path)
    db=tmp_path/'target.sqlite'
    with sqlite3.connect(db): pass
    alias=tmp_path/'alias.sqlite'
    try:
        alias.symlink_to(db)
    except OSError:
        pytest.skip('platform does not permit local symlink creation')
    evidence=tmp_path/'target.sqlite-journal'
    evidence.write_bytes(b'synthetic frame')
    index['frames'][0]['path']=evidence.name
    path,pin=write_index(tmp_path,index)
    (tmp_path/'bundle.json').write_bytes(raw(bundle))
    result=subprocess.run([sys.executable,'-m','tools.replay_video_canon',str(tmp_path/'bundle.json'),
        '--evidence-index',str(path),'--evidence-index-sha256',pin,
        '--local-staging-db',str(alias),'--output',str(tmp_path/'out.json')],capture_output=True)
    assert result.returncode!=0 and b'LOCAL_EVIDENCE_OUTPUT_COLLISION' in result.stderr
    assert evidence.read_bytes()==b'synthetic frame'


def test_diana_opt_in_cannot_reach_auto_pipeline(tmp_path):
    from diana_longitudinal_quality_v4_2 import build_quality_layer
    bundle,_,_,_,path,pin=fixture(tmp_path)
    bundle['video_canon_learning_candidate']=bundle.pop('learning_observation')
    bundle['video_canon_assertions']=bundle.pop('teacher_assertions')
    bundle['video_canon_verification_bundles']={'synthetic':'never evaluate'}
    reader=LocalEvidenceReader(path,pin)
    with patch('diana_longitudinal_quality_v4_2.run_video_canon_auto_pipeline',side_effect=AssertionError('must not promote')):
        result=build_quality_layer(bundle,replay_evidence_reader=reader)
    replay=result['video_canon_auto_pipeline']
    assert replay['status']=='BLOCKED' and not replay['promotion_commands']
    assert any(r['candidate_type']=='video_local_evidence_inspection' for r in result['candidate_staging_records'])


def test_real_retained_fixture_remains_partial_and_keeps_exact_quote(tmp_path):
    """Runs only against the separately delivered private real artifact bundle."""
    directory=os.environ.get('BRIDGE_VIDEO_REAL_FIXTURE_DIR')
    if not directory:
        pytest.skip('private real fixture not supplied; synthetic tests are not e2e')
    root=Path(directory)
    manifest=json.loads((root/'MANIFEST.json').read_bytes())
    for entry in manifest['files']:
        data=(root/entry['path']).read_bytes()
        assert len(data)==entry['bytes'] and sha256(data)==entry['sha256']
    # Copy only exact test inputs into disposable storage to test negative cases.
    for path in (root/'inputs').iterdir():
        if path.is_file(): (tmp_path/path.name).write_bytes(path.read_bytes())
    source=(tmp_path/'master_analysis.raw.json').read_bytes()
    parent=json.loads(source)
    retrieval=manifest['retrieval']
    assert sha256(source)==retrieval['master_json_raw_sha256_recomputed']
    assert digest(parent)==retrieval['master_json_canonical_sha256_recomputed']
    index={'schema':'video-local-evidence-index-v1',
        'parent':{'path':'master_analysis.raw.json','raw_sha256':sha256(source),'canonical_sha256':digest(parent)},
        'source':{'path':None,'video_file_id':parent['source']['driveId'],'sha256':parent['source']['sha256']},
        'frames':[{'path':p.name,'evidence_id':p.stem,'sha256':sha256(p.read_bytes())} for p in sorted(tmp_path.glob('frame_*.jpg'))]}
    path,pin=write_index(tmp_path,index)
    selection=json.loads((tmp_path/'selection.json').read_bytes())
    packet=build_replay_input(source,selection)
    reader=LocalEvidenceReader(path,pin)
    result=reader.replay(packet)
    assert all(r['payload_hash']==digest(r['payload']) for r in result['candidates'])
    assert result['status']=='BLOCKED' and not result['video_canon_inputs'] and not result['promotion_commands']
    assert {'SOURCE_MEDIA_BYTES_MISSING','INDEPENDENT_SOURCE_IDENTITY_ROLE_POLICY_UNAVAILABLE'} <= codes(result)
    record=next(r['payload'] for r in result['candidates'] if r['candidate_type']=='video_artifact_source_observation')
    expected=next(r for r in parent['transcript'] if r['segment_id']==selection['segment_id'])
    assert record['transcript']['quote']==expected['text']
    assert record['speaker_id']==record['speaker_role']=='UNKNOWN'
    assert record['frames'][0]['relation']=='CONTEXT_ONLY_OUTSIDE_SPEECH_INTERVAL'
    assert record['media_review_status']=='UNVERIFIED'
    assert result==reader.replay(packet)
    with sqlite3.connect(':memory:') as db:
        stage_local(db,result)
        assert stage_local(db,reader.replay(packet))['inserted']==0
    (tmp_path/index['frames'][0]['path']).unlink()
    missing=reader.replay(packet)
    assert 'FRAME_UNREADABLE' in codes(missing)
    assert all(r['payload_hash']==digest(r['payload']) for r in missing['candidates'])
    with sqlite3.connect(':memory:') as db:
        stage_local(db,result)
        stage_local(db,missing)  # changed evidence must not collide with sealed drafts
    (tmp_path/'master_analysis.raw.json').write_bytes(source+b' ')
    tampered=reader.replay(packet)
    assert 'PARENT_RAW_DIGEST_MISMATCH' in codes(tampered)
    assert not any(r['candidate_type']=='video_artifact_source_observation' for r in tampered['candidates'])


@pytest.mark.parametrize('lineage', [None, [], 'invalid', {'selection':None}, {'selection':[]}])
def test_malformed_bounded_lineage_never_crashes_reader(tmp_path,lineage):
    from tests.test_video_source_draft import packet
    _,_,_,_,path,pin=fixture(tmp_path)
    bundle=packet()
    bundle['input_builder_lineage']=lineage
    result=LocalEvidenceReader(path,pin).replay(bundle)
    assert result['status']=='BLOCKED'
    assert all(r['payload_hash']==digest(r['payload']) for r in result['candidates'])


@pytest.mark.parametrize('selected_id',[None,'',[],False])
def test_missing_selected_segment_id_cannot_match_missing_parent_id(tmp_path,selected_id):
    from tests.test_video_source_draft import packet
    _,parent,index,_,_,_=fixture(tmp_path)
    parent['transcript'][0].pop('segment_id')
    (tmp_path/'parent.json').write_bytes(raw(parent))
    index['parent']['raw_sha256']=sha256(raw(parent))
    index['parent']['canonical_sha256']=digest(parent)
    path,pin=write_index(tmp_path,index)
    bundle=packet()
    bundle['input_builder_lineage']={'input_kind':'full_master','source_raw_sha256':sha256(raw(parent)),
        'selection':{'segment_id':selected_id}}
    result=LocalEvidenceReader(path,pin).replay(bundle)
    assert result['status']=='BLOCKED'
    assert not any(r['candidate_type']=='video_artifact_source_observation' for r in result['candidates'])
