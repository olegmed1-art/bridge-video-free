"""Synthetic observations only; no real COMMIT/event/delivery is manufactured."""
import ast
from copy import deepcopy
from pathlib import Path
import unittest

from evolutionary_course import book_teacher_consumer as legacy
from evolutionary_course import book_teacher_delivery_consumer_v2 as v2
from bridge_contracts.book_world import render_teacher_book
from database.outbox_publication_decision import publish_if_authorized
from test_book_teacher_consumer_integration import fixture

SCHOOL='11111111-1111-1111-1111-111111111111'
RECORDED_ID='44444444-4444-4444-8444-444444444444'
PUBLISHED_ID='55555555-5555-4555-8555-555555555555'
PUBLISHED_CHANGE='66666666-6666-4666-8666-666666666666'


def synthetic_fixture():
    packet,unbound,bound,items=fixture(multiple_bounds=True)
    receipt={**deepcopy(bound['observed_import_receipt']),
             'receipt_contract':v2.IMPORT_RECEIPT_CONTRACT,'publication_authorization_status':'allowed'}
    payload={**deepcopy(bound['observed_recorded_event_payload']),
             'event_contract':v2.RECORDED_CONTRACT,'publication_authorization_status':'allowed'}
    recorded={'event_id':RECORDED_ID,'event_type':'BookWorldRecorded','aggregate_version':1,
              'school_id':SCHOOL,'changeset_id':receipt['changeset_id'],'partition_key':'book-world',
              'payload':payload,'payload_hash':legacy._digest(payload),'changeset_status':'committed',
              'outbox_status':'pending','event_position':None}
    mapped=v2.bind_import_receipt_v2(unbound,receipt,recorded,expected_school_id=SCHOOL,test_only=True)
    published_payload={'schema':'book-world-delivery-v1','publication_hash':receipt['publication_hash'],
                       'catalog_receipt_ref':'SYNTHETIC-CATALOG-ACK-NOT-REAL',
                       'event_contract':v2.PUBLISHED_CONTRACT,'publication_authorization_status':'allowed',
                       'recorded_event_id':RECORDED_ID,'recorded_changeset_id':receipt['changeset_id'],
                       'recorded_payload_hash':recorded['payload_hash']}
    published={**deepcopy(recorded),'event_id':PUBLISHED_ID,'event_type':'BookWorldPublished',
               'changeset_id':PUBLISHED_CHANGE,'payload':published_payload,
               'payload_hash':legacy._digest(published_payload),'outbox_status':'published','event_position':42}
    recorded_done={**deepcopy(recorded),'outbox_status':'published','event_position':41}
    snapshot={'schema':v2.READBACK_SCHEMA,'recorded_event':recorded_done,'published_event':published,
              'recorded_outbox_receipt':{'changeset_id':receipt['changeset_id'],'published_count':1,'event_positions':[41]},
              'published_outbox_receipt':{'changeset_id':PUBLISHED_CHANGE,'published_count':1,'event_positions':[42]},
              'publication_receipt':{'changeset_id':PUBLISHED_CHANGE,'version_ids':deepcopy(receipt['version_ids']),
                  'publication':'WORLD_VERIFIED','canon_activation_performed':False,'dry_run':False,'committed':True,
                  'receipt_contract':v2.PUBLICATION_RECEIPT_CONTRACT,'publication_authorization_status':'allowed'}}
    return packet,unbound,bound,items,receipt,recorded,mapped,snapshot


