"""Proposed test-only event/delivery consumer; no producer, writer or publisher.

V1 remains unchanged and rejects these extra fields. V2 retains the full guarded
events and uses an explicit legacy read view only after validation. Host-owned
readback is consistency evidence, not self-authenticating publication permission.
Books owner acceptance and a real read lane are still required.
"""
from copy import deepcopy
from uuid import UUID

from . import book_teacher_consumer as legacy

MAP_SCHEMA = 'teacher-book-card-map-v2-proposal'
RECORDED_CONTRACT = 'book-world-recorded-v2'
PUBLISHED_CONTRACT = 'book-world-published-v2'
READBACK_SCHEMA = 'book-world-delivery-readback-v2-proposal'
IMPORT_RECEIPT_CONTRACT = 'book-world-import-receipt-v2'
PUBLICATION_RECEIPT_CONTRACT = 'book-world-publication-receipt-v2'
IMPORT_BASE = frozenset({'schema','changeset_id','version_ids','new_versions','authority_lane',
                         'publication_hash','outbox_delivery','canon_activation_performed','dry_run','committed'})
PUBLICATION_BASE = frozenset({'changeset_id','version_ids','publication','canon_activation_performed','dry_run','committed'})
RECEIPT_V2_FIELDS = frozenset({'receipt_contract','publication_authorization_status'})
RECORDED_BASE = frozenset({'schema','publication_hash','run','source_id','stable_keys','version_ids'})
PUBLISHED_BASE = frozenset({'schema','publication_hash','catalog_receipt_ref'})
V2_FIELDS = frozenset({'event_contract','publication_authorization_status'})
PUBLISHED_RECORDED_REFS = frozenset({'recorded_event_id','recorded_changeset_id','recorded_payload_hash'})
META_FIELDS = frozenset({'event_id','event_type','aggregate_version','school_id','changeset_id',
                        'partition_key','payload','payload_hash','changeset_status','outbox_status','event_position'})
IMMUTABLE_META = META_FIELDS - {'changeset_status','outbox_status','event_position'}
SNAPSHOT_FIELDS = frozenset({'schema','recorded_event','recorded_outbox_receipt','publication_receipt',
                            'published_event','published_outbox_receipt'})


class BookDeliveryBindingError(ValueError):
    pass


def _need(ok,reason):
    if not ok:raise BookDeliveryBindingError(reason)


def _uuid(value):
    try:return type(value) is str and UUID(value).int != 0 and str(UUID(value)) == value
    except (ValueError,TypeError,AttributeError):return False


def _event_payload(value,kind):
    base,contract,schema = ((RECORDED_BASE,RECORDED_CONTRACT,legacy.BOOK_CONTRACT) if kind=='BookWorldRecorded'
                           else (PUBLISHED_BASE,PUBLISHED_CONTRACT,'book-world-delivery-v1'))
    extra=V2_FIELDS if kind=='BookWorldRecorded' else V2_FIELDS|PUBLISHED_RECORDED_REFS
    _need(type(value) is dict and set(value)==base|extra,'V2_EVENT_FIELDS_REQUIRED')
    _need(value['event_contract']==contract and value['schema']==schema,'V2_EVENT_CONTRACT_REQUIRED')
    status=value['publication_authorization_status']
    _need(type(status) is str and status in ('allowed','blocked','unknown'),'V2_AUTHORIZATION_STATUS_REQUIRED')
    return {k:deepcopy(value[k]) for k in base}


def _event(value,kind,school,delivered=False):
    _need(type(value) is dict and set(value)==META_FIELDS,'V2_EVENT_METADATA_REQUIRED')
    _need(_uuid(school) and value['school_id']==school and _uuid(value['event_id'])
          and _uuid(value['changeset_id']) and value['event_type']==kind
          and type(value['aggregate_version']) is int and value['aggregate_version']==1
          and value['partition_key']=='book-world','V2_EVENT_METADATA_BINDING')
    body=_event_payload(value['payload'],kind)
    try:actual_hash=legacy._digest(value['payload'])
    except (ValueError,TypeError):raise BookDeliveryBindingError('V2_EVENT_PAYLOAD_HASH') from None
    _need(value['payload_hash']==actual_hash,'V2_EVENT_PAYLOAD_HASH')
    _need(value['changeset_status']=='committed','EVENT_CHANGESET_NOT_COMMITTED')
    if delivered:
        _need(value['outbox_status']=='published' and type(value['event_position']) is int
              and value['event_position']>0,'EVENT_DELIVERY_NOT_OBSERVED')
    return body


def _authorization(payload):
    status=payload['publication_authorization_status']
    _need(status=='allowed','PUBLICATION_AUTHORIZATION_BLOCKED' if status=='blocked'
          else 'PUBLICATION_AUTHORIZATION_UNKNOWN')


