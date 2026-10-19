"""Pure, fail-closed continuity evidence validation; no host or admission API.

An accepted digest is a trusted loader input, never an ACK computed by this
module. Source record provenance must first be authenticated independently.
Success proves only the supplied B -> Ti -> C relation, not an unseen history.
"""
from dataclasses import dataclass
import base64
import hashlib
import json
import re


class ContinuityUnknown(ValueError):
    """Missing or contradictory evidence: preserve records and remain UNKNOWN."""


def _need(condition, code):
    if not condition:
        raise ContinuityUnknown('CONTINUITY_UNKNOWN_' + code)


def canonical(value):
    """Match the retained owner's ASCII canonical encoding exactly."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def sha256(raw):
    _need(type(raw) is bytes, 'BYTES')
    return hashlib.sha256(raw).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result, 'DUPLICATE_KEY')
        result[key] = value
    return result


def _not_integer(_):
    raise ContinuityUnknown('CONTINUITY_UNKNOWN_NUMBER')


def _load(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= 1048576, 'BYTES')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_float=_not_integer, parse_constant=_not_integer)
        _need(canonical(value) == raw, 'CANONICAL')
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, ContinuityUnknown):
            raise
        raise ContinuityUnknown('CONTINUITY_UNKNOWN_JSON') from None


def _keys(value, keys):
    _need(type(value) is dict and set(value) == set(keys), 'SCHEMA')


def _hash(value, length=64):
    _need(type(value) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', value), 'HASH')


def _integer(value, minimum=0):
    _need(type(value) is int and minimum <= value <= 2**63 - 1, 'INTEGER')


def _identity(value, state):
    _keys(value, ('hostname', 'pid', 'invocation_id', 'release', 'fingerprint'))
    _need(type(value['hostname']) is str and re.fullmatch('[A-Za-z0-9.-]{1,253}', value['hostname']), 'HOST')
    _integer(value['pid'])
    _need(type(value['release']) is str and re.fullmatch(r'/[A-Za-z0-9_./-]{1,512}', value['release'])
          and '..' not in value['release'].split('/'), 'RELEASE')
    _hash(value['fingerprint'])
    if state == 'RUNNING':
        _need(value['pid'] > 0, 'PROCESS')
        _hash(value['invocation_id'], 32)
    else:
        _need(value['pid'] == 0 and (value['invocation_id'] == '' or
              type(value['invocation_id']) is str and re.fullmatch('[0-9a-f]{32}', value['invocation_id'])), 'PROCESS')


def _protected(value, digest):
    _need(type(value) is dict and 1 <= len(value) <= 32, 'INVENTORY')
    for name, row in value.items():
        _need(type(name) is str and 1 <= len(name) <= 1024 and '\n' not in name, 'INVENTORY')
        _keys(row, ('mode', 'limit', 'sha256'))
        _integer(row['mode'])
        _integer(row['limit'], 1)
        _need(row['mode'] <= 0o777 and row['limit'] <= 16777216, 'METADATA')
        _hash(row['sha256'])
    _hash(digest)
    _need(sha256(canonical(value)) == digest, 'CONFIG_DIGEST')


def _snapshot(value):
    _keys(value, ('identity', 'state', 'admission', 'protected', 'protected_sha256'))
    _need(value['state'] in ('RUNNING', 'STOPPED') and value['admission'] in ('HOLD', 'ACTIVE'), 'STATE')
    _identity(value['identity'], value['state'])
    _protected(value['protected'], value['protected_sha256'])


def _baseline(raw):
    value = _load(raw)
    _keys(value, ('version', 'source', 'package_sha256', 'agreement_sha256',
                  'scope_sha256', 'prior', 'protected', 'protected_sha256', 'observed_at'))
    _need(type(value['version']) is int and value['version'] == 1, 'BASELINE_VERSION')
    _hash(value['source'], 40)
    for name in ('package_sha256', 'agreement_sha256', 'scope_sha256'):
        _hash(value[name])
    _integer(value['observed_at'])
    snapshot = dict(identity=value['prior'], state='RUNNING', admission='HOLD',
                    protected=value['protected'], protected_sha256=value['protected_sha256'])
    _snapshot(snapshot)
    return value, snapshot


def _source(raw, pin):
    value = _load(raw)
    _keys(value, ('version', 'controller_source', 'controller_package_sha256',
                  'installed_release', 'observed_at', 'snapshot'))
    _keys(pin, ('controller_source', 'controller_package_sha256'))
    _need(type(value['version']) is int and value['version'] == 2, 'SOURCE_VERSION')
    _hash(value['controller_source'], 40)
    _hash(value['controller_package_sha256'])
    _need(all(value[k] == pin[k] for k in pin), 'PROVENANCE')
    _integer(value['observed_at'])
    _snapshot(value['snapshot'])
    _need(value['installed_release'] == value['snapshot']['identity']['release'], 'INSTALLED_RELEASE')
    return value


def _changes(before, after):
    """Compare a fixed schema; this is deliberately not a JSON patch evaluator."""
    changes = []
    for key in ('state', 'admission', 'protected', 'protected_sha256'):
        if before[key] != after[key]:
            changes.append(dict(field=key, before=before[key], after=after[key]))
    for key in ('hostname', 'pid', 'invocation_id', 'release', 'fingerprint'):
        if before['identity'][key] != after['identity'][key]:
            changes.append(dict(field='identity.' + key, before=before['identity'][key], after=after['identity'][key]))
    return sorted(changes, key=lambda row: row['field'])


def _drop_bytes(release, variant, admission):
    raw = '[Service]\nWorkingDirectory=' + release + '\nEnvironment=AUTOPILOT_ADMISSION_MODE=' + admission + '\n'
    if variant == 'WITH_BROKER_ENV':
        raw += 'EnvironmentFile=' + release + '/ops/autopilot/broker-hold.env\n'
    return raw.encode()


def _activation(before, after, witness, drop_name, variant):
    _keys(witness, ('before_base64', 'after_base64'))
    try:
        old = base64.b64decode(witness['before_base64'], validate=True)
        new = base64.b64decode(witness['after_base64'], validate=True)
    except (ValueError, TypeError):
        raise ContinuityUnknown('CONTINUITY_UNKNOWN_DROP_BYTES') from None
    release = before['identity']['release']
    _need(old == _drop_bytes(release, variant, 'HOLD') and
          new == _drop_bytes(release, variant, 'ACTIVE'), 'DROP_SUBSTITUTION')
    old_row, new_row = before['protected'][drop_name], after['protected'][drop_name]
    _need(old_row['mode'] == new_row['mode'] == 0o644 and
          old_row['limit'] == new_row['limit'] and len(old) <= old_row['limit'] and
          len(new) <= new_row['limit'] and old_row['sha256'] == sha256(old) and
          new_row['sha256'] == sha256(new), 'DROP_BINDING')
    _need(all(after['protected'][name] == row for name, row in before['protected'].items()
              if name != drop_name), 'PROTECTED_DRIFT')


def _accounting(before, after, witness):
    _keys(witness, ('before_fields', 'after_fields', 'exec_main_pid', 'exec_main_start'))
    old, new = witness['before_fields'], witness['after_fields']
    for fields, snapshot in ((old, before), (new, after)):
        _keys(fields, ('service', 'environment', 'pins', 'route', 'drop'))
        _need(type(fields['service']) is dict and 'ExecStart' in fields['service'] and
              all(type(k) is str and type(v) is str for k, v in fields['service'].items()), 'ACCOUNTING_FIELDS')
        for name in ('environment', 'pins', 'route', 'drop'):
            _hash(fields[name])
        _need(sha256(canonical(fields)) == snapshot['identity']['fingerprint'], 'ACCOUNTING_FINGERPRINT')
    _need(witness['exec_main_pid'] == str(before['identity']['pid']) and
          type(witness['exec_main_start']) is str and
          re.fullmatch(r'[A-Za-z0-9:+. -]{1,128}', witness['exec_main_start']), 'ACCOUNTING_PROCESS')
    tail = ' ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'
    current = new['service']['ExecStart']
    _need(current.endswith(tail), 'ACCOUNTING_TAIL')
    reconstructed = current[:-len(tail)] + ' ; start_time=[' + witness['exec_main_start'] + \
        '] ; stop_time=[n/a] ; pid=' + witness['exec_main_pid'] + ' ; code=(null) ; status=0/0 }'
    expected = {**new, 'service': {**new['service'], 'ExecStart': reconstructed}}
    _need(old == expected, 'ACCOUNTING_EQUIVALENCE')


def _transition(receipt, drop_name, variant):
    before, after = receipt['before'], receipt['after']
    _snapshot(before)
    _snapshot(after)
    _need(set(before['protected']) == set(after['protected']) and drop_name in before['protected'], 'INVENTORY')
    _need(before['identity']['hostname'] == after['identity']['hostname'] and
          before['identity']['release'] == after['identity']['release'], 'INVARIANT')
    changes = _changes(before, after)
    _need(bool(changes) and canonical(receipt['changes']) == canonical(changes), 'EXACT_CHANGES')
    fields = {row['field'] for row in changes}
    process = {'state', 'identity.pid', 'identity.invocation_id', 'identity.fingerprint'}
    kind = receipt['kind']
    if kind == 'RELOAD_ACCOUNTING':
        _need(before['state'] == after['state'] == 'RUNNING' and
              before['admission'] == after['admission'] == 'HOLD' and
              fields == {'identity.fingerprint'}, 'RELOAD_SCOPE')
        _accounting(before, after, receipt['witness'])
    elif kind in ('STOP_HOLD', 'START_HOLD', 'ACTIVATE_LEGACY'):
        old_state, new_state = ('RUNNING', 'STOPPED') if kind == 'STOP_HOLD' else ('STOPPED', 'RUNNING')
        _need(before['state'] == old_state and after['state'] == new_state and
              before['admission'] == 'HOLD' and
              before['identity']['fingerprint'] != after['identity']['fingerprint'], 'LIFECYCLE')
        if kind == 'ACTIVATE_LEGACY':
            _need(after['admission'] == 'ACTIVE' and fields <= process | {'admission', 'protected', 'protected_sha256'}, 'ACTIVATION_SCOPE')
            _activation(before, after, receipt['witness'], drop_name, variant)
        else:
            _need(after['admission'] == 'HOLD' and fields <= process and receipt['witness'] is None, 'HOLD_SCOPE')
        if kind != 'STOP_HOLD':
            _need(before['identity']['invocation_id'] != after['identity']['invocation_id'], 'NEW_INVOCATION')
        else:
            _need(after['identity']['invocation_id'] in ('', before['identity']['invocation_id']), 'STOP_INVOCATION')
    else:
        raise ContinuityUnknown('CONTINUITY_UNKNOWN_RECEIPT_KIND')


@dataclass(frozen=True, repr=False)
class ContinuityProof:
    """Private evidence capability; never Native, replay or no-ABA authority."""
    baseline_sha256: str
    current_sha256: str
    manifest_sha256: str
    journal_inventory_sha256: str
    receipt_sha256: tuple
    expires_at: int
    current_observed_at: int
    current_admission: str
    assurance: str = 'CONTINUITY_ONLY'


def verify_continuity_v2(*, baseline_raw, current_raw, receipts, source_records,
                         manifest_raw, accepted_manifest_sha256, baseline_sha256,
                         journal_inventory_sha256, validator_source,
                         validator_sha256, now, max_current_age_seconds=60):
    """Validate independently accepted exact bytes. Any gap raises UNKNOWN.

    The caller authenticates source records and acceptance *before* this call,
    computes unchanged journal inventory from protected originals, and pins this
    module in its verified controller release. This function performs no I/O.
    """
    for digest in (accepted_manifest_sha256, baseline_sha256, journal_inventory_sha256, validator_sha256):
        _hash(digest)
    _hash(validator_source, 40)
    _integer(now)
    _integer(max_current_age_seconds, 1)
    _need(max_current_age_seconds <= 300, 'FRESHNESS_BOUND')
    _need(sha256(manifest_raw) == accepted_manifest_sha256 and sha256(baseline_raw) == baseline_sha256, 'TRUST_PIN')
    manifest = _load(manifest_raw)
    _keys(manifest, ('version', 'purpose', 'baseline_sha256', 'journal_inventory_sha256',
                     'current_sha256', 'validator_source', 'validator_sha256',
                     'accepted_at', 'expires_at', 'drop_name', 'drop_variant', 'receipts', 'sources'))
    _need(type(manifest['version']) is int and manifest['version'] == 2 and
          manifest['purpose'] == 'LEGACY_CONTINUITY_ONLY', 'MANIFEST_SCOPE')
    for key, expected in (('baseline_sha256', baseline_sha256),
                          ('journal_inventory_sha256', journal_inventory_sha256),
                          ('validator_source', validator_source), ('validator_sha256', validator_sha256),
                          ('current_sha256', sha256(current_raw))):
        _need(manifest[key] == expected, 'MANIFEST_PIN')
    _integer(manifest['accepted_at'])
    _integer(manifest['expires_at'])
    _need(manifest['accepted_at'] <= now < manifest['expires_at'] and
          manifest['expires_at'] - manifest['accepted_at'] <= 900, 'ACCEPTANCE_WINDOW')
    _need(type(manifest['drop_name']) is str and manifest['drop_variant'] in ('BASIC', 'WITH_BROKER_ENV'), 'DROP_POLICY')
    _need(type(receipts) in (tuple, list) and 0 < len(receipts) <= 128 and
          type(manifest['receipts']) is list and len(receipts) == len(manifest['receipts']), 'MISSING_CHAIN')
    _need(type(source_records) is dict and type(manifest['sources']) is dict and
          set(source_records) == set(manifest['sources']) and len(source_records) <= 257, 'SOURCE_INVENTORY')
    sources = {}
    for digest, raw in source_records.items():
        _hash(digest)
        _need(type(raw) is bytes and sha256(raw) == digest, 'SOURCE_HASH')
        sources[digest] = _source(raw, manifest['sources'][digest])
    current_digest = sha256(current_raw)
    _need(source_records.get(current_digest) == current_raw, 'CURRENT_SOURCE')
    current = sources[current_digest]
    _need(0 <= now - current['observed_at'] <= max_current_age_seconds and
          current['observed_at'] <= manifest['accepted_at'], 'CURRENT_STALE')
    baseline, snapshot = _baseline(baseline_raw)
    previous, observed, seen, used = None, baseline['observed_at'], set(), {current_digest}
    for number, raw in enumerate(receipts):
        _need(type(raw) is bytes, 'RECEIPT_BYTES')
        digest = sha256(raw)
        _need(digest == manifest['receipts'][number] and digest not in seen, 'RECEIPT_PIN')
        seen.add(digest)
        receipt = _load(raw)
        _keys(receipt, ('version', 'kind', 'sequence', 'previous_receipt_sha256',
                        'before_source_sha256', 'after_source_sha256', 'before', 'after', 'changes', 'witness'))
        _need(type(receipt['version']) is int and receipt['version'] == 2 and
              type(receipt['sequence']) is int and receipt['sequence'] == number and
              receipt['previous_receipt_sha256'] == previous, 'CHAIN')
        _need(receipt['before'] == snapshot, 'DISCONNECTED')
        source_pair = []
        for side in ('before', 'after'):
            source_digest = receipt[side + '_source_sha256']
            _hash(source_digest)
            _need(source_digest in sources, 'MISSING_SOURCE')
            source = sources[source_digest]
            _need(source['snapshot'] == receipt[side], 'SOURCE_SNAPSHOT')
            used.add(source_digest)
            source_pair.append(source)
        start, end = source_pair
        _need(observed <= start['observed_at'] < end['observed_at'] <= manifest['accepted_at'], 'SOURCE_ORDER')
        _transition(receipt, manifest['drop_name'], manifest['drop_variant'])
        snapshot, observed, previous = receipt['after'], end['observed_at'], digest
    _need(used == set(sources) and snapshot == current['snapshot'] and
          observed <= current['observed_at'], 'CURRENT_DISCONNECTED')
    return ContinuityProof(baseline_sha256, current_digest, accepted_manifest_sha256,
                           journal_inventory_sha256, tuple(manifest['receipts']),
                           manifest['expires_at'], current['observed_at'], snapshot['admission'])


def require_evidence_gate_v2(proof, *, purpose, baseline_sha256, current_sha256,
                             manifest_sha256, journal_inventory_sha256, now):
    """Proposed explicit v2 consumer gate; existing v1 guards are untouched."""
    _need(type(proof) is ContinuityProof and proof.assurance == 'CONTINUITY_ONLY', 'PROOF')
    _need(purpose in ('OBSERVE', 'RETIREMENT_EVIDENCE'), 'NO_AUTHORITY')
    _integer(now)
    _need(0 <= now - proof.current_observed_at <= 60 and now < proof.expires_at, 'PROOF_STALE')
    for key, value in (('baseline_sha256', baseline_sha256), ('current_sha256', current_sha256),
                       ('manifest_sha256', manifest_sha256), ('journal_inventory_sha256', journal_inventory_sha256)):
        _hash(value)
        _need(getattr(proof, key) == value, 'PROOF_REPIN')
    return proof
