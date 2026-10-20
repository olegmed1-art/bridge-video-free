"""Synthetic stdlib contracts. No production evidence, credentials or I/O ports."""
import base64
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / 'ops' / 'light_native_continuity.py'
SPEC = importlib.util.spec_from_file_location('continuity_under_test', MODULE_PATH)
c = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = c
SPEC.loader.exec_module(c)

RELEASE = '/synthetic/releases/' + '1' * 40
DROP = '/synthetic/unit.d/reviewed.conf'


def drop(admission, variant='WITH_BROKER_ENV'):
    text = '[Service]\nWorkingDirectory=' + RELEASE + '\nEnvironment=AUTOPILOT_ADMISSION_MODE=' + admission + '\n'
    if variant == 'WITH_BROKER_ENV':
        text += 'EnvironmentFile=' + RELEASE + '/ops/autopilot/broker-hold.env\n'
    return text.encode()


def changes(before, after):
    flat_before = {**{key: before[key] for key in before if key != 'identity'},
                   **{'identity.' + key: value for key, value in before['identity'].items()}}
    flat_after = {**{key: after[key] for key in after if key != 'identity'},
                  **{'identity.' + key: value for key, value in after['identity'].items()}}
    return [dict(field=key, before=flat_before[key], after=flat_after[key])
            for key in sorted(flat_before) if flat_before[key] != flat_after[key]]