def _receipt(value,base,contract):
    _need(type(value) is dict and set(value)==base|RECEIPT_V2_FIELDS
          and value['receipt_contract']==contract,'V2_RECEIPT_CONTRACT_REQUIRED')
    _need(type(value['publication_authorization_status']) is str
          and value['publication_authorization_status'] in ('allowed','blocked','unknown'),
          'V2_AUTHORIZATION_STATUS_REQUIRED')
    return {k:deepcopy(value[k]) for k in base}


def bind_import_receipt_v2(card_map,receipt,recorded_event,*,expected_school_id,test_only=False):
    """Bind only observed IDs. No V1 upgrade, UUID derivation or implicit allowed.

    event_contract identifies the new payload protocol. aggregate_version keeps
    the existing first-event counter of1; it is not a payload schema version.
    Producer event/idempotency versioning is a separate Books-owner decision.
    """
    _need(test_only is True,'TEST_ONLY_OPT_IN_REQUIRED')
    _need(type(card_map) is dict and card_map.get('schema')==legacy.MAP_SCHEMA
          and card_map.get('observed_import_receipt') is None,'UNBOUND_V1_CARD_MAP_REQUIRED')
    # Preserve the existing pre-COMMIT refusal before considering event metadata.
    _need(type(receipt) is dict and receipt.get('committed') is True and receipt.get('dry_run') is False,
          'IMPORT_NOT_COMMITTED')
    legacy_receipt=_receipt(receipt,IMPORT_BASE,IMPORT_RECEIPT_CONTRACT)
    body=_event(recorded_event,'BookWorldRecorded',expected_school_id)
    _need(receipt['publication_authorization_status']==recorded_event['payload']['publication_authorization_status'],
          'IMPORT_AUTHORIZATION_BINDING')
    _need(recorded_event['changeset_id']==receipt.get('changeset_id'),'RECORDED_CHANGESET_MISMATCH')
    try:bound=legacy.bind_import_receipt(card_map,legacy_receipt,body)
    except legacy.BookTeacherBindingError as exc:raise BookDeliveryBindingError(str(exc)) from None
    bound['schema']=MAP_SCHEMA
    bound['event_delivery_contract']=READBACK_SCHEMA
    bound['expected_school_id']=expected_school_id
    bound['observed_recorded_event_v2']=deepcopy(recorded_event)
    bound['observed_import_receipt_v2']=deepcopy(receipt)
    bound['binding_state']='COMMITTED_REQUIRES_V2_DELIVERY_AND_ACTIVE_WORLD_READBACK'
    return bound


def _outbox(receipt,event):
    _need(type(receipt) is dict and set(receipt)=={'changeset_id','published_count','event_positions'},
          'OUTBOX_RECEIPT_FIELDS')
    _need(receipt['changeset_id']==event['changeset_id'] and type(receipt['published_count']) is int
          and receipt['published_count']==1 and type(receipt['event_positions']) is list
          and len(receipt['event_positions'])==1 and type(receipt['event_positions'][0]) is int
          and receipt['event_positions']==[event['event_position']],'OUTBOX_RECEIPT_BINDING')


def _delivery(snapshot,card_map):
    _need(type(snapshot) is dict and set(snapshot)==SNAPSHOT_FIELDS
          and snapshot['schema']==READBACK_SCHEMA,'V2_DELIVERY_READBACK_REQUIRED')
    school=card_map['expected_school_id'];recorded=snapshot['recorded_event'];published=snapshot['published_event']
    old=_event(recorded,'BookWorldRecorded',school,True)
    new=_event(published,'BookWorldPublished',school,True)
    bound=card_map['observed_recorded_event_v2']
    _need(all(recorded[k]==bound[k] for k in IMMUTABLE_META),'RECORDED_EVENT_READBACK_MISMATCH')
    _authorization(recorded['payload']);_authorization(published['payload'])
    _need(published['payload']['recorded_event_id']==recorded['event_id']
          and published['payload']['recorded_changeset_id']==recorded['changeset_id']
          and published['payload']['recorded_payload_hash']==recorded['payload_hash'],
          'PUBLISHED_RECORDED_CAUSAL_BINDING')
    _need(published['event_id']!=recorded['event_id'] and published['changeset_id']!=recorded['changeset_id']
          and published['event_position']>recorded['event_position'],'PUBLISHED_EVENT_ORDER_OR_IDENTITY')
    _need(new['publication_hash']==old['publication_hash'] and type(new['catalog_receipt_ref']) is str
          and bool(new['catalog_receipt_ref'].strip()),'PUBLISHED_DELIVERY_BINDING')
    receipt=snapshot['publication_receipt']
    _receipt(receipt,PUBLICATION_BASE,PUBLICATION_RECEIPT_CONTRACT)
    _need(receipt['publication_authorization_status']==published['payload']['publication_authorization_status'],
          'PUBLICATION_AUTHORIZATION_BINDING')
    _need(receipt['changeset_id']==published['changeset_id'] and receipt['version_ids']==old['version_ids']
          and receipt['publication']=='WORLD_VERIFIED' and receipt['canon_activation_performed'] is False
          and receipt['dry_run'] is False and receipt['committed'] is True,'PUBLICATION_RECEIPT_BINDING')
    _outbox(snapshot['recorded_outbox_receipt'],recorded);_outbox(snapshot['published_outbox_receipt'],published)


