"""Pinned local replay and explicit transaction composition. No connector/DSN/CLI DB path."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import importlib
import json
import sys
from write_bounds import BoundedConnection, WriteBudget, limits_for

ROOT=Path(__file__).resolve().parent
def raw_digest(raw): return hashlib.sha256(raw).hexdigest()
def encode(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(value): return raw_digest(encode(value))
def require(ok,code):
    if not ok: raise ValueError(code)

def verify_vendor(expected_pins_sha256):
    raw=(ROOT/'DEPENDENCY_PINS.json').read_bytes()
    require(raw_digest(raw)==expected_pins_sha256,'CODE_MANIFEST_PIN_MISMATCH')
    pins=json.loads(raw)
    for pin in pins['files']:
        p=(ROOT/pin['path']).resolve()
        require(p.is_relative_to(ROOT/'vendor') or p in {ROOT/name for name in
            ('staging_runner.py','write_bounds.py','injected_transactions.py')},'CODE_PATH_OUTSIDE_PACKAGE')
        content=p.read_bytes(); require(len(content)==pin['bytes'] and raw_digest(content)==pin['sha256'],'CODE_PIN_MISMATCH')
    sys.path.insert(0,str(ROOT/'vendor')) if str(ROOT/'vendor') not in sys.path else None
    return pins

def module(name):
    m=importlib.import_module(name)
    require(Path(m.__file__).resolve().is_relative_to(ROOT/'vendor'),'UNPINNED_MODULE_LOADED:'+name)
    return m

@dataclass(frozen=True)
class Prepared:
    master_raw: bytes
    done_raw: bytes
    payload_raw: bytes
    packet_raw: bytes
    replay_raw: bytes
    code_pins_sha256: str
    @property
    def master(self): return json.loads(self.master_raw)
    @property
    def done(self): return json.loads(self.done_raw)
    @property
    def payload(self): return json.loads(self.payload_raw)
    @property
    def generation(self): return self.payload['generation_identity']['sha256']
    def validate(self):
        verify_vendor(self.code_pins_sha256)
        p=self.payload
        require(p['generation_identity']['sha256']==digest({k:v for k,v in p.items() if k!='generation_identity'}),'DERIVED_GENERATION_PIN_MISMATCH')
        require(p['source_scope']['semantic_domain']=='CARD_PLAY','DOMAIN_CHANGED')
        require(p['source_scope']['purpose']=='DIAGNOSTIC_SOURCE_STAGING','SCOPE_CHANGED')
        require(p['parent']['master_raw_sha256']==raw_digest(self.master_raw),'PARENT_MASTER_MISMATCH')
        require(self.done['masterPdf']['masterJsonSha256']==raw_digest(self.master_raw),'DONE_MASTER_MISMATCH')
        replay=json.loads(self.replay_raw)
        require(p['quality_v2']['video_canon_auto_pipeline']==replay,'REPLAY_SUBSTITUTED')
        require(p['quality_v2']['candidate_staging_records']==replay['candidates'],'REPLAY_MEMBERS_SUBSTITUTED')
        require(replay['status']=='BLOCKED' and replay['promotion_commands']==[] and replay['video_canon_inputs']=={},'NON_DIAGNOSTIC_REPLAY')
        module('database.video_candidate_persistence').validate_replay(replay)
        require(len(replay['candidates'])==1,'UNBOUNDED_REPLAY_MEMBERS')
        kind=replay['candidates'][0]['candidate_type']
        if kind=='video_school_canon_candidate':
            require(p['schema']=='video-derived-definition-staging-generation-v1','DEFINITION_GENERATION_SCHEMA')
            packet=json.loads(self.packet_raw)
            selection=packet['input_builder_lineage']['selection']
            rebuilt=module('bridge_contracts.video_replay_input').build_replay_input(self.master_raw,selection,
                constraints_bytes=encode(packet['source_constraints']))
            require(rebuilt==packet,'DEFINITION_PACKET_MASTER_MISMATCH')
            diagnostic=module('bridge_contracts.video_canon_replay').replay_result_bundle(packet)
            require(replay['definition_staging']['parent_diagnostic_replay_sha256']==diagnostic['replay_sha256'],
                'DEFINITION_DIAGNOSTIC_PARENT_MISMATCH')
            expected=dict(diagnostic,candidates=replay['candidates'],definition_staging=replay['definition_staging'])
            expected.pop('replay_sha256');expected['replay_sha256']=digest(expected)
            require(expected==replay,'DEFINITION_REPLAY_DERIVATION_MISMATCH')
            draft=replay['draft_knowledge']
            require(draft['source_constraints']['producer_parent_generation']==p['parent'],'DEFINITION_PARENT_MISMATCH')
            require(draft['source_constraints']['owner_confirmation_record']['job_id']==self.master['job_id'],
                'DEFINITION_ACTUAL_JOB_MISMATCH')
            require(draft['source_claim']['drive_file_id']==self.master['source']['driveId']
                and draft['source_claim']['source_sha256']==self.master['source']['sha256'],'DEFINITION_MASTER_SOURCE_MISMATCH')
            module('bridge_contracts.video_definition_staging').project_definition(replay['candidates'][0]['payload'],replay)
        else:
            require(kind=='video_source_knowledge_draft','UNEXPECTED_REPLAY_CANDIDATE_TYPE')

def prepare_from_bundle(bundle_root,*,expected_bundle_sha256,expected_inputs_manifest_sha256,expected_code_pins_sha256):
    root=Path(bundle_root).resolve(); verify_vendor(expected_code_pins_sha256)
    raw=(root/'SOURCE_BUNDLE.json').read_bytes(); require(raw_digest(raw)==expected_bundle_sha256,'SOURCE_BUNDLE_PIN_MISMATCH')
    bundle=json.loads(raw)
    rawpins=(root/'INPUTS_MANIFEST.json').read_bytes(); require(raw_digest(rawpins)==expected_inputs_manifest_sha256,'INPUT_MANIFEST_PIN_MISMATCH')
    for pin in json.loads(rawpins)['files']:
        path=(root/pin['path']).resolve(); require(path.is_relative_to(root),'INPUT_PATH_ESCAPE')
        content=path.read_bytes(); require(len(content)==pin['bytes'] and raw_digest(content)==pin['sha256'],'INPUT_BYTES_MISMATCH')
    master_raw=(root/'source/MASTER_ANALYSIS.json').read_bytes(); master=json.loads(master_raw)
    done_raw=(root/'source/AI_DONE.json').read_bytes()
    parent=json.loads((root/'source/LONGITUDINAL_V42.json').read_bytes())
    require(digest({k:v for k,v in parent.items() if k!='file_idempotency'})[:12]==bundle['source_generation_key'],'PARENT_GENERATION_MISMATCH')
    selection=json.loads((root/'native-input/SELECTION.json').read_bytes())
    constraints_raw=(root/'native-input/SOURCE_CONSTRAINTS.json').read_bytes()
    require(selection['constraints_raw_sha256']==raw_digest(constraints_raw),'CONSTRAINTS_PIN_MISMATCH')
    owner=json.loads((root/'owner/OWNER_CONFIRMATION.json').read_bytes())
    constraints=json.loads(constraints_raw)
    require(raw_digest((root/'owner/OWNER_CONFIRMATION.json').read_bytes())==constraints['owner_confirmation_sidecar']['sha256'],'OWNER_PIN_MISMATCH')
    require(owner['candidate_id']==bundle['candidate_id'] and owner['segment_id']==bundle['segment_id'],'OWNER_SELECTION_MISMATCH')
    require(owner['absolute_time_seconds']==bundle['time_seconds'] and owner['raw_text_unchanged']==bundle['raw_text'],'OWNER_QUOTE_SCOPE_MISMATCH')
    require(owner['status']=='OWNER_CONFIRMED' and owner['automatic_audio_verified'] is False,'OWNER_PROVENANCE_CHANGED')
    # The complete attestation survives normalization into replay and DB snapshot.
    # An annotation is not a new automatic identity/audio/Canon verification.
    constraints['owner_confirmation_record']=owner
    parent_scope={'generation_key':bundle['source_generation_key'],
        'quality_payload_sha256':bundle['source_quality_payload_sha256'],'master_raw_sha256':raw_digest(master_raw),
        'master_canonical_sha256':digest(master),
        'candidate_id':bundle['candidate_id'],'candidate_object_sha256':bundle['candidate_object_sha256'],
        'quality_input_fingerprint':bundle['original_quality_input_fingerprint']}
    constraints['producer_parent_generation']=parent_scope
    constraints_raw=encode(constraints)
    selection['constraints_raw_sha256']=raw_digest(constraints_raw)
    packet=module('bridge_contracts.video_replay_input').build_replay_input(master_raw,selection,constraints_bytes=constraints_raw)
    replay=module('bridge_contracts.video_canon_replay').replay_result_bundle(packet)
    fp=digest({'parent':parent_scope,'replay_sha256':replay['replay_sha256'],'code_pins_sha256':expected_code_pins_sha256})
    payload={'schema':'video-derived-diagnostic-staging-generation-v1','job_id':master['job_id'],
        'parent':parent_scope,'source_scope':{'semantic_domain':'CARD_PLAY','purpose':'DIAGNOSTIC_SOURCE_STAGING',
            'classification_basis':'Existing selected definition scope; native extracted knowledge remains UNKNOWN',
            'native_extraction_domain':replay['draft_knowledge']['semantic_domain'],
            'automatic_audio_verified':False,'owner_confirmation_message_id':owner['message_id']},
        'quality_v2':{'method_version':'video-diagnostic-staging-assembly-v1',
            'incremental_processing':{'input_fingerprint':fp,'parent_generation_key':bundle['source_generation_key'],
                                     'source_draft_input_sha256':digest(packet)},
            'candidate_staging_records':replay['candidates'],'video_canon_auto_pipeline':replay},
        'execution':{'DB_not_run':True,'admission_not_requested':True,'publication_allowed':False},
        'code_pins_sha256':expected_code_pins_sha256}
    payload['generation_identity']={'sha256':digest(payload),'hash_domain':'CANONICAL_JSON_WITHOUT_GENERATION_IDENTITY'}
    prepared=Prepared(master_raw,done_raw,encode(payload),encode(packet),encode(replay),expected_code_pins_sha256)
    prepared.validate()
    return prepared

def prepare_definition_from_bundle(bundle_root,*,proposal_raw,expected_proposal_sha256,definition_scope,**pins):
    """New explicit derived generation; existing diagnostic/source bytes survive.

    Host pins are integrity inputs, never authority or an authenticated provider.
    This function performs local preparation only and does not persist anything.
    """
    require(type(proposal_raw) is bytes and raw_digest(proposal_raw)==expected_proposal_sha256,'DEFINITION_PROPOSAL_RAW_PIN_MISMATCH')
    base=prepare_from_bundle(bundle_root,**pins)
    require(definition_scope['school_key']==module('database.video_result_persistence').SCHOOL_STABLE_NAME,
        'DEFINITION_SCHOOL_KEY_MISMATCH')
    # These bytes were verified by the existing input manifest in preparation;
    # now tie each selected object to that exact pinned parent generation.
    root=Path(bundle_root).resolve();parent=base.payload['parent']
    quality_raw=(root/'source/LONGITUDINAL_V42.json').read_bytes();quality=json.loads(quality_raw)
    original=json.loads((root/'owner/CANDIDATE_ORIGINAL.json').read_bytes())
    selected=json.loads((root/'owner/SELECTED_SEGMENT_ORIGINAL.json').read_bytes())
    packet=json.loads(base.packet_raw);owner=packet['source_constraints']['owner_confirmation_record']
    require(raw_digest(quality_raw)==parent['quality_payload_sha256'],'DEFINITION_QUALITY_PARENT_MISMATCH')
    require(digest(original)==parent['candidate_object_sha256'] and original['candidate_id']==parent['candidate_id']
        and sum(row==original for row in quality['quality_v2']['candidate_staging_records'])==1,'DEFINITION_ORIGINAL_CANDIDATE_MISMATCH')
    require(selected==packet['selected_transcript_segment']['original_record']
        and digest(selected)==owner['segment_object_sha256'] and owner['job_id']==base.master['job_id'],
        'DEFINITION_ORIGINAL_SEGMENT_MISMATCH')
    proposal=module('bridge_contracts.video_replay_input').read_json(proposal_raw)
    member=module('database.definition_candidate_producer').build_definition_staging_record(proposal,scope=definition_scope)
    replay=json.loads(base.replay_raw);parent_replay_hash=replay.pop('replay_sha256')
    replay['candidates']=[member]
    replay['definition_staging']=dict(schema='video-definition-staging-correlation-v1',scope=definition_scope,
        candidate_payload_sha256=member['payload_hash'],logical_ki_key=member['stable_key'],
        parent_diagnostic_replay_sha256=parent_replay_hash)
    replay['replay_sha256']=digest(replay)
    payload=base.payload;payload.pop('generation_identity')
    payload['schema']='video-derived-definition-staging-generation-v1'
    payload['definition_proposal_raw_sha256']=expected_proposal_sha256
    quality=payload['quality_v2'];quality['method_version']='video-definition-staging-assembly-v1'
    quality['candidate_staging_records']=[member];quality['video_canon_auto_pipeline']=replay
    quality['incremental_processing']['input_fingerprint']=digest(dict(parent=payload['parent'],
        replay_sha256=replay['replay_sha256'],code_pins_sha256=base.code_pins_sha256))
    payload['generation_identity']=dict(sha256=digest(payload),hash_domain='CANONICAL_JSON_WITHOUT_GENERATION_IDENTITY')
    result=Prepared(base.master_raw,base.done_raw,encode(payload),base.packet_raw,encode(replay),base.code_pins_sha256)
    result.validate()
    return result


def persist_with_adapter(prepared,adapter):
    """Caller supplies trusted transaction/read connections; no service access here.

    Adapter: evidence_class, begin_attempt(nonce), outcome_for(nonce),
    write_transaction(nonce), read_transaction(nonce), connection_identity(conn).
    Fresh nonce and transaction ACK are mandatory. Identity is a trusted backend
    session token, not Python object identity. Commit-exit exceptions are UNKNOWN
    unless this attempt has a confirmed rollback. No automatic reconnect/retry.
    No connection is accepted from bundle JSON or environment.
    """
    prepared.validate()
    require(adapter.evidence_class in ('SYNTHETIC_TRANSACTION_DOUBLE','AUTHORIZED_CALLER_CONNECTION'),'ADAPTER_EVIDENCE_CLASS_INVALID')
    budget=WriteBudget(limits_for(prepared.master,len(prepared.payload['quality_v2']['candidate_staging_records'])))
    persisted=None; written=None; raw_write=None; commit_ack=False
    from uuid import uuid4
    attempt=str(uuid4())  # Control token, never a persistence/replay identifier.
    try:
        adapter.begin_attempt(attempt)
        with adapter.write_transaction(attempt) as raw_write:
            connection=BoundedConnection(raw_write,budget)
            persisted=module('database.video_result_persistence').persist_video_result_on_connection(connection,prepared.master,prepared.done)
            require(persisted.get('persisted') is True and persisted.get('rolled_back') is False,'PRODUCER_WRITE_NOT_COMPLETED')
            budget.phase='source_identity'
            persisted['source_identity']=module('database.source_identity_persistence').ensure_drive_source_identity_on_connection(
                connection,source_drive_id=prepared.master['source']['driveId'],display_name=prepared.master['source']['name'],job_id=prepared.master['job_id'])
            def publication_forbidden(*args,**kwargs): raise ValueError('PUBLICATION_FORBIDDEN')
            persisted['outbox']=module('database.outbox_publication_decision').publish_if_authorized('',persisted,publication_forbidden)
            budget.phase='candidate_staging'
            def injected_writer(marker,payload,*,persisted_result):
                require(marker=='INJECTED_CONNECTION_NO_DSN','UNEXPECTED_DSN_PATH')
                records=payload['quality_v2']['candidate_staging_records']
                if records[0]['candidate_type']=='video_school_canon_candidate':
                    # Obtain exact facts through the existing scoped database
                    # helper, not from proposal/replay/adapter JSON assertions.
                    lineage_module=module('database.video_staging_lineage')
                    with connection.cursor() as cursor:
                        school=lineage_module.fixed_school_id(cursor)
                        lineage=lineage_module.exact_lineage(cursor,school,persisted_result,job_id=prepared.master['job_id'])
                        module('database.video_definition_staging_lineage').require_definition_segment(
                            cursor,records[0]['payload'],payload['quality_v2']['video_canon_auto_pipeline'],lineage)
                return module('database.video_candidate_persistence').persist_quality_candidates_on_connection(connection,payload,persisted_result=persisted_result)
            written=module('database.video_staging_handoff').persist_quality_handoff(
                'INJECTED_CONNECTION_NO_DSN',prepared.payload,persisted,writer=injected_writer)
        require(adapter.outcome_for(attempt)=='COMMITTED','COMMIT_OUTCOME_NOT_PROVEN')
        commit_ack=True
        write_identity=adapter.connection_identity(raw_write)
        require(write_identity is not None,'WRITE_BACKEND_IDENTITY_MISSING')
        with adapter.read_transaction(attempt) as read_connection:
            read_identity=adapter.connection_identity(read_connection)
            require(read_identity is not None and read_identity!=write_identity,'READBACK_REUSES_WRITE_BACKEND')
            witness=module('database.video_staging_receipt_read').read_committed_receipt_on_connection(
                read_connection,receipt_id=written['replay_receipt_id'],analysis_run_id=written['analysis_run_id'],source_id=written['source_id'])
            require(witness.get('status')=='COMMITTED_RECEIPT','COMMITTED_WITNESS_MISSING')
            require(all(witness.get(k)==v for k,v in {'receipt_id':written['replay_receipt_id'],
                'analysis_run_id':written['analysis_run_id'],'source_id':written['source_id']}.items()),'RECEIPT_READBACK_SUBSTITUTED')
            require(len(written['candidate_ids'])==1,'UNEXPECTED_CANDIDATE_COUNT')
            read=module('database.video_staging_read').read_internal_school_video_candidate(read_connection,written['candidate_ids'][0])
            require(read.get('status')=='READABLE_STAGING','CANDIDATE_READBACK_FAILED')
            expected={'candidate_id':written['candidate_ids'][0],'source_id':persisted['source_id'],
                'analysis_run_id':persisted['analysis_run_id'],'source_asset_id':persisted['source_asset_id'],
                'artifact_version_id':persisted['artifact_version_id'],
                'replay_receipt_id':written['replay_receipt_id'],
                'payload_sha256':prepared.payload['quality_v2']['candidate_staging_records'][0]['payload_hash'],
                'replay_sha256':json.loads(prepared.replay_raw)['replay_sha256'],
                'parent_raw_sha256':raw_digest(prepared.master_raw),'parent_canonical_sha256':digest(prepared.master),
                'source_media_sha256_claim':prepared.master['source']['sha256'],
                'source_locator':'gdrive:file:'+prepared.master['source']['driveId']}
            require(all(read.get(k)==v for k,v in expected.items()),'CANDIDATE_READBACK_SUBSTITUTED')
            require(read.get('accepted_canon')==[] and read.get('canon_write_allowed') is False and read.get('student_delivery_allowed') is False,'READBACK_AUTHORITY_ESCALATION')
        synthetic=adapter.evidence_class=='SYNTHETIC_TRANSACTION_DOUBLE'
        return {'status':'SYNTHETIC_COMMIT_READBACK_CHECK_PASSED' if synthetic else 'COMMITTED_AND_READ_BACK',
            'evidence_class':adapter.evidence_class,'database_execution_proven':not synthetic,
            'generation_sha256':prepared.generation,'parent_generation_key':prepared.payload['parent']['generation_key'],
            'semantic_domain':'CARD_PLAY','bounds':budget.report(),
            'synthetic_outputs' if synthetic else 'writer_outputs':{'persisted_result':persisted,'staging_result':written,'receipt_readback':witness,'candidate_readback':read}}
    except Exception as exc:
        try: outcome=adapter.outcome_for(attempt)
        except Exception: outcome='UNKNOWN'
        status='COMMITTED_READBACK_FAILED' if commit_ack else {'ROLLED_BACK':'ROLLED_BACK','NOT_STARTED':'NOT_STARTED'}.get(outcome,'UNKNOWN_COMMIT_OR_ROLLBACK')
        return {'status':status,'evidence_class':adapter.evidence_class,'database_execution_proven':False,
                'failure_code':str(exc) if isinstance(exc,ValueError) else type(exc).__name__,
                'transaction_outcome':('COMMITTED' if commit_ack else outcome if outcome in ('ROLLED_BACK','NOT_STARTED') else 'UNKNOWN'),
                'automatic_retry_allowed':False,'bounds':budget.report(),
                'reconcile_writer_outputs':{'persisted_result':persisted,'staging_result':written} if outcome in ('COMMITTED','UNKNOWN') else None}

def save_prepared(prepared,output):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    files={'MASTER_ANALYSIS.raw.json':prepared.master_raw,'AI_DONE.raw.json':prepared.done_raw,
           'DERIVED_GENERATION.json':prepared.payload_raw,'BOUNDED_SOURCE_PACKET.json':prepared.packet_raw,
           'STAGING_REPLAY.json':prepared.replay_raw,
           'WRITE_LIMITS.json':encode(limits_for(prepared.master,1))}
    for name,raw in files.items():
        p=output/name
        require(not p.exists() or p.read_bytes()==raw,'EXISTING_OUTPUT_CONFLICT:'+name)
        p.write_bytes(raw)
    manifest={'schema':'prepared-staging-generation-v1','generation_sha256':prepared.generation,
        'parent_generation_key':prepared.payload['parent']['generation_key'],'code_pins_sha256':prepared.code_pins_sha256,
        'DB_execution':'NOT_RUN','replay_status':json.loads(prepared.replay_raw)['status'],
        'files':[{'path':name,'bytes':len(raw),'sha256':raw_digest(raw)} for name,raw in files.items()]}
    (output/'PREPARED.json').write_bytes(encode(manifest))
    return manifest

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Local prepare only; no DB execution command')
    parser.add_argument('--bundle',required=True); parser.add_argument('--output',required=True)
    parser.add_argument('--bundle-sha256',required=True); parser.add_argument('--inputs-manifest-sha256',required=True)
    parser.add_argument('--code-pins-sha256',required=True)
    args=parser.parse_args()
    p=prepare_from_bundle(args.bundle,expected_bundle_sha256=args.bundle_sha256,
        expected_inputs_manifest_sha256=args.inputs_manifest_sha256,expected_code_pins_sha256=args.code_pins_sha256)
    print(json.dumps(save_prepared(p,args.output),ensure_ascii=False))
