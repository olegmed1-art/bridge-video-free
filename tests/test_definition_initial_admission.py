"""New successor tests only. All identities, evidence and stores are SOURCE_ONLY."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from threading import Thread
from unittest import TestCase, main
from unittest.mock import patch
import hashlib
import json
from database.definition_initial_admission import DefinitionInitialAdmission
from database.definition_initial_postgres import DefinitionInitialPostgres, epoch_us
from database.definition_contract_postgres import DefinitionTargetPin, _OWNED, _LOCK
from database.definition_contract_store import StoreDenied, OutcomeUnknown
from definition_initial_fixtures import proposal, MemoryInitial, NOW, SCOPE, BUNDLE, POST_BUNDLE, sha
from bridge_contracts.video_canon_ai_promotion import REQUIRED_CHECKS, STATE_DEPENDENT_CHECKS


class InitialTests(TestCase):
    def setUp(self):
        self.p = proposal(); self.b = MemoryInitial(self.p)
        self.service = DefinitionInitialAdmission(backend=self.b, scope=SCOPE, clock=lambda: self.b.now_value)

    def admit(self, **kwargs):
        return self.service.admit(self.p, verification_bundle_sha256=kwargs.get('bundle', BUNDLE),
            request_key=kwargs.get('key', 'SOURCE_ONLY:command'))

    def read(self, bundle=POST_BUNDLE):
        return self.service.read_accepted(self.p, verification_bundle_sha256=bundle, request_key='SOURCE_ONLY:command')

    def denied(self, reply):
        self.assertEqual(reply['status'], 'ABSTAIN', reply)
        self.assertIsNone(reply['teacher_packet']); self.assertIsNone(reply['accepted_selectors'])
        self.assertEqual(reply['genuine_Canon_checks_created'], 0)

    def committed(self):
        reply = self.admit(); self.assertEqual(reply['status'], 'SOURCE_ONLY_ADMISSION_EXERCISE', reply)
        self.assertEqual(self.b.writes, 4); self.b.post_bundle()

    def test_01_positive_initial_to_current_read_is_synthetic_only(self):
        self.committed(); reply = self.read()
        self.assertEqual(reply['status'], 'SOURCE_ONLY_ACCEPTED_READ_EXERCISE', reply)
        self.assertTrue(reply['source_only']); self.assertFalse(reply['ready'])
        self.assertIsNone(reply['accepted_selectors']); self.assertIsNone(reply['teacher_packet'])
        self.assertEqual(self.b.record['version']['content'], self.p['knowledge_version']['content'])

    def test_02_default_and_request_auth_claims_cannot_bind_provider(self):
        self.denied(DefinitionInitialAdmission(scope=SCOPE).admit(self.p, verification_bundle_sha256=BUNDLE, request_key='x'))
        self.denied(DefinitionInitialAdmission(scope=SCOPE).read_accepted(self.p, verification_bundle_sha256=BUNDLE, request_key='x'))
        for key in ('authenticated', 'principal', 'accepted', 'source_only'):
            p = deepcopy(self.p); p[key] = True
            self.denied(self.service.admit(p, verification_bundle_sha256=BUNDLE, request_key='x'))
        self.assertEqual(self.b.writes, 0)

    def test_03_each_missing_or_failed_gate_blocks_before_any_write(self):
        cases = 0
        for check in sorted(REQUIRED_CHECKS):
            for mode in ('missing', 'FAIL'):
                self.setUp(); body = self.b.bundles[BUNDLE]
                if mode == 'missing':
                    body['checks'] = [r for r in body['checks'] if r['check_id'] != check]
                else:
                    next(r for r in body['checks'] if r['check_id'] == check)['result'] = mode
                self.denied(self.admit()); self.assertEqual(self.b.writes, 0); cases += 1
        self.assertEqual(cases, 32)

    def test_04_low_confidence_owner_login_alias_and_wrong_scope_refuse(self):
        mutations = [lambda b: b.update(semantic_confidence=0.949),
            lambda b: b['scope'].update(activation_scope='SOURCE_ONLY:foreign'),
            lambda b: b.update(verification_bundle_sha256='0'*64),
            lambda b: b['checks'][0].update(candidate_payload_hash='0'*64),
            lambda b: b['memberships'][0].update(is_member=1),
            lambda b: b['registry'][0].update(status='revoked')]
        for mutate in mutations:
            self.setUp(); mutate(self.b.bundles[BUNDLE]); self.denied(self.admit()); self.assertEqual(self.b.writes, 0)
        self.setUp(); body = self.b.bundles[BUNDLE]
        for row in body['checks']:
            row['execution_principal'] = 'SOURCE_ONLY:owner'
        for m in body['memberships']:
            m['execution_principal'] = 'SOURCE_ONLY:owner'
        self.denied(self.admit()); self.assertEqual(self.b.writes, 0)

    def test_05_exact_retry_does_not_rewrite_and_changed_key_or_effect_refuses(self):
        self.committed(); before = deepcopy(self.b.record)
        again = self.admit(); self.assertEqual(again['reason'], 'ALREADY_COMMITTED_REVALIDATION_REQUIRED', again)
        self.assertEqual(self.b.writes, 4); self.assertEqual(before, self.b.record)
        self.denied(self.admit(key='SOURCE_ONLY:different'))
        self.denied(self.admit(bundle=POST_BUNDLE)); self.assertEqual(self.b.writes, 4)

    def test_06_stale_origin_is_not_current_after_own_write(self):
        self.committed(); self.denied(self.read(BUNDLE))
        self.assertEqual(self.read()['status'], 'SOURCE_ONLY_ACCEPTED_READ_EXERCISE')
        for check in sorted(STATE_DEPENDENT_CHECKS):
            self.b.post_bundle()
            row = next(r for r in self.b.bundles[POST_BUNDLE]['checks'] if r['check_id'] == check)
            row['canon_snapshot_sha256'] = self.b.bundles[BUNDLE]['canon_snapshot_sha256']
            self.denied(self.read())

    def test_07_partial_writes_and_exit_failure_roll_back_atomic_rows(self):
        for step in range(1, 5):
            self.setUp(); self.b.fail_step = step; self.denied(self.admit())
            self.assertIsNone(self.b.record); self.assertEqual(self.b.writes, 0)
        self.setUp(); self.b.fail_exit = True; self.denied(self.admit())
        self.assertIsNone(self.b.record); self.assertEqual(self.b.writes, 0)

    def test_08_lost_ack_is_unknown_then_exact_history_reconciliation_no_rewrite(self):
        self.b.lose_ack = True; reply = self.admit()
        self.assertEqual(reply['status'], 'OUTCOME_UNKNOWN'); self.assertIsNotNone(self.b.record)
        self.b.lose_ack = False
        self.assertEqual(self.admit()['reason'], 'ALREADY_COMMITTED_REVALIDATION_REQUIRED')
        self.assertEqual(self.b.writes, 4)

    def test_09_revoke_conflict_and_expiry_each_block_current_read(self):
        for mutation in ('source', 'reader', 'origin', 'conflict', 'expired', 'ki', 'kv', 'activation'):
            self.setUp(); self.committed()
            if mutation == 'source': self.b.live_source = False
            elif mutation == 'reader': self.b.read_authorized = False
            elif mutation == 'origin': self.b.origin_authorized = False
            elif mutation == 'conflict': self.b.conflicted = True
            elif mutation == 'expired': self.b.now_value += timedelta(hours=2)
            elif mutation == 'ki': self.b.record['item']['status'] = 'retired'
            elif mutation == 'kv': self.b.record['version']['status'] = 'candidate'
            elif mutation == 'activation': self.b.record['activation']['status'] = 'revoked'
            self.denied(self.read()); self.assertEqual(self.b.writes, 4)

    def test_10_approval_json_forgery_and_native_row_mismatch_refuse(self):
        mutations = [lambda r: r['activation']['approval_provenance'].update(effect_sha256='0'*64),
            lambda r: r['activation']['approval_provenance'].update(origin_projection_sha256='0'*64),
            lambda r: r['activation']['approval_provenance'].update(actor='SOURCE_ONLY:other'),
            lambda r: r['version'].update(content={'forged': 'approved source'}),
            lambda r: r['source_link'].update(source_id='SOURCE_ONLY:foreign'),
            lambda r: r['activation'].update(scope_key='SOURCE_ONLY:other'),
            lambda r: r['activation']['approval_provenance'].update(accepted=True)]
        for mutate in mutations:
            self.setUp(); self.committed(); mutate(self.b.record); self.denied(self.read())

    def test_11_mid_lease_revoke_input_tamper_and_suppression_cannot_succeed(self):
        self.b.on_exit = lambda: setattr(self.b, 'write_authorized', False)
        self.denied(self.admit()); self.assertIsNone(self.b.record)
        self.setUp(); self.b.suppress = True; self.b.fail_step = 2
        self.denied(self.admit()); self.assertIsNone(self.b.record)
        self.setUp(); self.committed()
        self.b.on_exit = lambda: self.p['knowledge_item'].update(title='SOURCE_ONLY:changed')
        self.denied(self.read())

    def test_12_preexisting_item_is_not_initial_and_parallel_retry_is_single_effect(self):
        self.committed(); self.b.record['version']['version_no'] = 2
        self.denied(self.admit()); self.assertEqual(self.b.writes, 4)
        self.setUp(); replies = []
        threads = [Thread(target=lambda: replies.append(self.admit())) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=3); self.assertFalse(t.is_alive())
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(r['status'] == 'SOURCE_ONLY_ADMISSION_EXERCISE' for r in replies))
        self.assertEqual(self.b.writes, 4)

    def test_13_wrong_version_numeric_domain_and_profile_are_not_repurposed(self):
        for kind in ('version', 'domain', 'profile', 'id'):
            self.setUp()
            if kind == 'version': self.p['knowledge_version']['version_no'] = True
            if kind == 'domain': self.p['knowledge_version']['content']['definition_evidence']['knowledge_domain'] = 'BIDDING'
            if kind == 'profile': self.p['knowledge_version']['bidding_system_key'] = 'SOURCE_ONLY:foreign'
            if kind == 'id': self.p['knowledge_item']['knowledge_item_id'] = 'SOURCE_ONLY:self-declared'
            self.denied(self.admit()); self.assertEqual(self.b.writes, 0)

    def test_17_observed_mode_flag_is_not_a_native_provider(self):
        self.b.mode = 'OBSERVED'
        self.denied(self.admit()); self.assertEqual(self.b.writes, 0)
        self.denied(self.read())

    def test_18_review_type_alias_retry_source_and_exact_epoch_regressions(self):
        for mutate in (
            lambda r: r['version']['content']['definition_evidence'].update(requires_52_cards=0),
            lambda r: r['version']['provenance'].update(review_revision=True),
            lambda r: r['source_link']['source_locator'].update(position=True),
        ):
            self.setUp()
            self.p['knowledge_version']['provenance']['review_revision'] = 1
            self.p['knowledge_version_source']['source_locator']['position'] = 1
            self.b = MemoryInitial(self.p)
            self.service = DefinitionInitialAdmission(backend=self.b, scope=SCOPE, clock=lambda: self.b.now_value)
            self.committed(); mutate(self.b.record); self.denied(self.read())
        self.setUp(); self.committed(); self.b.record['source_link']['source_id'] = 'SOURCE_ONLY:foreign'
        self.denied(self.admit()); self.assertEqual(self.b.writes, 4)
        self.setUp(); self.b.bundles[BUNDLE]['source_binding']['interval_seconds'][0] = True
        self.denied(self.admit()); self.assertEqual(self.b.writes, 0)
        import calendar
        for micros in (0, 1, 123457, 999999):
            value = NOW.replace(microsecond=micros)
            self.assertEqual(epoch_us(value), calendar.timegm(value.utctimetuple()) * 1000000 + micros)


class FakeConn:
    """DB-API protocol double only. Never connects or executes SQL."""
    def __init__(self):
        self.info = type('Info', (), {'transaction_status': 0})()
        self.root = (91, '100'); self.closed = False; self.rolled_back = False
        self.commit_error = False; self.close_error = False; self.sent = []
    def execute(self, sql): self.sent.append(sql)
    def commit(self):
        if self.commit_error: raise OSError('SOURCE_ONLY:ACK lost')
    def rollback(self): self.rolled_back = True
    def close(self):
        if self.close_error: raise OSError('SOURCE_ONLY:close unknown')
        self.closed = True


class ProtocolTests(TestCase):
    def setup_backend(self):
        conn = FakeConn(); scope = dict(school='00000000-0000-0000-0000-000000000001',
            profile=SCOPE['system_profile'], level=SCOPE['learner_level'], scope=SCOPE['activation_scope'])
        target = DefinitionTargetPin('SOURCE_ONLY:db', '1', '2', 180006, 'SOURCE_ONLY:promoter',
            'SOURCE_ONLY:promoter', 'SOURCE_ONLY:lease', sha('SOURCE_ONLY:candidate'), scope)
        class Context:
            def authorize(self, c, req, **kw): return target.session_user
            def evidence(self, *a, **kw): raise AssertionError('Unused in native protocol-only probe')
            def fence(self, c): return None
        context = Context()
        class Provider:
            @contextmanager
            def lease(self, *a, **kw): yield context
        class Backend(DefinitionInitialPostgres):
            def _attest(self, c, readonly, root=None):
                if root is not None and c.root != root: raise StoreDenied('SOURCE_ONLY:ROOT_CHANGED')
                return c.root
        backend = Backend(connection_provider=lambda: conn, context_provider=Provider(), target=target)
        req = dict(operation='INITIAL_DEFINITION_ACCEPT', candidate_payload_hash=target.candidate_hash,
            scope=deepcopy(SCOPE), stable_key='SOURCE_ONLY:skill', verification_bundle_sha256=BUNDLE, request_key='SOURCE_ONLY:k')
        def one(c, sql, args=()):
            if 'stable_name' in sql: return {'stable_name': SCOPE['school_key']}
            if 'rolcanlogin' in sql: return {'rolcanlogin': True, 'member': True}
            return {'locked': None}
        return backend, req, conn, context, one

    def test_14_native_unbound_does_not_connect_and_no_read_lease_write(self):
        req = dict(operation='INITIAL_DEFINITION_ACCEPT')
        with self.assertRaises(StoreDenied):
            with DefinitionInitialPostgres().transaction(req): pass
        backend, req, conn, ctx, fake = self.setup_backend()
        with patch('database.definition_initial_postgres.one', fake):
            with self.assertRaises(StoreDenied):
                with backend.observer(req): pass
        self.assertEqual(conn.sent, [])

    def test_15_native_root_loss_and_ack_loss_are_unknown(self):
        for fault in ('root', 'commit', 'close'):
            backend, req, conn, ctx, fake = self.setup_backend()
            with patch('database.definition_initial_postgres.one', fake):
                with self.assertRaises(OutcomeUnknown):
                    with backend.transaction(req) as tx:
                        if fault == 'root': conn.root = (92, '101')
                        if fault == 'commit': conn.commit_error = True
                        if fault == 'close': conn.close_error = True
                self.assertFalse(tx.finished)
            if fault == 'close':
                with _LOCK: _OWNED.pop(id(conn), None)  # Remove this isolated fake only.

    def test_16_native_fence_bool_is_not_authority_and_body_fault_rolls_back(self):
        for fault in ('fence', 'body'):
            backend, req, conn, ctx, fake = self.setup_backend()
            if fault == 'fence': ctx.fence = lambda c: True
            with patch('database.definition_initial_postgres.one', fake):
                with self.assertRaises((StoreDenied, ValueError)):
                    with backend.transaction(req) as tx:
                        if fault == 'body': raise ValueError('SOURCE_ONLY:failure')
                self.assertTrue(conn.rolled_back); self.assertTrue(conn.closed); self.assertFalse(tx.finished)


if __name__ == '__main__':
    main()
