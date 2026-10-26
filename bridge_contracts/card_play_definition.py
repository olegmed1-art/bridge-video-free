"""Source-only CARD_PLAY definition content in the existing KI/KV proposal.

This is a representation validator, not an authority verifier, admission issuer,
writer, promotion command or Teacher route. Input hashes establish correlation,
not source ownership or trusted identity. All sixteen Canon gates stay closed.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from bridge_contracts.video_canon_ai_promotion import REQUIRED_CHECKS
from bridge_contracts.video_canon_evidence import _has_forbidden_key, _has_forbidden_value

SCHEMA = 'card-play-definition-proposal-v1'
_SHA = re.compile(r'[0-9a-f]{64}')


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _fields(value, names):
    _require(type(value) is dict and set(value) == set(names.split()), 'DEFINITION_FIELDS')


def _digest(text):
    _require(type(text) is str and bool(text.strip()), 'DEFINITION_TEXT')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _json(raw):
    _require(type(raw) is bytes, 'RAW_UTF8_BYTES_REQUIRED')
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError('NONFINITE_JSON')
    def finite_float(text):
        value = float(text)
        _require(math.isfinite(value), 'NONFINITE_JSON')
        return value
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_constant=invalid_constant, parse_float=finite_float)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('INVALID_JSON') from exc
    _require(type(value) is dict, 'JSON_OBJECT_REQUIRED')
    return value


def validate_card_play_definition(value):
    """Validate only the explicit unaccepted content subtype; grant no trust."""
    _fields(value, 'schema knowledge_domain knowledge_kind statement_text statement_sha256 '
        'source_binding owner_confirmation scope requires_52_cards semantic_confidence source_authorization admission')
    _require(value['schema'] == SCHEMA and value['knowledge_domain'] == 'CARD_PLAY'
        and value['knowledge_kind'] == 'DEFINITION', 'DEFINITION_KIND_OR_DOMAIN')
    _require(_digest(value['statement_text']) == value['statement_sha256'], 'DEFINITION_TEXT_HASH')
    _require(value['scope'] == 'DEFINITION_RECOGNITION_ONLY'
        and value['requires_52_cards'] is False, 'DEFINITION_SCOPE')
    _require(value['semantic_confidence'] is None and value['source_authorization'] is None,
        'SOURCE_ONLY_NO_AUTHORITY')
    source = value['source_binding']
    _fields(source, 'source_candidate_id source_drive_id source_sha256 source_segment_id '
        'interval_seconds raw_text raw_text_sha256')
    for key in ('source_candidate_id', 'source_drive_id', 'source_segment_id'):
        _require(type(source[key]) is str and bool(source[key].strip()), 'SOURCE_BINDING')
    _require(type(source['source_sha256']) is str and bool(_SHA.fullmatch(source['source_sha256'])),
        'SOURCE_DIGEST')
    _require(_digest(source['raw_text']) == source['raw_text_sha256'], 'SOURCE_TEXT_HASH')
    span = source['interval_seconds']
    _require(type(span) is list and len(span) == 2, 'SOURCE_INTERVAL')
    try:
        valid = all(type(x) in (int, float) and math.isfinite(x) for x in span) and 0 <= span[0] < span[1]
    except OverflowError:
        valid = False
    _require(valid, 'SOURCE_INTERVAL')
    owner = value['owner_confirmation']
    _fields(owner, 'status message_ref record_sha256 confirmed_role named_person independent_audio_review')
    _require(owner['status'] == 'OWNER_CONFIRMED' and owner['confirmed_role'] == 'teacher'
        and owner['named_person'] is None and owner['independent_audio_review'] is False,
        'OWNER_CONFIRMATION_SCOPE')
    _require(type(owner['message_ref']) is str and bool(owner['message_ref'].strip())
        and type(owner['record_sha256']) is str and bool(_SHA.fullmatch(owner['record_sha256'])),
        'OWNER_CONFIRMATION_BINDING')
    admission = value['admission']
    _fields(admission, 'status required_checks verification_receipts')
    _require(admission['status'] == 'BLOCKED' and admission['verification_receipts'] == []
        and type(admission['verification_receipts']) is list, 'NO_CANON_RECEIPTS_OR_ACCEPTANCE')
    _require(type(admission['required_checks']) is list
        and admission['required_checks'] == sorted(REQUIRED_CHECKS), 'ALL_CANON_GATES_REQUIRED')
    _require(not _has_forbidden_key(value) and not _has_forbidden_value(value), 'HIDDEN_INFORMATION')


def adapt_definition_proposal(manifest_raw, owner_raw, storage_raw):
    """Attach typed definition evidence to the same unaccepted KI/KV proposal.

    The owner/storage raw-byte pins are checked before parsing those files.
    The manifest itself still needs an authenticated external provenance check
    before any future admission; a self-consistent manifest confers no trust.
    No new DB identity, policy, receipt, accepted version or activation is made.
    """
    manifest = _json(manifest_raw)
    _require(manifest.get('schema') == 'owner-confirmed-definition-proposal-manifest-v1'
        and manifest.get('proposed_domain') == 'CARD_PLAY'
        and manifest.get('scope') == 'LOCAL_DRAFT_SOURCE_RECOGNITION_ONLY', 'DEFINITION_PROPOSAL_SCOPE')
    for key in ('normalized_BIDDING_rule', 'real_KI_KV_activation_pins', 'replay_receipt_id', 'source_DB_ID'):
        _require(key in manifest and manifest[key] is None, 'SOURCE_ONLY_NO_RUNTIME_PINS')
    files = manifest.get('files')
    _require(type(files) is dict, 'FILE_PIN_MISMATCH')
    for name, raw in [('OWNER_CONFIRMATION.json', owner_raw), ('KNOWLEDGE_VERSION_PROPOSAL.json', storage_raw)]:
        _require(type(raw) is bytes, 'RAW_UTF8_BYTES_REQUIRED')
        pin = files.get(name)
        _require(type(pin) is dict, 'FILE_PIN_MISMATCH')
        _require(type(pin.get('bytes')) is int and pin['bytes'] == len(raw)
            and pin.get('sha256') == hashlib.sha256(raw).hexdigest(), 'FILE_PIN_MISMATCH')
    owner, storage = _json(owner_raw), _json(storage_raw)
    refs = manifest.get('source_refs')
    _require(type(refs) is dict, 'SOURCE_BINDING')
    for left, right in [('source_candidate_id', 'source_candidate_id'),
            ('source_drive_id', 'source_drive_id'), ('source_sha256_recorded', 'source_sha256_recorded'),
            ('source_segment_id', 'source_segment_id'), ('interval_seconds', 'source_ASR_interval_seconds'),
            ('raw_text_sha256', 'raw_ASR_text_sha256'), ('owner_confirmed_text_sha256', 'confirmed_definition_text_sha256'),
            ('owner_message_ref', 'source_message_id')]:
        _require(left in refs and right in owner and refs[left] == owner[right], 'SOURCE_BINDING')
    _require(refs.get('owner_record_sha256') == hashlib.sha256(owner_raw).hexdigest()
        and refs.get('assertion_status') == 'OWNER_CONFIRMED'
        and 'semantic_confidence' in refs and refs['semantic_confidence'] is None, 'SOURCE_BINDING')
    _fields(storage, 'schema status knowledge_item knowledge_version knowledge_version_source '
        'canon_activation accepted_readback db_write_performed')
    _require(storage.get('schema') == 'knowledge-storage-skill-proposal-only-v1'
        and storage.get('status') == 'PROPOSAL_NOT_INSERTABLE_WITHOUT_REAL_FOREIGN_KEYS'
        and storage.get('db_write_performed') is False, 'STORAGE_PROPOSAL_ONLY')
    for key in ('canon_activation', 'accepted_readback'):
        _require(key in storage and storage[key] is None, 'STORAGE_PROPOSAL_ONLY')
    item, version, link = (storage.get(key) for key in ('knowledge_item', 'knowledge_version', 'knowledge_version_source'))
    _require(all(type(x) is dict for x in (item, version, link)), 'STORAGE_OBJECTS')
    _fields(item, 'knowledge_item_id school_id stable_key knowledge_type title status')
    _fields(version, 'knowledge_version_id knowledge_item_id version_no content authority_class review_status '
        'status bidding_system_key agreement_scope level_scope effective_from effective_to method_version provenance')
    _fields(link, 'knowledge_version_id source_id relation_type source_locator')
    for obj, keys in [(item, ('knowledge_item_id', 'school_id')),
            (version, ('knowledge_version_id', 'knowledge_item_id')),
            (link, ('knowledge_version_id', 'source_id'))]:
        _require(all(key in obj and obj[key] is None for key in keys), 'NO_REAL_OR_SYNTHETIC_DB_IDS')
    _require(item.get('status') == 'candidate' and item.get('knowledge_type') == 'skill'
        and version.get('authority_class') == 'research_candidate'
        and version.get('review_status') == 'unreviewed' and version.get('status') == 'candidate'
        and type(version.get('version_no')) is int and version['version_no'] > 0, 'STORAGE_AUTHORITY')
    content = version.get('content')
    _fields(content, 'catalog_skill recognition_scope_only source_assertion')
    skill = content['catalog_skill']
    _require(type(skill) is dict and skill.get('review_state') == 'DRAFT'
        and type(item.get('stable_key')) is str and bool(item['stable_key'].strip())
        and item['stable_key'] == skill.get('skill_id'), 'STORAGE_SKILL_BINDING')
    _require(content['recognition_scope_only'] is True and content['source_assertion'] == refs
        and link.get('relation_type') == 'derived_from' and link.get('source_locator') == refs,
        'SOURCE_BINDING')
    definition = {
        'schema': SCHEMA, 'knowledge_domain': 'CARD_PLAY', 'knowledge_kind': 'DEFINITION',
        'statement_text': owner.get('confirmed_definition_text'),
        'statement_sha256': owner.get('confirmed_definition_text_sha256'),
        'source_binding': {'source_candidate_id': refs['source_candidate_id'],
            'source_drive_id': refs['source_drive_id'], 'source_sha256': refs['source_sha256_recorded'],
            'source_segment_id': refs['source_segment_id'], 'interval_seconds': deepcopy(refs['interval_seconds']),
            'raw_text': owner.get('raw_ASR_text'), 'raw_text_sha256': refs['raw_text_sha256']},
        'owner_confirmation': {'status': owner.get('status'), 'message_ref': owner.get('source_message_id'),
            'record_sha256': refs['owner_record_sha256'], 'confirmed_role': owner.get('confirmed_speaker_role'),
            'named_person': owner.get('confirmed_named_person'), 'independent_audio_review': owner.get('independent_audio_review')},
        'scope': 'DEFINITION_RECOGNITION_ONLY', 'requires_52_cards': False,
        'semantic_confidence': None, 'source_authorization': None,
        'admission': {'status': 'BLOCKED', 'required_checks': sorted(REQUIRED_CHECKS), 'verification_receipts': []},
    }
    validate_card_play_definition(definition)
    output = deepcopy(storage)
    output['knowledge_version']['content']['definition_evidence'] = definition
    _require(not _has_forbidden_key(output) and not _has_forbidden_value(output), 'HIDDEN_INFORMATION')
    return output


__all__ = ['SCHEMA', 'validate_card_play_definition', 'adapt_definition_proposal']
