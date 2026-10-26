"""Explicit DEFINITION member for the existing source-bound staging writer.

This builds a draft record only, not a replay/persisted_result, ID or receipt.
The Video owner still binds the member to its actual reviewed replay/lineage.
"""
from copy import deepcopy
from bridge_contracts.card_play_definition_admission import _digest
from bridge_contracts.definition_staging_identity import definition_staging_keys
from database.definition_initial_admission import proposal_check

METHOD_VERSION='definition-candidate-producer-contract-v2'

def build_definition_staging_record(proposal,*,scope):
    before=_digest(proposal)
    payload=deepcopy(proposal)
    proposal_check(payload,scope)
    logical=payload['knowledge_item']['stable_key']
    definition_staging_keys(logical,before)
    if _digest(proposal)!=before or _digest(payload)!=before:
        raise ValueError('DEFINITION_PRODUCER_INPUT_CHANGED')
    # Explicit record base R=logical K for this typed branch only. The existing
    # generic writer, unchanged, derives W(K,H) and its own native UUID.
    return dict(candidate_type='video_school_canon_candidate',stable_key=logical,
        payload=payload,payload_hash=before,quality_status='UNKNOWN',promotion_status='STAGING_ONLY',
        evidence_refs=[],reasons=['DEFINITION_GENUINE_CHECKS_REQUIRED'],method_version=METHOD_VERSION,
        authoritative_tables_modified=False)

__all__=['METHOD_VERSION','build_definition_staging_record']