def _legacy_read_view(card_map):
    _need(type(card_map) is dict and card_map.get('schema')==MAP_SCHEMA
          and card_map.get('event_delivery_contract')==READBACK_SCHEMA,'V2_BOUND_MAP_REQUIRED')
    body=_event(card_map['observed_recorded_event_v2'],'BookWorldRecorded',card_map['expected_school_id'])
    _authorization(card_map['observed_recorded_event_v2']['payload'])
    legacy_receipt=_receipt(card_map['observed_import_receipt_v2'],IMPORT_BASE,IMPORT_RECEIPT_CONTRACT)
    _need(card_map['observed_recorded_event_v2']['changeset_id']==legacy_receipt['changeset_id'],
          'V2_RECORDED_CHANGESET_BINDING')
    _need(legacy_receipt==card_map['observed_import_receipt']
          and card_map['observed_import_receipt_v2']['publication_authorization_status']==card_map['observed_recorded_event_v2']['payload']['publication_authorization_status'],
          'V2_IMPORT_RECEIPT_BINDING')
    _need(body==card_map.get('observed_recorded_event_payload'),'V2_LEGACY_VIEW_BINDING')
    view=deepcopy(card_map);view['schema']=legacy.MAP_SCHEMA
    for k in ('event_delivery_contract','expected_school_id','observed_recorded_event_v2','observed_import_receipt_v2'):view.pop(k)
    try:fresh=legacy.bind_import_receipt(view,view['observed_import_receipt'],body)
    except legacy.BookTeacherBindingError as exc:raise BookDeliveryBindingError(str(exc)) from None
    _need(fresh['cards']==view['cards'],'V2_VERSION_BINDING_CHANGED')
    _need(all(c['knowledge_item_id'] is None for c in view['cards']),'DO_NOT_INFER_KNOWLEDGE_ITEM_IDS')
    return view


def consume_card_v2(card_map,card_label,context,*,query_knowledge,render_teacher_book,
                    read_delivery,authority_lane=legacy.LANE,test_only=False):
    """Proposed bounded consumer; old reader item_id remains a version ID."""
    gap=lambda reason:legacy._gap(card_label,reason,authority_lane)
    if test_only is not True:return gap('TEST_ONLY_OPT_IN_REQUIRED')
    if authority_lane!=legacy.LANE:return gap('NO_CANON_FALLBACK_FROM_WORLD_BOOK')
    if type(card_label) is not str or card_label not in legacy.LABELS:return gap('CARD_NOT_CONFIGURED')
    if type(context) is not dict or set(context)-{'topic','stage','dummy_exposed','opening_lead_made','asks_for'}:
        return gap('BOOK_CONTEXT_FIELDS')
    if not isinstance(card_map,dict) or not isinstance(card_map.get('observed_import_receipt'),dict) or card_map['observed_import_receipt'].get('committed') is not True:
        return gap('IMPORT_NOT_COMMITTED')
    if not callable(read_delivery):return gap('V2_DELIVERY_READER_REQUIRED')
    try:
        before=deepcopy(card_map);before_context=deepcopy(context);view=_legacy_read_view(card_map)
        query={'recorded_event_id':card_map['observed_recorded_event_v2']['event_id'],
               'recorded_changeset_id':view['observed_import_receipt']['changeset_id'],
               'publication_hash':view['observed_import_receipt']['publication_hash'],
               'source_id':view['source']['registry_source_id'],'expected_school_id':card_map['expected_school_id']}
        try:first=deepcopy(read_delivery(deepcopy(query)))
        except Exception:return gap('V2_DELIVERY_READER_UNAVAILABLE')
        _delivery(first,card_map)
        if card_map!=before or context!=before_context:return gap('V2_HOST_BINDING_CHANGED')
        result=legacy.consume_card(view,card_label,context,query_knowledge=query_knowledge,
                                   render_teacher_book=render_teacher_book,authority_lane=authority_lane)
        if card_map!=before or context!=before_context:return gap('V2_HOST_BINDING_CHANGED')
        if result.get('status')!='EXPLANATION_READY':return result
        try:fresh=deepcopy(read_delivery(deepcopy(query)))
        except Exception:return gap('V2_DELIVERY_READER_UNAVAILABLE')
        _delivery(fresh,card_map)
        if card_map!=before or context!=before_context:return gap('V2_HOST_BINDING_CHANGED')
        if first!=fresh:return gap('V2_DELIVERY_CHANGED')
        result['event_delivery_contract']=READBACK_SCHEMA
        result['event_delivery_binding']={kind:{k:fresh[kind][k] for k in ('event_id','changeset_id','payload_hash','event_position')}
                                          for kind in ('recorded_event','published_event')}
        result['production_ready']=False
        return result
    except BookDeliveryBindingError as exc:return gap(str(exc))
    except Exception:return gap('V2_DELIVERY_BINDING_UNAVAILABLE')
