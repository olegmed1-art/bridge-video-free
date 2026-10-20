"""Exact read-only retained-input/current-legacy reconciliation.

The authenticated root bootstrap verifies helper hashes before importing code.
CurrentAnchor and witness bytes exist only in this bounded call's memory. They
neither replace the historical baseline nor authorize retirement/admission/work.
No DB/provider credentials, write port, historical import, or CLI is exposed.
"""
from datetime import datetime, timezone
import json
import os
import re
import time

from ops import light_native_retirement as r

NATIVE_SOURCE = 'f82d58efabf21ba62fd242a4fa02a8e7cfda1d23'
LEGACY_SOURCE = '5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c'
LEGACY_BUNDLE = '3f7736a4fa7a6796eb9fa7be10e1d01e6cd04cb803d426991c942a91f7eb1c07'
HELPERS = frozenset(('ops/light_native_legacy_capture.py', 'ops/light_native_current_anchor.py',
                     'ops/light_native_reconciliation.py', 'ops/oracle_light_stopped_hold_upgrade.py'))
REFERENCE_KEYS = frozenset(('version', 'action', 'source', 'accepted_controller_sha256',
    'accepted_runtime_sha256', 'accepted_capture_helpers_sha256', 'accepted_legacy_package_sha256',
    'legacy_bundle_sha256', 'legacy_source', 'policy_sha256', 'index', 'original_inventory_sha256',
    'original_baseline_sha256', 'agreement', 'accepted_agreement_sha256'))
FALSE_FLAGS = frozenset(('historical_hold_equality_verified', 'history_verified', 'controls_verified',
    'incident_closed', 'execution_acknowledged', 'new_task_authorized', 'retirement_authorized',
    'admission_authorized', 'replay_authorized'))
REASONS = frozenset(('NONE', 'HOST', 'REFERENCE', 'PACKAGES', 'AUTHORITY', 'RETAINED_INPUTS',
    'ORIGINAL_INVENTORY', 'LOCAL_JOURNALS', 'BASELINE', 'CURRENT_CAPTURE', 'CURRENT_ANCHOR',
    'FINAL_GUARDS', 'BOUNDED_UNKNOWN'))
COVERAGE = ['direct_owner_sql', 'host_administration', 'workflow_administration',
            'workflow_reruns', 'main_pushes']
encoded = r.encoded
sha = r.sha


class Refusal(RuntimeError):
    """Fixed categories only. Public failures never serialize exception text."""


def _need(ok, code='REFERENCE'):
    if not ok:
        raise Refusal(code)


def _parse(raw, *, limit=8192, ascii_json=False):
    _need(type(raw) is bytes and 0 < len(raw) <= limit)
    try:
        value = r.parse(raw, limit=limit)
        canonical = _legacy_encoded(value) if ascii_json else encoded(value)
        _need(canonical == raw)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, r.Refusal):
        raise Refusal('REFERENCE') from None


