"""New refusal joins only: real ReadIssuer/PostgresStore/host, SQL transport doubles.

Existing canonical JSON is explicitly synthetic. It is used only to reach the
new authority boundary and prove ABSTAIN; no genuine phase/catalog/approval
record is invented and no authorization callback returns allowed. No PG engine.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from psycopg.rows import dict_row, tuple_row

from bridge_contracts.book_material import digest
from bridge_contracts.book_world import world_uuid
from bridge_school_api.book_teacher_receipts import BookReceiptBinding, BookReceiptRepository
from bridge_school_api.teacher_books_authority import compose_book_teacher_authority
from bridge_school_api.teacher_books_host import BookTeacherSession
from database.books_authority import Binding, Denied, Policy, ReadIssuer
from database.books_authority_postgres import PostgresStore, VALIDATION_SQL
from database.book_world_event_readback import COLUMNS

FIXTURE = Path(__file__).parent/'fixtures/book_world_canonical_v2.json'
R1_OBSERVATIONS = []


class Transport:
    """Returns metadata fixture rows and missing/revoked ledger only. No allowed."""
    def __init__(self, recorded, published):
        self.events = (recorded, published)
        self.connections = []
        self.metadata_reads = 0
        self.validation_reads = 0
        self.target_checks = 0
        self.snapshot = None
        self.observed_target = 'synthetic-primary-no-runtime'
        self.fail_validation = False

    def attest(self, connection, target):
        self.target_checks += 1
        assert connection.row_factory is dict_row, 'Attester must retain existing row semantics'
        return connection.observed_target == target

    def connect(self):
        owner = self
        class Cursor:
            rowcount = 1
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, params=None):
                self.rows = []
                if sql.startswith('SET LOCAL ') or sql.startswith('SELECT set_config('):
                    return
                if sql == VALIDATION_SQL:
                    owner.validation_reads += 1
                    if owner.fail_validation: raise RuntimeError('SYNTHETIC_SQL_TRANSPORT_FAILURE')
                    self.rows = [(deepcopy(owner.snapshot),)]
                    return
                assert sql.lstrip().startswith('SELECT') and 'e.event_id=%s' in sql
                owner.metadata_reads += 1
                self.rows = [tuple(deepcopy(event[k]) for k in COLUMNS)
                             for event in owner.events
                             if event['school_id'] == params[0] and event['event_id'] == params[1]]
            def fetchall(self):
                if self.row_factory is dict_row:
                    return [{'jsonb_build_object': row[0]} for row in self.rows]
                return self.rows
        class Connection:
            info = SimpleNamespace(transaction_status=0)
            autocommit = False
            read_only = False
            isolation_level = None
            def __init__(self):
                self.closed = 0; self.rollbacks = 0
                self.row_factory = dict_row
                self.observed_target = owner.observed_target
                self.transaction_readonly = None
            def __enter__(self): return self
            def __exit__(self, *args):
                if not self.closed: self.close()
                return False
            def cursor(self, **kwargs):
                cursor = Cursor()
                cursor.row_factory = kwargs.get('row_factory', self.row_factory)
                return cursor
            def rollback(self): self.rollbacks += 1
            def close(self): self.closed += 1
            @contextmanager
            def transaction(self):
                self.transaction_readonly = self.read_only
                assert self.read_only is True
                yield
        connection = Connection(); self.connections.append(connection)
        return connection


class AuthorityJoin(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).parent/'join-temp'
        parent.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='join-', dir=parent)
        self.addCleanup(self.tmp.cleanup)
        f = json.loads(FIXTURE.read_text('utf-8'))
        self.f = f
        self.recorded = dict(event_id=f['published_payload']['recorded_event_id'],
            event_type='BookWorldRecorded', aggregate_version=1, school_id=f['school_id'],
            changeset_id=f['import_receipt']['changeset_id'], partition_key='book-world',
            payload=deepcopy(f['recorded_payload']), payload_hash=digest(f['recorded_payload']),
            changeset_status='committed', outbox_status='published', event_position=1)
        self.published = dict(event_id=world_uuid('publish-event', f['school_id'], f['import_receipt']['changeset_id']),
            event_type='BookWorldPublished', aggregate_version=1, school_id=f['school_id'],
            changeset_id=f['publication_receipt']['changeset_id'], partition_key='book-world',
            payload=deepcopy(f['published_payload']), payload_hash=digest(f['published_payload']),
            changeset_status='committed', outbox_status='published', event_position=2)
        self.snapshot = dict(schema='book-world-delivery-readback-v2-proposal',
            recorded_event=self.recorded, published_event=self.published,
            publication_receipt=deepcopy(f['publication_receipt']),
            recorded_outbox_receipt=dict(changeset_id=self.recorded['changeset_id'],published_count=1,event_positions=[1]),
            published_outbox_receipt=dict(changeset_id=self.published['changeset_id'],published_count=1,event_positions=[2]))
        # Explicitly synthetic negative setup. No phase/catalog/approval success
        # evidence is made. These historical fixture fields cannot authorize.
        self.repository = BookReceiptRepository.create(Path(self.tmp.name)/'synthetic.sqlite')
        self.receipt_binding = self.repository.retain('synthetic-negative-join',
            source_packet=f['packet'], import_receipt=f['import_receipt'],
            snapshot=self.snapshot, expected_school_id=f['school_id'])
        self.transport = Transport(self.recorded, self.published)
        self.world_calls = []
        def world(**kwargs):
            self.world_calls.append(kwargs)
            raise AssertionError('WORLD MUST NOT BE REACHED WITHOUT REAL AUTHORITY')
        self.options = dict(repository=self.repository, receipt_binding=self.receipt_binding,
            connect=self.transport.connect, assert_target=self.transport.attest,
            policy=Policy('synthetic-policy-not-accepted',60,120,500,100),
            authority_binding=Binding('synthetic-executor','synthetic-primary-no-runtime',f['school_id']),
            validation_id='synthetic-missing-validation',
            retained={'import_receipt':deepcopy(f['import_receipt']), 'snapshot':deepcopy(self.snapshot)},
            query_knowledge=world)
        self.request=dict(schema='teacher-request-v04-test-v1',task_class='BOOK_PRINCIPLE_EXPLANATION',
            authority_lane='WORLD_EXTERNAL',system_profile='SYSTEM_NEUTRAL',card_label='E01',
            context=dict(topic='HAND_AND_DUMMY_TERMS',stage='INITIAL_PLAN',dummy_exposed=True,
                         opening_lead_made=True,asks_for='PRINCIPLE_EXPLANATION'))

    def compose(self, **changes):
        return compose_book_teacher_authority(**{**self.options, **changes})

    def abstain(self, result):
        self.assertEqual(result['status'],'ABSTAIN')
        for key in ('answer','decision','proposal','binding'): self.assertIsNone(result[key])
        self.assertEqual(result['knowledge_versions'],[])
        self.assertEqual(result['trace'],[])
        self.assertFalse(result['production_ready'])
        self.assertEqual(self.world_calls,[])

    def test_composition_binds_real_issuer_and_store_without_io_or_effect_api(self):
        session=self.compose()
        self.assertIs(type(session),BookTeacherSession)
        self.assertIs(type(session.authorization_provider),ReadIssuer)
        self.assertIsInstance(session.authorization_provider.store,PostgresStore)
        self.assertEqual(session.connect,session.authorization_provider.store.connect)
        self.assertFalse(hasattr(session.authorization_provider,'publish'))
        self.assertEqual(self.transport.connections,[])

    def test_no_policy_means_unbound_before_repository_or_connect(self):
        with patch.object(self.repository,'load',side_effect=AssertionError('NO LOAD')):
            self.abstain(self.compose(policy=None).answer(self.request,test_only=True))
        self.assertEqual(self.transport.connections,[])

    def test_invalid_policy_limits_and_untyped_policy_never_get_default_ttl(self):
        for value in ({},replace(self.options['policy'],acceptance_ref=''),
                      replace(self.options['policy'],validation_ttl_seconds=0),
                      replace(self.options['policy'],execution_ttl_seconds=float('inf'))):
            self.abstain(self.compose(policy=value).answer(self.request,test_only=True))
        self.assertEqual(self.transport.connections,[])

    def test_missing_host_inputs_are_unbound_without_receipt_synthesis(self):
        for key in self.options:
            self.assertIsNone(self.compose(**{key:None}).authorization_provider, key)
        self.assertEqual(self.transport.connections,[])

    def test_school_mismatch_and_empty_binding_fail_before_io(self):
        bad=replace(self.options['authority_binding'],school='00000000-0000-0000-0000-000000000001')
        self.assertIsNone(self.compose(authority_binding=bad).authorization_provider)
        self.assertIsNone(self.compose(authority_binding=replace(bad,target='')).authorization_provider)
        self.assertEqual(self.transport.connections,[])

    def test_no_authorization_callback_in_composition_interface(self):
        with self.assertRaises(TypeError):
            self.compose(authorization_provider=object())

    def test_retained_input_is_owned_copy_but_missing_phases_are_not_invented(self):
        session=self.compose(); retained=session.authorization_provider.retained
        self.options['retained']['snapshot']['recorded_event']['event_position']=99
        self.assertEqual(retained['snapshot']['recorded_event']['event_position'],1)
        self.assertNotIn('phases',retained)

    def test_actual_host_readback_and_sql_issuer_missing_ledger_abstain_before_world(self):
        self.abstain(self.compose().answer(self.request,test_only=True))
        self.assertEqual((self.transport.metadata_reads,self.transport.validation_reads),(2,1))
        self.assertEqual(len(self.transport.connections),2)
        self.assertTrue(all(c.read_only and c.closed for c in self.transport.connections))
        self.assertTrue(self.transport.connections[-1].transaction_readonly)

    def test_current_validation_revocation_abstains_without_fabricated_phase_records(self):
        self.transport.snapshot={'validation':{'kind':'validation','status':'revoked','revision':1,'body':{}}}
        self.abstain(self.compose().answer(self.request,test_only=True))
        self.assertEqual(self.transport.validation_reads,1)

    def test_existing_dict_row_connector_scalar_snapshot_is_adapted_and_restored(self):
        self.transport.snapshot={'negative_transport_probe':'not-an-authority-record'}
        issuer=self.compose().authorization_provider
        # Scalar row transport is below the mandatory new-profile snapshot gate.
        # This marker is intentionally not accepted as an authority snapshot.
        with issuer.store.transaction(issuer.policy,readonly=True) as tx:
            value=tx.one(VALIDATION_SQL,(issuer.validation_id,)*3)
        self.assertEqual(value,self.transport.snapshot)
        self.assertEqual(self.transport.target_checks,1)
        self.assertIs(self.transport.connections[-1].row_factory,dict_row)
        self.assertEqual(self.world_calls,[])

    def test_read_only_composition_store_cannot_start_execution_transaction(self):
        issuer=self.compose().authorization_provider
        with self.assertRaisesRegex(Denied,'READ_ONLY'):
            with issuer.store.transaction(issuer.policy): pass
        self.assertEqual(self.transport.connections,[])

    def test_new_issuer_snapshot_on_each_call_no_cache_or_historical_allowed_fallback(self):
        issuer=self.compose().authorization_provider
        for _ in range(2):
            with self.assertRaises(Denied): issuer({'recorded_event':deepcopy(self.recorded)})
        self.assertEqual(self.transport.validation_reads,2)
        self.assertEqual(len(self.transport.connections),2)

    def test_target_attestation_failure_and_sql_failure_never_reach_world(self):
        self.transport.observed_target='synthetic-other-target'
        self.abstain(self.compose().answer(self.request,test_only=True))
        self.assertEqual(self.transport.validation_reads,0)
        self.transport.observed_target='synthetic-primary-no-runtime'
        self.transport.fail_validation=True
        result=self.compose().answer(self.request,test_only=True)
        self.abstain(result)
        self.assertNotIn('SYNTHETIC_SQL_TRANSPORT_FAILURE',str(result))

    def test_request_cannot_configure_authority_and_test_only_remains_required(self):
        session=self.compose()
        self.abstain(session.answer({**self.request,'validation_id':'forged'},test_only=True))
        self.abstain(session.answer(self.request))
        self.assertEqual(self.transport.connections,[])

    def test_new_profile_missing_or_empty_state_ids_cannot_reach_world_via_legacy(self):
        # Reuse the owner's explicitly synthetic LEGACY read fixture. No test
        # authorization callback and no genuine evidence are manufactured.
        # Only this method runs; importing its builder runs none of its suites.
        from test_books_authority_local import read_fixture
        for variant in ('missing','empty'):
            with self.subTest(state_ids=variant):
                state, legacy_fixture = read_fixture()
                retained = deepcopy(legacy_fixture.retained)
                retained['snapshot'] = deepcopy(self.snapshot)
                state.objects['v']['body']['retained_hash'] = digest(retained)
                rules = state.objects['g']['body']['proof_predicates']
                rules.pop('state_ids',None)
                if variant == 'empty': rules['state_ids'] = []
                self.transport.snapshot = state.validation_snapshot('v',legacy_fixture.policy)
                self.transport.observed_target = state.target
                self.world_calls.clear()
                self.transport.metadata_reads = 0
                self.transport.validation_reads = 0
                session = self.compose(policy=legacy_fixture.policy,
                    authority_binding=legacy_fixture.binding,
                    validation_id='v',retained=retained)
                result = session.answer(self.request,test_only=True)
                R1_OBSERVATIONS.append(dict(state_ids=variant,
                    status=result['status'],world_calls=len(self.world_calls),
                    metadata_reads=self.transport.metadata_reads,
                    fresh_validation_reads=self.transport.validation_reads))
                # Before the strict gate, actual ReadIssuer validated this
                # legacy snapshot and reached WORLD. The spy stops the read;
                # after the fix it must remain untouched for BOTH variants.
                self.assertEqual(result['status'],'ABSTAIN')
                self.assertEqual((self.transport.metadata_reads,
                                  self.transport.validation_reads),(2,1))
                self.assertEqual(self.world_calls,[],
                    'NEW_PROFILE_LEGACY_FALLBACK_REACHED_WORLD: '+variant)
                self.abstain(result)


if __name__ == '__main__': unittest.main()