class VersionedBookDeliveryTests(unittest.TestCase):
    def setUp(self):
        (self.packet,self.unbound,self.oldbound,self.items,self.receipt,self.recorded,self.mapped,self.snapshot)=synthetic_fixture()
        self.context={'topic':legacy.TOPICS['E01'],'stage':'INITIAL_PLAN','dummy_exposed':True,
                      'opening_lead_made':True,'asks_for':'PRINCIPLE_EXPLANATION'}
        self.queries=[];self.deliveries=[];self.label='E01'

    def query(self,**kwargs):
        self.queries.append(deepcopy(kwargs));row=deepcopy(self.items[self.label])
        return {'contract_version':legacy.KNOWLEDGE_CONTRACT,'authority_lane':legacy.LANE,
                'system_profile':'SYSTEM_NEUTRAL','fallback_performed':False,'candidate_activation_performed':False,
                'count':1,'items':[row],'retrieval_status':'WORLD_MATCH'}

    def delivery(self,query):
        self.deliveries.append(deepcopy(query));return deepcopy(self.snapshot)

    def call(self,**overrides):
        kwargs={'query_knowledge':self.query,'render_teacher_book':render_teacher_book,
                'read_delivery':self.delivery,'test_only':True}
        kwargs.update(overrides)
        return v2.consume_card_v2(self.mapped,self.label,self.context,**kwargs)

    def gap(self,result):
        self.assertEqual(result['status'],'GAP');self.assertIsNone(result['answer'])
        self.assertEqual(result['citations'],[]);self.assertFalse(result['canon_activation_performed'])
        self.assertFalse(result['fallback_performed']);self.assertNotIn('book_response',result)

    def test_three_cards_actual_legacy_consumer_renderer_preserve_bounds_and_versions(self):
        for label in legacy.LABELS:
            self.label=label;self.context['topic']=legacy.TOPICS[label];self.queries=[];self.deliveries=[]
            before=deepcopy((self.mapped,self.context));result=self.call()
            self.assertEqual(result['status'],'EXPLANATION_READY')
            self.assertEqual(result['book_response'],render_teacher_book(self.items[label]))
            self.assertEqual(len(self.queries),1);self.assertEqual(len(self.deliveries),2)
            for text in result['book_response']['conditions']+result['book_response']['exceptions']:
                self.assertIn(text,result['explanation_with_page'])
            self.assertIsNone(next(c for c in self.mapped['cards'] if c['card_label']==label)['knowledge_item_id'])
            self.assertEqual(result['book_response']['knowledge_version_id'],self.items[label]['item_id'])
            self.assertEqual(before,(self.mapped,self.context));self.assertFalse(result['production_ready'])
            self.assertEqual(result['event_delivery_binding']['recorded_event']['event_position'],41)

    def test_v1_six_fields_do_not_upgrade_and_v1_consumer_rejects_v2_map(self):
        event=deepcopy(self.recorded);event['payload']=deepcopy(self.oldbound['observed_recorded_event_payload'])
        event['payload_hash']=legacy._digest(event['payload'])
        with self.assertRaises(v2.BookDeliveryBindingError):
            v2.bind_import_receipt_v2(self.unbound,self.receipt,event,expected_school_id=SCHOOL,test_only=True)
        with self.assertRaises(legacy.BookTeacherBindingError):
            legacy.bind_import_receipt(self.unbound,self.oldbound['observed_import_receipt'],self.recorded['payload'])
        result=legacy.consume_card(self.mapped,'E01',self.context,query_knowledge=self.query,render_teacher_book=render_teacher_book)
        self.assertEqual(result['reason'],'MAP_SCHEMA_MISMATCH');self.assertEqual(self.queries,[])

    def test_published_v1_producer_still_rejects_extra_v2_fields_before_SQL(self):
        path=Path(__file__).resolve().parents[1]/'database/book_world_persistence.py'
        node=next(n for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.FunctionDef) and n.name=='publish_book_world')
        namespace={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
        with self.assertRaisesRegex(ValueError,'DELIVERY_FIELDS'):
            namespace['publish_book_world'](None,school_id=SCHOOL,changeset_id=PUBLISHED_CHANGE,
                                           delivery_receipt=self.snapshot['published_event']['payload'])

    def test_actual_producer_decision_guard_preserves_blocked_unknown_and_missing(self):
        for status,state in [('blocked','BLOCKED_PENDING'),('unknown','UNKNOWN_PENDING'),(None,'UNKNOWN_PENDING')]:
            data=deepcopy(self.receipt)
            if status is None:data.pop('publication_authorization_status')
            else:data['publication_authorization_status']=status
            def forbidden(*a):raise AssertionError('publisher must not be called')
            result=publish_if_authorized('SYNTHETIC_UNUSED_NOT_A_DSN',data,forbidden)
            self.assertEqual(result['publication_state'],state);self.assertEqual(result['published_count'],0)
        calls=[]
        def synthetic_publisher(dsn,change):
            calls.append(change);return {'changeset_id':change,'published_count':1,'event_positions':[41]}
        result=publish_if_authorized('SYNTHETIC_UNUSED_NOT_A_DSN',self.receipt,synthetic_publisher)
        self.assertEqual(calls,[self.receipt['changeset_id']]);self.assertEqual(result['publication_state'],'PUBLISHED')

    def test_precommit_is_import_not_committed_and_generates_no_UUID(self):
        for receipt in (None,self.packet['expected_import_receipt']):
            with self.assertRaisesRegex(v2.BookDeliveryBindingError,'IMPORT_NOT_COMMITTED'):
                v2.bind_import_receipt_v2(self.unbound,receipt,None,expected_school_id=SCHOOL,test_only=True)
        result=v2.consume_card_v2(self.unbound,'E01',self.context,query_knowledge=self.query,
                                  render_teacher_book=render_teacher_book,read_delivery=self.delivery,test_only=True)
        self.assertEqual(result['reason'],'IMPORT_NOT_COMMITTED');self.assertEqual(self.queries+self.deliveries,[])
        self.assertTrue(all(c['knowledge_item_id'] is None and c['knowledge_version_id'] is None for c in self.unbound['cards']))

    def test_default_opt_in_and_unknown_or_cross_lane_tasks_do_no_IO(self):
        for kwargs in ({'test_only':False},{'test_only':1},{'authority_lane':'ACTIVE_SCHOOL_CANON'}):
            self.gap(self.call(**kwargs));self.assertEqual(self.queries+self.deliveries,[])
        self.label='FULL_CALL';self.gap(self.call());self.assertEqual(self.queries+self.deliveries,[])

    def test_blocked_or_unknown_recorded_authorization_never_reads_delivery_or_world(self):
        for status in ('blocked','unknown'):
            receipt=deepcopy(self.receipt);event=deepcopy(self.recorded)
            receipt['publication_authorization_status']=status;event['payload']['publication_authorization_status']=status
            event['payload_hash']=legacy._digest(event['payload'])
            mapped=v2.bind_import_receipt_v2(self.unbound,receipt,event,expected_school_id=SCHOOL,test_only=True)
            result=v2.consume_card_v2(mapped,'E01',self.context,query_knowledge=self.query,
                                     render_teacher_book=render_teacher_book,read_delivery=self.delivery,test_only=True)
            self.gap(result);self.assertEqual(self.queries+self.deliveries,[])

    def test_receipt_and_event_authorization_must_be_bound_without_defaults(self):
        for kind in ('receipt_contract','missing_status','changed_status','wrong_event_contract','aggregate_v2'):
            receipt=deepcopy(self.receipt);event=deepcopy(self.recorded)
            if kind=='receipt_contract':receipt['receipt_contract']='v1'
            if kind=='missing_status':event['payload'].pop('publication_authorization_status')
            if kind=='changed_status':receipt['publication_authorization_status']='blocked'
            if kind=='wrong_event_contract':event['payload']['event_contract']=v2.PUBLISHED_CONTRACT
            if kind=='aggregate_v2':event['aggregate_version']=2
            event['payload_hash']=legacy._digest(event['payload'])
            with self.assertRaises(v2.BookDeliveryBindingError):
                v2.bind_import_receipt_v2(self.unbound,receipt,event,expected_school_id=SCHOOL,test_only=True)

    def test_published_guard_not_allowed_withholds_even_if_receipt_claims_success(self):
        for status in ('blocked','unknown'):
            self.snapshot['published_event']['payload']['publication_authorization_status']=status
            self.snapshot['published_event']['payload_hash']=legacy._digest(self.snapshot['published_event']['payload'])
            self.gap(self.call());self.assertEqual(self.queries,[])

    def test_missing_delivery_reader_or_incomplete_snapshot_withholds(self):
        self.gap(self.call(read_delivery=None));self.assertEqual(self.queries,[])
        for key in tuple(self.snapshot):
            value=deepcopy(self.snapshot);value.pop(key)
            self.gap(self.call(read_delivery=lambda query:value));self.assertEqual(self.queries,[])

    def test_each_delivery_status_changeset_or_position_gap_withholds(self):
        original=deepcopy(self.snapshot)
        for kind in ('recorded_event','published_event'):
            for field,value in [('outbox_status','pending'),('changeset_status','started'),('event_position',None),('event_position',True)]:
                self.snapshot=deepcopy(original);self.snapshot[kind][field]=value
                self.gap(self.call());self.assertEqual(self.queries,[])

    def test_outbox_counts_positions_and_publication_receipt_are_exact(self):
        original=deepcopy(self.snapshot)
        for key,field,value in [('recorded_outbox_receipt','published_count',True),('published_outbox_receipt','event_positions',[41]),
                                ('publication_receipt','committed',False),('publication_receipt','publication','DRY_RUN_NOT_PUBLISHED'),
                                ('publication_receipt','receipt_contract','v1'),('publication_receipt','publication_authorization_status','unknown'),
                                ('publication_receipt','version_ids',list(reversed(self.receipt['version_ids'])))]:
            self.snapshot=deepcopy(original);self.snapshot[key][field]=value
            self.gap(self.call());self.assertEqual(self.queries,[])

    def test_school_source_event_or_payload_hash_mismatch_withholds(self):
        original=deepcopy(self.snapshot)
        for kind,field,value in [('recorded_event','event_id',PUBLISHED_ID),('recorded_event','school_id',PUBLISHED_CHANGE),
                                  ('published_event','payload_hash','0'*64),('published_event','event_id',RECORDED_ID),
                                  ('published_event','event_position',40)]:
            self.snapshot=deepcopy(original);self.snapshot[kind][field]=value
            self.gap(self.call());self.assertEqual(self.queries,[])

    def test_source_descriptor_and_version_ids_are_not_interchangeable(self):
        bad=deepcopy(self.recorded);bad['payload']['source_id']=PUBLISHED_ID;bad['payload_hash']=legacy._digest(bad['payload'])
        with self.assertRaises(v2.BookDeliveryBindingError):
            v2.bind_import_receipt_v2(self.unbound,self.receipt,bad,expected_school_id=SCHOOL,test_only=True)
        self.mapped['cards'][0]['knowledge_version_id']=self.packet['source']['registry_source_id']
        self.gap(self.call());self.assertEqual(self.queries+self.deliveries,[])

    def test_item_id_remains_version_id_and_knowledge_item_id_is_never_filled(self):
        result=self.call();self.assertEqual(result['book_response']['knowledge_version_id'],self.items['E01']['item_id'])
        self.assertTrue(all(c['knowledge_item_id'] is None for c in self.mapped['cards']))
        self.mapped['cards'][0]['knowledge_item_id']=self.items['E01']['item_id']
        self.gap(self.call())

    def test_staged_or_missing_world_cannot_use_delivery_as_substitute(self):
        for state in ('staged','missing'):
            def reader(**kwargs):
                value=self.query(**kwargs)
                if state=='staged':value['items'][0]['version_status']='staged'
                else:value.update(items=[],count=0,retrieval_status='WORLD_GAP')
                return value
            self.gap(self.call(query_knowledge=reader))

    def test_second_delivery_revocation_withholds_old_explanation(self):
        calls=[]
        def delivery(query):
            calls.append(1);value=deepcopy(self.snapshot)
            if len(calls)==2:value['published_event']['outbox_status']='pending'
            return value
        result=self.call(read_delivery=delivery);self.gap(result);self.assertEqual(len(self.queries),1)

    def test_same_aliased_snapshot_cannot_hide_changed_delivery(self):
        calls=[];shared=deepcopy(self.snapshot)
        def delivery(query):
            calls.append(1)
            if len(calls)==2:
                shared['published_event']['payload']['catalog_receipt_ref']='SYNTHETIC-OTHER-ACK'
                shared['published_event']['payload_hash']=legacy._digest(shared['published_event']['payload'])
            return shared
        result=self.call(read_delivery=delivery);self.gap(result);self.assertEqual(result['reason'],'V2_DELIVERY_CHANGED')

    def test_host_context_or_map_mutation_withholds(self):
        def delivery(query):self.context['asks_for']='SPECIFIC_CARD';return deepcopy(self.snapshot)
        result=self.call(read_delivery=delivery);self.gap(result);self.assertEqual(self.queries,[])
        self.context['asks_for']='PRINCIPLE_EXPLANATION'
        def query(**kwargs):self.mapped['source']['registry_source_id']=PUBLISHED_ID;return self.query(**kwargs)
        self.gap(self.call(query_knowledge=query))

    def test_delivery_reader_error_never_leaks_or_falls_back(self):
        def broken(query):raise RuntimeError('SYNTHETIC_PRIVATE_ERROR')
        result=self.call(read_delivery=broken);self.gap(result);self.assertNotIn('SYNTHETIC_PRIVATE',str(result))
        self.assertEqual(self.queries,[])

    def test_delivery_query_is_bounded_and_contains_no_hand_or_authorization_override(self):
        self.call()
        expected={'recorded_event_id':RECORDED_ID,'recorded_changeset_id':self.receipt['changeset_id'],
                  'publication_hash':self.receipt['publication_hash'],'source_id':self.packet['source']['registry_source_id'],
                  'expected_school_id':SCHOOL}
        self.assertEqual(self.deliveries,[expected,expected])
        self.assertNotIn('publication_authorization_status',expected)

    def test_hidden_or_unknown_context_fields_do_not_enter_consumer(self):
        self.context['hand']='SYNTHETIC_HIDDEN';self.gap(self.call());self.assertEqual(self.queries+self.deliveries,[])

    def test_other_same_content_run_cannot_supply_published_event(self):
        original=deepcopy(self.snapshot)
        for ref in ('recorded_event_id','recorded_changeset_id','recorded_payload_hash'):
            self.snapshot=deepcopy(original)
            payload=self.snapshot['published_event']['payload']
            payload[ref]='f'*64 if ref=='recorded_payload_hash' else '77777777-7777-4777-8777-777777777777'
            self.snapshot['published_event']['payload_hash']=legacy._digest(payload)
            result=self.call();self.gap(result)
            self.assertEqual(result['reason'],'PUBLISHED_RECORDED_CAUSAL_BINDING')
            self.assertEqual(self.queries,[])

    def test_changed_recorded_changeset_after_binding_refuses_before_IO(self):
        self.mapped['observed_recorded_event_v2']['changeset_id']=PUBLISHED_CHANGE
        result=self.call();self.gap(result)
        self.assertEqual(result['reason'],'V2_RECORDED_CHANGESET_BINDING')
        self.assertEqual(self.queries+self.deliveries,[])


if __name__=='__main__':unittest.main(verbosity=2)