def _legacy_encoded(value):
    # The existing resume.packet bundle uses default JSON ASCII serialization.
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _timestamp(value):
    _need(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value))
    try:
        return int(datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        raise Refusal('REFERENCE') from None


def scope(value):
    """Every accepted blob/source/selector is covered by the finite Agreement."""
    return dict(version=1, operation='READ_ONLY_VERIFY_RETAINED_INPUTS_AND_CURRENT_LEGACY',
        **{key: value[key] for key in ('source', 'accepted_controller_sha256', 'accepted_runtime_sha256',
            'accepted_capture_helpers_sha256', 'accepted_legacy_package_sha256', 'legacy_bundle_sha256',
            'legacy_source', 'policy_sha256', 'index', 'original_inventory_sha256', 'original_baseline_sha256')})


def reference(raw, accepted=None):
    _need(accepted is None or r.digest(accepted) and type(raw) is bytes and sha(raw) == accepted)
    value = _parse(raw)
    _need(type(value) is dict and set(value) == REFERENCE_KEYS
          and type(value['version']) is int and value['version'] == 1
          and value['action'] == 'observe-current-reconciliation-reference'
          and r.digest(value['source'], 40) and value['legacy_source'] == LEGACY_SOURCE
          and value['legacy_bundle_sha256'] == LEGACY_BUNDLE
          and type(value['index']) is int and 0 <= value['index'] < 8
          and all(r.digest(value[key]) for key in value if key.endswith('_sha256')))
    agreement = value['agreement']
    _need(type(agreement) is dict and set(agreement) == {'version', 'owner', 'operation_digest',
        'not_before', 'expires_at', 'coverage', 'evidence'}
        and type(agreement['version']) is int and agreement['version'] == 1
        and agreement['owner'] == 'olegmed1-art' and agreement['coverage'] == COVERAGE
        and agreement['evidence'] == 'OWNER_ACCEPTED_READ_ONLY_CURRENT_RECONCILIATION'
        and agreement['operation_digest'] == sha(encoded(scope(value)))
        and sha(encoded(agreement)) == value['accepted_agreement_sha256']
        and 0 < _timestamp(agreement['expires_at'])-_timestamp(agreement['not_before']) <= 1800)
    # Real Agreement validates wall clock + monotonic lifetime before any reads.
    return value


def validate_helpers(raw, accepted, source):
    _need(r.digest(accepted) and r.digest(source, 40) and type(raw) is bytes and sha(raw) == accepted, 'PACKAGES')
    package = _parse(raw, limit=3*1024*1024)
    _need(type(package) is dict and set(package) == {'version', 'kind', 'source', 'helpers'}
          and type(package['version']) is int and package['version'] == 1
          and package['kind'] == 'CURRENT_RECONCILIATION_HELPERS' and package['source'] == source
          and type(package['helpers']) is dict and set(package['helpers']) == HELPERS
          and all(type(text) is str and 0 < len(text.encode()) <= 1024*1024 for text in package['helpers'].values()),
          'PACKAGES')
    return package


def validate_legacy_package(raw, accepted_wire, accepted_logical, legacy_source):
    """Validate data against pins from a validated reference; never execute it."""
    _need(r.digest(accepted_wire) and r.digest(accepted_logical) and r.digest(legacy_source, 40)
          and type(raw) is bytes and sha(raw) == accepted_wire, 'PACKAGES')
    bundle = _parse(raw, limit=4*1024*1024, ascii_json=True)
    _need(type(bundle) is dict and set(bundle) == {'revision', 'files', 'sha256'}
          and bundle['revision'] == legacy_source and bundle['sha256'] == accepted_logical
          and type(bundle['files']) is dict and 1 <= len(bundle['files']) <= 256, 'PACKAGES')
    extras = {'ops/autopilot/broker-release.json', 'ops/autopilot/broker-hold.env', 'ops/autopilot/previous-hold.conf'}
    for name, text in bundle['files'].items():
        _need(type(name) is str and (name in extras or
              re.fullmatch(r'(?:oracle_autopilot|autopilot_phase3b)/[a-z0-9_]+\.py', name))
              and type(text) is str and len(text.encode()) <= 600000, 'PACKAGES')
    _need('ops/autopilot/broker-hold.env' in bundle['files'] and
          sha(_legacy_encoded({key: bundle[key] for key in ('revision', 'files')})) == accepted_logical,
          'PACKAGES')
    return bundle


def public_result(value, reference=None):
    keys = {'version', 'audit', 'state', 'reason', 'original_baseline_sha256', 'original_inventory_sha256',
            'current_witness_sha256', 'current_anchor_sha256', 'local_journals_verified', 'current_snapshot_verified'} | FALSE_FLAGS
    _need(type(value) is dict and set(value) == keys and type(value['version']) is int
          and value['version'] == 1 and value['audit'] == 'LIGHT_NATIVE_CURRENT_RECONCILIATION'
          and type(value['state']) is str and value['state'] in ('OBSERVED_CURRENT', 'UNKNOWN')
          and type(value['reason']) is str and value['reason'] in REASONS
          and all(value[key] is False for key in FALSE_FLAGS), 'FINAL_GUARDS')
    pins = ('original_baseline_sha256', 'original_inventory_sha256', 'current_witness_sha256', 'current_anchor_sha256')
    observed = value['state'] == 'OBSERVED_CURRENT'
    _need(value['local_journals_verified'] is observed and value['current_snapshot_verified'] is observed
          and (value['reason'] == 'NONE' if observed else value['reason'] != 'NONE'), 'FINAL_GUARDS')
    for key in pins:
        _need(r.digest(value[key]) if observed else value[key] is None or r.digest(value[key]), 'FINAL_GUARDS')
    if not observed:
        _need(value['current_witness_sha256'] is None and value['current_anchor_sha256'] is None, 'FINAL_GUARDS')
    if reference is not None:
        _need(all(value[key] == reference[key] for key in ('original_baseline_sha256', 'original_inventory_sha256')),
              'FINAL_GUARDS')
    _need(len(encoded(value)) <= 4096, 'FINAL_GUARDS')
    return value


def _result(value=None, *, reason='NONE', witness=None, anchor=None):
    observed = reason == 'NONE'
    return dict(version=1, audit='LIGHT_NATIVE_CURRENT_RECONCILIATION',
        state='OBSERVED_CURRENT' if observed else 'UNKNOWN', reason=reason,
        original_baseline_sha256=None if value is None else value['original_baseline_sha256'],
        original_inventory_sha256=None if value is None else value['original_inventory_sha256'],
        current_witness_sha256=witness if observed else None, current_anchor_sha256=anchor if observed else None,
        local_journals_verified=observed, current_snapshot_verified=observed,
        **dict.fromkeys(FALSE_FLAGS, False))


def _components():
    from ops import light_native_lane_controller as owner
    from ops import light_native_retirement_live as live
    from ops import light_native_bounded as bounded
    from ops.native_maintenance_agreement import Agreement
    from ops import light_native_legacy_capture as capture
    from ops import light_native_current_anchor as current
    return owner, live, bounded, Agreement, capture, current


def observe_current(controller, runtime, capture_helpers, legacy_raw, raw, accepted, guard):
    """One bounded root-only observation. No original or current bytes are stored."""
    value = None
    phase = 'REFERENCE'
    try:
        value = reference(raw, accepted)
        phase = 'HOST'
        _need(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'HOST')
        phase = 'PACKAGES'
        validate_helpers(capture_helpers, value['accepted_capture_helpers_sha256'], value['source'])
        legacy = validate_legacy_package(legacy_raw, value['accepted_legacy_package_sha256'],
                                         value['legacy_bundle_sha256'], value['legacy_source'])
        owner, live, bounded, Agreement, capture, current = _components()
        owner.validate_package(controller, value['source'], value['accepted_controller_sha256'])
        _need(type(runtime) is bytes and sha(runtime) == value['accepted_runtime_sha256'], 'PACKAGES')
        # Retained release.package used default ASCII JSON. Preserve its exact
        # historical bytes/hash even when helper source contains Unicode.
        retained = _parse(runtime, limit=3*1024*1024, ascii_json=True)
        _need(type(retained) is dict and set(retained) == {'version', 'source', 'runtime', 'helpers'}
              and type(retained['version']) is int and retained['version'] == 1
              and retained['source'] == owner.install.RETAINED_SOURCE == NATIVE_SOURCE, 'PACKAGES')
        owner.release.validate(retained['runtime'], retained['source'], retained['runtime']['sha256'])

        def operation():
            phase = 'AUTHORITY'
            try:
                agreement = Agreement(value['agreement'], value['accepted_agreement_sha256'], scope(value))
                agreement.assert_held(agreement.scope)
                guard.assert_current(); owner.release.staging.require_current_main(value['source'])
                agreement.assert_held(agreement.scope)
                phase = 'RETAINED_INPUTS'
                with live.observation_locks(owner.ROOT), r.Snapshot(owner.ROOT, 'unused') as view:
                    inventory, proposed = r._retained_issuer_inputs(view, value['policy_sha256'], value['index'],
                        NATIVE_SOURCE, value['accepted_runtime_sha256'])
                    inventory = r.public_issuer_inventory(inventory, value['policy_sha256'], value['index'])
                    phase = 'ORIGINAL_INVENTORY'
                    _need(sha(encoded(inventory)) == value['original_inventory_sha256'] and
                          inventory['journal_sha256']['baseline.json'] == value['original_baseline_sha256'], phase)
                    proposal = r.record(proposed, inventory['proposal_sha256'])
                    phase = 'LOCAL_JOURNALS'
                    agreement.assert_held(agreement.scope)
                    local = live.local(view, proposal, inventory['journal_sha256'], observation=True)
                    phase = 'BASELINE'
                    baseline = view.read(proposal['plan_sha256']+'/baseline.json', value['original_baseline_sha256'])
                    _need(type(local) is dict and local['baseline'] == r.parse(baseline), phase)
                    agreement.assert_held(agreement.scope)
                    phase = 'CURRENT_CAPTURE'
                    witness = capture.capture_current_legacy(runtime_bundle=legacy,
                        accepted_runtime_bundle_sha256=value['legacy_bundle_sha256'],
                        controller_source=value['source'], accepted_controller_sha256=value['accepted_controller_sha256'])
                    phase = 'CURRENT_ANCHOR'
                    witness_pin = sha(witness)
                    anchor = current.validate_current_anchor(witness, witness_pin, value['source'],
                        value['accepted_controller_sha256'], value['legacy_bundle_sha256'],
                        value['original_baseline_sha256'], int(time.time()))
                    _need(anchor.runtime_source == value['legacy_source'], phase)
                    def fresh():
                        return current.require_current_observation(anchor, purpose='OBSERVE_CURRENT',
                            accepted_witness_sha256=witness_pin, original_baseline_sha256=value['original_baseline_sha256'],
                            now=int(time.time()))
                    fresh()
                    phase = 'FINAL_GUARDS'
                    agreement.assert_held(agreement.scope)
                    guard.assert_current(); owner.release.staging.require_current_main(value['source'])
                    view.check(); agreement.assert_held(agreement.scope); fresh()
                agreement.assert_held(agreement.scope)
                fresh()  # Locks/Snapshot exit must not hide expiry or stale current evidence.
                # Only the verified JSON-safe projection is a digest preimage;
                # private retained witness bytes never enter output serialization.
                anchor_pin = sha(encoded(current.anchor_projection(anchor)))
                agreement.assert_held(agreement.scope); fresh()
                return public_result(_result(value, witness=witness_pin, anchor=anchor_pin), value)
            except BaseException:
                return public_result(_result(value, reason=phase), value)
        phase = 'BOUNDED_UNKNOWN'
        return public_result(bounded.run(operation), value)
    except BaseException:
        return public_result(_result(value, reason=phase), value)