class Fixture:
    def __init__(self, variant='WITH_BROKER_ENV'):
        self.variant = variant
        inventory = {DROP: dict(mode=0o644, limit=4096, sha256=c.sha256(drop('HOLD', variant))),
                     '/synthetic/environment': dict(mode=0o600, limit=65536, sha256='a' * 64),
                     '/synthetic/unit': dict(mode=0o644, limit=65536, sha256='b' * 64)}
        self.first = dict(identity=dict(hostname='synthetic-host', pid=101, invocation_id='1' * 32,
                                       release=RELEASE, fingerprint='c' * 64),
                          state='RUNNING', admission='HOLD', protected=inventory,
                          protected_sha256=c.sha256(c.canonical(inventory)))
        self.baseline = dict(version=1, source='2' * 40, package_sha256='3' * 64,
                             agreement_sha256='4' * 64, scope_sha256='5' * 64,
                             prior=deepcopy(self.first['identity']), protected=deepcopy(inventory),
                             protected_sha256=self.first['protected_sha256'], observed_at=90)
        self.sources, self.receipts = {}, []
        self.previous_source = self.source(self.first, 100)
        current = deepcopy(self.first)
        for number, kind in enumerate(('STOP_HOLD', 'START_HOLD', 'STOP_HOLD', 'ACTIVATE_LEGACY')):
            following = deepcopy(current)
            running = kind != 'STOP_HOLD'
            following['state'] = 'RUNNING' if running else 'STOPPED'
            following['identity'].update(pid=102 + number if running else 0,
                                         invocation_id=str(number + 2) * 32 if running else '',
                                         fingerprint=str(number + 6) * 64)
            witness = None
            if kind == 'ACTIVATE_LEGACY':
                following['admission'] = 'ACTIVE'
                following['protected'][DROP]['sha256'] = c.sha256(drop('ACTIVE', variant))
                following['protected_sha256'] = c.sha256(c.canonical(following['protected']))
                witness = dict(before_base64=base64.b64encode(drop('HOLD', variant)).decode(),
                               after_base64=base64.b64encode(drop('ACTIVE', variant)).decode())
            self.append(kind, current, following, 110 + number * 10, witness)
            current = following
        self.current_raw = self.sources[self.source(current, 150)]

    def source(self, snapshot, timestamp):
        raw = c.canonical(dict(version=2, controller_source='d' * 40,
                               controller_package_sha256='e' * 64, installed_release=RELEASE,
                               observed_at=timestamp, snapshot=deepcopy(snapshot)))
        digest = c.sha256(raw)
        self.sources[digest] = raw
        return digest

    def append(self, kind, before, after, timestamp, witness):
        next_source = self.source(after, timestamp)
        self.receipts.append(dict(version=2, kind=kind, sequence=len(self.receipts),
                                  previous_receipt_sha256=c.sha256(c.canonical(self.receipts[-1])) if self.receipts else None,
                                  before_source_sha256=self.previous_source, after_source_sha256=next_source,
                                  before=deepcopy(before), after=deepcopy(after),
                                  changes=changes(before, after), witness=witness))
        self.previous_source = next_source

    def arguments(self):
        baseline_raw = c.canonical(self.baseline)
        receipt_raw = [c.canonical(value) for value in self.receipts]
        manifest = dict(version=2, purpose='LEGACY_CONTINUITY_ONLY', baseline_sha256=c.sha256(baseline_raw),
                        journal_inventory_sha256='f' * 64, current_sha256=c.sha256(self.current_raw),
                        validator_source='a' * 40, validator_sha256='b' * 64,
                        accepted_at=150, expires_at=210, drop_name=DROP, drop_variant=self.variant,
                        receipts=[c.sha256(raw) for raw in receipt_raw],
                        sources={digest: {key: json.loads(raw)[key] for key in
                                           ('controller_source', 'controller_package_sha256')}
                                 for digest, raw in self.sources.items()})
        manifest_raw = c.canonical(manifest)
        return dict(baseline_raw=baseline_raw, current_raw=self.current_raw, receipts=receipt_raw,
                    source_records=deepcopy(self.sources), manifest_raw=manifest_raw,
                    accepted_manifest_sha256=c.sha256(manifest_raw), baseline_sha256=c.sha256(baseline_raw),
                    journal_inventory_sha256='f' * 64, validator_source='a' * 40,
                    validator_sha256='b' * 64, now=151)


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.args = self.fixture.arguments()

    def unknown(self, args=None):
        with self.assertRaises(c.ContinuityUnknown) as raised:
            c.verify_continuity_v2(**(args or self.args))
        self.assertTrue(str(raised.exception).startswith('CONTINUITY_UNKNOWN_'))

    def reaccept_manifest(self, mutate):
        value = json.loads(self.args['manifest_raw'])
        mutate(value)
        self.args['manifest_raw'] = c.canonical(value)
        self.args['accepted_manifest_sha256'] = c.sha256(self.args['manifest_raw'])

    def test_connected_actual_record_relation_and_active_evidence_gate(self):
        proof = c.verify_continuity_v2(**self.args)
        self.assertEqual(proof.assurance, 'CONTINUITY_ONLY')
        self.assertEqual(proof.current_admission, 'ACTIVE')
        self.assertEqual(len(proof.receipt_sha256), 4)
        accepted = c.require_evidence_gate_v2(proof, purpose='RETIREMENT_EVIDENCE', now=151,
            **{key: getattr(proof, key) for key in ('baseline_sha256', 'current_sha256', 'manifest_sha256', 'journal_inventory_sha256')})
        self.assertIs(accepted, proof)
        with self.assertRaises(Exception):
            proof.current_admission = 'HOLD'

    def test_both_reviewed_drop_templates(self):
        self.assertEqual(c.verify_continuity_v2(**Fixture('BASIC').arguments()).current_admission, 'ACTIVE')

    def test_canonical_encoding_and_duplicate_keys(self):
        for raw in (self.args['baseline_raw'] + b'\n', b'{"version":1,"version":1}', b'{"x":NaN}', b'{"x":1.0}', b'\xff'):
            with self.subTest(raw=raw[:25]):
                args = deepcopy(self.args)
                args['baseline_raw'] = raw
                args['baseline_sha256'] = c.sha256(raw)
                manifest = json.loads(args['manifest_raw'])
                manifest['baseline_sha256'] = args['baseline_sha256']
                args['manifest_raw'] = c.canonical(manifest)
                args['accepted_manifest_sha256'] = c.sha256(args['manifest_raw'])
                self.unknown(args)

    def test_external_acceptance_and_validator_pins(self):
        for key in ('accepted_manifest_sha256', 'baseline_sha256', 'journal_inventory_sha256', 'validator_sha256', 'validator_source'):
            with self.subTest(key=key):
                args = deepcopy(self.args)
                args[key] = '0' * len(args[key])
                self.unknown(args)

    def test_hash_computed_from_unaccepted_ack_is_insufficient(self):
        self.args['manifest_raw'] = self.args['manifest_raw'].replace(b'LEGACY_CONTINUITY_ONLY', b'NATIVE_ADMISSION')
        self.unknown()

    def test_missing_chain_and_equal_current_files_are_unknown(self):
        self.args['receipts'] = []
        self.reaccept_manifest(lambda m: m.update(receipts=[]))
        self.unknown()

    def test_gap_and_wrong_sequence_rejected_even_when_reaccepted(self):
        for field, value in (('sequence', 2), ('previous_receipt_sha256', '1' * 64), ('kind', 'CONFIG_CHANGE')):
            with self.subTest(field=field):
                fixture = Fixture()
                fixture.receipts[0][field] = value
                self.unknown(fixture.arguments())

    def test_unlisted_noop_reordered_or_wildcard_change(self):
        original = deepcopy(self.fixture.receipts[0]['changes'])
        for value in ([], list(reversed(original)), original + [dict(field='*', before=None, after=None)],
                      original + [dict(field='identity.hostname', before='synthetic-host', after='synthetic-host')]):
            with self.subTest(value=value):
                fixture = Fixture()
                fixture.receipts[0]['changes'] = value
                self.unknown(fixture.arguments())

    def test_source_byte_hash_and_source_provenance_pins(self):
        digest = next(iter(self.args['source_records']))
        self.args['source_records'][digest] += b' '
        self.unknown()
        self.args = self.fixture.arguments()
        self.reaccept_manifest(lambda m: m['sources'][digest].update(controller_source='0' * 40))
        self.unknown()

    def test_missing_source_or_extra_source_inventory(self):
        self.args['source_records'].pop(next(iter(self.args['source_records'])))
        self.unknown()
        self.args = self.fixture.arguments()
        self.args['source_records']['0' * 64] = b'{}'
        self.unknown()

    def test_freshness_expiry_future_time_and_boolean_time(self):
        for now in (89, 149, 211, True):
            with self.subTest(now=now):
                args = deepcopy(self.args)
                args['now'] = now
                self.unknown(args)
        self.args['max_current_age_seconds'] = 301
        self.unknown()

    def test_current_record_requires_fresh_exact_chain_endpoint(self):
        current = json.loads(self.fixture.current_raw)
        current['snapshot']['identity']['pid'] += 1
        self.fixture.current_raw = self.fixture.sources[self.fixture.source(current['snapshot'], 150)]
        self.unknown(self.fixture.arguments())

    def test_endpoint_source_snapshot_cannot_be_a_receipt_only_claim(self):
        self.fixture.receipts[-1]['after']['identity']['pid'] = 999
        self.fixture.receipts[-1]['changes'] = changes(self.fixture.receipts[-1]['before'], self.fixture.receipts[-1]['after'])
        self.unknown(self.fixture.arguments())

    def test_host_release_and_non_drop_configuration_are_invariant(self):
        for section, key, value in (('identity', 'hostname', 'other-host'), ('identity', 'release', '/synthetic/other'),
                                     ('protected', '/synthetic/environment', dict(mode=0o600, limit=65536, sha256='0' * 64))):
            with self.subTest(key=key):
                receipt = deepcopy(self.fixture.receipts[-1])
                receipt['after'][section][key] = value
                receipt['after']['protected_sha256'] = c.sha256(c.canonical(receipt['after']['protected']))
                receipt['changes'] = changes(receipt['before'], receipt['after'])
                with self.assertRaises(c.ContinuityUnknown):
                    c._transition(receipt, DROP, self.fixture.variant)

    def test_activation_has_exact_typed_process_transition(self):
        for part, value in (('state', 'RUNNING'), ('admission', 'ACTIVE')):
            receipt = deepcopy(self.fixture.receipts[-1])
            receipt['before'][part] = value
            receipt['before']['identity']['pid'] = 123
            receipt['before']['identity']['invocation_id'] = '1' * 32
            receipt['changes'] = changes(receipt['before'], receipt['after'])
            with self.subTest(part=part), self.assertRaises(c.ContinuityUnknown):
                c._transition(receipt, DROP, self.fixture.variant)

    def test_activation_drop_bytes_reject_arbitrary_commands_and_metadata(self):
        for suffix in (b'ExecStart=/synthetic/command\n', b'Environment=EXTRA=1\n', b'\n'):
            receipt = deepcopy(self.fixture.receipts[-1])
            raw = drop('ACTIVE') + suffix
            receipt['witness']['after_base64'] = base64.b64encode(raw).decode()
            receipt['after']['protected'][DROP]['sha256'] = c.sha256(raw)
            receipt['after']['protected_sha256'] = c.sha256(c.canonical(receipt['after']['protected']))
            receipt['changes'] = changes(receipt['before'], receipt['after'])
            with self.subTest(suffix=suffix), self.assertRaises(c.ContinuityUnknown):
                c._transition(receipt, DROP, self.fixture.variant)

    def test_duplicate_receipts_and_journal_drift(self):
        self.args['receipts'][1] = self.args['receipts'][0]
        self.reaccept_manifest(lambda m: m['receipts'].__setitem__(1, m['receipts'][0]))
        self.unknown()
        self.args = self.fixture.arguments()
        self.args['journal_inventory_sha256'] = '0' * 64
        self.unknown()

    def test_reload_requires_exact_accounting_reconstruction(self):
        fixture = Fixture()
        before = deepcopy(fixture.first)
        tail = ' ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'
        after_fields = dict(service=dict(ExecStart='{ path=/synthetic/python ; argv[]=/synthetic/python' + tail,
                                         InvocationID='1' * 32), environment='a' * 64, pins='b' * 64,
                            route='c' * 64, drop='d' * 64)
        before_fields = deepcopy(after_fields)
        before_fields['service']['ExecStart'] = after_fields['service']['ExecStart'][:-len(tail)] + \
            ' ; start_time=[Mon 2026-01-01 00:00:00 UTC] ; stop_time=[n/a] ; pid=101 ; code=(null) ; status=0/0 }'
        before['identity']['fingerprint'] = c.sha256(c.canonical(before_fields))
        after = deepcopy(before)
        after['identity']['fingerprint'] = c.sha256(c.canonical(after_fields))
        witness = dict(before_fields=before_fields, after_fields=after_fields, exec_main_pid='101',
                       exec_main_start='Mon 2026-01-01 00:00:00 UTC')
        fixture.baseline['prior'] = deepcopy(before['identity'])
        fixture.sources, fixture.receipts = {}, []
        fixture.previous_source = fixture.source(before, 100)
        fixture.append('RELOAD_ACCOUNTING', before, after, 140, witness)
        fixture.current_raw = fixture.sources[fixture.source(after, 150)]
        self.assertEqual(c.verify_continuity_v2(**fixture.arguments()).current_admission, 'HOLD')
        fixture.receipts[0]['witness']['exec_main_start'] = 'different timestamp'
        self.unknown(fixture.arguments())

    def test_proof_gate_rejects_authority_replay_and_stale_pins(self):
        proof = c.verify_continuity_v2(**self.args)
        pins = {key: getattr(proof, key) for key in ('baseline_sha256', 'current_sha256', 'manifest_sha256', 'journal_inventory_sha256')}
        for purpose in ('NATIVE_ADMISSION', 'REPLAY', 'ISSUE', 'RETIRE_PREPARE_WRITE', 'NO_ABA'):
            with self.subTest(purpose=purpose), self.assertRaises(c.ContinuityUnknown):
                c.require_evidence_gate_v2(proof, purpose=purpose, now=151, **pins)
        with self.assertRaises(c.ContinuityUnknown):
            c.require_evidence_gate_v2(proof, purpose='OBSERVE', now=211, **pins)
        pins['current_sha256'] = '0' * 64
        with self.assertRaises(c.ContinuityUnknown):
            c.require_evidence_gate_v2(proof, purpose='OBSERVE', now=151, **pins)

    def test_existing_guards_are_not_imported_or_modified(self):
        source = MODULE_PATH.read_text(encoding='utf-8')
        for forbidden in ('import subprocess', 'import psycopg', 'import fcntl', 'import os', 'light_native_retirement_live'):
            self.assertNotIn(forbidden, source)

    @unittest.skip('NOTRUN: root-owned Linux no-follow metadata and live source capture belong to the separately reviewed host adapter')
    def test_actual_linux_metadata_and_live_acceptance(self):
        pass


if __name__ == '__main__':
    unittest.main()
