"""Typed Definition staging correlation, never authority or Canon acceptance.

Parent/selection evidence stays in the existing replay snapshot, not rewritten
into the immutable KI/KV proposal. A source claim is not an attestor receipt.
"""
from database.definition_candidate_producer import build_definition_staging_record
from database.definition_initial_admission import proposal_check, same_json
from bridge_contracts.card_play_definition_admission import _digest

KIND='video_school_canon_candidate'
SCHEMA='video-definition-staging-correlation-v1'

def need(ok,code):
    if not ok:raise ValueError(code)

def project_definition(payload,replay):
    need(type(replay) is dict,'DEFINITION_REPLAY_REQUIRED')
    context=replay['definition_staging']
    need(type(context) is dict and set(context)=={'schema','scope','candidate_payload_sha256','logical_ki_key','parent_diagnostic_replay_sha256'}
        and context['schema']==SCHEMA,'DEFINITION_CONTEXT_INVALID')
    definition=proposal_check(payload,context['scope'])
    need(context['candidate_payload_sha256']==_digest(payload) and context['logical_ki_key']==payload['knowledge_item']['stable_key'],
        'DEFINITION_CONTEXT_PAYLOAD_MISMATCH')
    # This is the exact member ABI accepted by Video, not an arbitrary new kind.
    members=replay['candidates']
    expected=build_definition_staging_record(payload,scope=context['scope'])
    need(type(members) is list and len(members)==1 and same_json(members[0],expected),'DEFINITION_MEMBER_NOT_UNIQUE')
    draft=replay['draft_knowledge']
    need(draft['schema']=='video-source-knowledge-draft-v1' and draft['status']=='CANDIDATE_RESEARCH'
        and draft['acceptance_status']=='BLOCKED','DEFINITION_SOURCE_DRAFT_REQUIRED')
    # Restore the exact pinned bounded-source branch's member ABI to verify
    # its prior diagnostic replay hash at read time as well as preparation.
    # This only verifies retained correlation, never a trusted source receipt.
    sha=_digest(draft)
    prior={k:v for k,v in replay.items() if k not in ('replay_sha256','definition_staging')}
    prior['candidates']=[dict(candidate_type='video_source_knowledge_draft',stable_key='video-source-draft:sha256:'+sha,
        payload_hash=sha,payload=draft,quality_status='BLOCKED',promotion_status='STAGING_ONLY',evidence_refs=[],
        method_version=draft['schema'],authoritative_tables_modified=False)]
    need(prior['bundle_sha256']==draft['input_packet_sha256'] and _digest(prior)==context['parent_diagnostic_replay_sha256'],
        'DEFINITION_DIAGNOSTIC_PARENT_MISMATCH')
    source=draft['source_claim'];parent=draft['parent_document_claim'];text=draft['transcript']
    constraints=draft['source_constraints'];generation=constraints['producer_parent_generation']
    owner=constraints['owner_confirmation_record'];binding=definition['source_binding']
    need(binding['source_drive_id']==source['drive_file_id'] and binding['source_sha256']==source['source_sha256']
        and binding['source_segment_id']==text['segment_id']
        and same_json(binding['interval_seconds'],[text['start'],text['end']])
        and binding['raw_text']==text['exact_text'] and binding['raw_text_sha256']==text['exact_text_utf8_sha256']
        and binding['source_candidate_id']==generation['candidate_id'],'DEFINITION_SOURCE_SELECTION_MISMATCH')
    need(generation['master_raw_sha256']==parent['embedded_raw_sha256']
        and generation['master_canonical_sha256']==parent['embedded_canonical_sort_compact_utf8_sha256'],
        'DEFINITION_PARENT_MISMATCH')
    need(owner['status']=='OWNER_CONFIRMED' and owner['automatic_audio_verified'] is False
        and owner['message_id']==definition['owner_confirmation']['message_ref']
        and owner['candidate_id']==binding['source_candidate_id'] and owner['segment_id']==binding['source_segment_id']
        and owner['generation_key']==generation['generation_key']
        and owner['candidate_object_sha256']==generation['candidate_object_sha256']
        and owner['segment_object_sha256']==_digest(text['original_record'])
        and owner['source_drive_id']==binding['source_drive_id'] and owner['source_video_sha256']==binding['source_sha256']
        and same_json(owner['absolute_time_seconds'],binding['interval_seconds'])
        and owner['raw_text_unchanged']==binding['raw_text']
        and owner['owner_confirmed_quote_verbatim']==definition['statement_text'],
        'DEFINITION_OWNER_SELECTION_MISMATCH')
    # Repeated locators are not allowed to contradict the immutable definition.
    for locator in (payload['knowledge_version']['provenance'],payload['knowledge_version_source']['source_locator'],
                    payload['knowledge_version']['content']['source_assertion']):
        for key,target in (('source_candidate_id','source_candidate_id'),('source_drive_id','source_drive_id'),
            ('source_sha256_recorded','source_sha256'),('source_segment_id','source_segment_id'),
            ('interval_seconds','interval_seconds'),('raw_text_sha256','raw_text_sha256')):
            need(same_json(locator[key],binding[target]),'DEFINITION_REPEATED_SOURCE_MISMATCH')
        need(locator['owner_message_ref']==definition['owner_confirmation']['message_ref']
            and locator['owner_record_sha256']==definition['owner_confirmation']['record_sha256']
            and locator['owner_confirmed_text_sha256']==definition['statement_sha256'],
            'DEFINITION_REPEATED_SOURCE_MISMATCH')
    return 'DEFINITION_RESEARCH',dict(quote=binding['raw_text'],start=binding['interval_seconds'][0],end=binding['interval_seconds'][1]),dict(
        source_video_file_id=binding['source_drive_id'],source_media_sha256=binding['source_sha256'],
        parent_raw_sha256=parent['embedded_raw_sha256'],parent_canonical_sha256=parent['embedded_canonical_sort_compact_utf8_sha256'])

def validate_definition_lineage(payload,replay,lineage):
    """Lineage must be returned by existing exact_lineage on the host connection.

    The helper compares values; it does not authenticate a caller dictionary.
    Only the runner/read adapter obtains actual database facts.
    """
    stage,display,claims=project_definition(payload,replay)
    need(all(lineage[k]==claims[k] for k in ('source_video_file_id','source_media_sha256','parent_raw_sha256')),
        'DEFINITION_ACTUAL_LINEAGE_MISMATCH')
    need(replay['draft_knowledge']['source_constraints']['owner_confirmation_record']['job_id']==lineage['job_id'],
        'DEFINITION_ACTUAL_JOB_MISMATCH')
    return stage,display,claims
