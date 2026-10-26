"""Entirely synthetic public native qualification data, never school evidence."""
from pathlib import Path
import json,hashlib

def encode(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def digest(x):return sha(encode(x))

def create(root,runner,*,job='SOURCE_ONLY_NATIVE_STAGING_R4'):
    root=Path(root);quote='SOURCE_ONLY DEFINITION FIXTURE. No teaching claim.'
    drive='SOURCE_ONLY_VIDEO_'+sha(job.encode())[:12];media_hash=sha(('SOURCE_ONLY_MEDIA:'+job).encode())
    segment=dict(segment_id='SOURCE_ONLY_S1',start=10.0,end=20.0,text=quote,raw_text=quote,speaker='SOURCE_ONLY_CLUSTER')
    master=dict(schema='synthetic-master-v1',job_id=job,algorithmVersion='3.1 FREE',algorithmRevision='SOURCE_ONLY_NATIVE_STAGING_R4',
        source=dict(driveId=drive,sha256=media_hash,name='SOURCE_ONLY.mp4',sizeBytes=123,durationSeconds=30.0,mimeType='video/mp4',parentFolderId='SOURCE_ONLY'),
        transcript=[segment],episodes=[dict(episode_id='SOURCE_ONLY_E1',start=9.0,end=21.0,segment_ids=[segment['segment_id']],evidence=[segment['segment_id']])],
        screenshots=[],learning_interactions=[dict(cycle_id='SOURCE_ONLY_L1',evidence=[segment['segment_id']])],
        decisions=[dict(decision_id='SOURCE_ONLY_D1',evidence=[segment['segment_id']])])
    master_raw=encode(master)
    done=dict(masterPdf=dict(driveId='SOURCE_ONLY_REPORT',sha256='b'*64,name='SOURCE_ONLY.pdf',sizeBytes=123,masterJsonSha256=sha(master_raw)))
    original=dict(candidate_id='SOURCE_ONLY_CANDIDATE',candidate_type='RULE_CANDIDATE',payload=dict(SOURCE_ONLY=True,segment_id=segment['segment_id']))
    quality=dict(quality_v2=dict(method_version='SOURCE_ONLY',incremental_processing=dict(input_fingerprint='d'*64),candidate_staging_records=[original]))
    quality_raw=encode(quality);generation=digest(quality)[:12]
    owner=dict(status='OWNER_CONFIRMED',test_only=True,message_id='SOURCE_ONLY_NOT_A_USER_MESSAGE',candidate_id=original['candidate_id'],
        candidate_object_sha256=digest(original),segment_id=segment['segment_id'],segment_object_sha256=digest(segment),
        absolute_time_seconds=[10.0,20.0],raw_text_unchanged=quote,owner_confirmed_quote_verbatim=quote,
        automatic_audio_verified=False,source_drive_id=drive,source_video_sha256=media_hash,job_id=job,generation_key=generation)
    owner_raw=encode(owner)
    constraints=dict(owner_confirmation_sidecar=dict(sha256=sha(owner_raw)),domain_boundary='SOURCE_ONLY CARD_PLAY definition fixture; no admission')
    selection=dict(schema='video-replay-selection-v1',input_kind='full_master',source_raw_sha256=sha(master_raw),
        constraints_raw_sha256=digest(constraints),source_document_drive_id='SOURCE_ONLY_REPORT',segment_id=segment['segment_id'],episode_id='SOURCE_ONLY_E1',frame_ids=[])
    bundle=dict(source_generation_key=generation,source_quality_payload_sha256=sha(quality_raw),original_quality_input_fingerprint='d'*64,
        candidate_id=original['candidate_id'],candidate_object_sha256=digest(original),segment_id=segment['segment_id'],time_seconds=[10.0,20.0],raw_text=quote)
    files={'source/MASTER_ANALYSIS.json':master_raw,'source/AI_DONE.json':encode(done),'source/LONGITUDINAL_V42.json':quality_raw,
        'owner/OWNER_CONFIRMATION.json':owner_raw,'owner/CANDIDATE_ORIGINAL.json':encode(original),'owner/SELECTED_SEGMENT_ORIGINAL.json':encode(segment),
        'native-input/SELECTION.json':encode(selection),'native-input/SOURCE_CONSTRAINTS.json':encode(constraints)}
    for name,raw in files.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    im=dict(files=[dict(path=n,bytes=len(raw),sha256=sha(raw)) for n,raw in files.items()])
    (root/'SOURCE_BUNDLE.json').write_bytes(encode(bundle));(root/'INPUTS_MANIFEST.json').write_bytes(encode(im))
    repeated=dict(source_candidate_id=original['candidate_id'],source_drive_id=drive,source_sha256_recorded=media_hash,
        source_segment_id=segment['segment_id'],interval_seconds=[10.0,20.0],raw_text_sha256=sha(quote.encode()),
        owner_message_ref=owner['message_id'],owner_record_sha256=sha(owner_raw),owner_confirmed_text_sha256=sha(quote.encode()))
    from bridge_contracts.video_canon_ai_promotion import REQUIRED_CHECKS
    definition=dict(schema='card-play-definition-proposal-v1',knowledge_domain='CARD_PLAY',knowledge_kind='DEFINITION',statement_text=quote,
        statement_sha256=sha(quote.encode()),source_binding=dict(source_candidate_id=original['candidate_id'],source_drive_id=drive,source_sha256=media_hash,
            source_segment_id=segment['segment_id'],interval_seconds=[10.0,20.0],raw_text=quote,raw_text_sha256=sha(quote.encode())),
        owner_confirmation=dict(status='OWNER_CONFIRMED',message_ref=owner['message_id'],record_sha256=sha(owner_raw),confirmed_role='teacher',named_person=None,independent_audio_review=False),
        scope='DEFINITION_RECOGNITION_ONLY',requires_52_cards=False,semantic_confidence=None,source_authorization=None,
        admission=dict(status='BLOCKED',required_checks=sorted(REQUIRED_CHECKS),verification_receipts=[]))
    proposal=dict(schema='knowledge-storage-skill-proposal-only-v1',status='PROPOSAL_NOT_INSERTABLE_WITHOUT_REAL_FOREIGN_KEYS',
        knowledge_item=dict(knowledge_item_id=None,school_id=None,stable_key='SOURCE_ONLY_DEFINITION_K',knowledge_type='skill',title='SOURCE_ONLY',status='candidate'),
        knowledge_version=dict(knowledge_version_id=None,knowledge_item_id=None,version_no=1,content=dict(source_assertion=repeated,definition_evidence=definition),
            authority_class='research_candidate',review_status='unreviewed',status='candidate',bidding_system_key=None,agreement_scope={},level_scope={},
            effective_from=None,effective_to=None,method_version='SOURCE_ONLY',provenance=repeated),
        knowledge_version_source=dict(knowledge_version_id=None,source_id=None,relation_type='derived_from',source_locator=repeated),
        canon_activation=None,accepted_readback=None,db_write_performed=False)
    proposal_raw=encode(proposal)
    scope=dict(school_key=runner.module('database.video_result_persistence').SCHOOL_STABLE_NAME,system_profile='SOURCE_ONLY_PROFILE',learner_level='SOURCE_ONLY_LEVEL',activation_scope='SOURCE_ONLY_SCOPE')
    pins=dict(expected_bundle_sha256=digest(bundle),expected_inputs_manifest_sha256=digest(im),
        expected_code_pins_sha256=sha((runner.ROOT/'DEPENDENCY_PINS.json').read_bytes()))
    p=runner.prepare_definition_from_bundle(root,proposal_raw=proposal_raw,expected_proposal_sha256=sha(proposal_raw),definition_scope=scope,**pins)
    return p,proposal,scope,pins
