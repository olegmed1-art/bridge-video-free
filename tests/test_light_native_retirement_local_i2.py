"""Independent local-diagnostic regressions: real root journals and supervisor.

Only current-main/run and host-name inputs are synthetic external ports. No DB,
provider, deployed runtime, service, writer or transport may be called here.
"""
import ast
from copy import deepcopy
import hashlib
import inspect
import os
from pathlib import Path
import socket
import subprocess
import time

import pytest

from ops import light_native_bounded as bounded
from ops import light_native_lane_controller as owner
from ops import light_native_retirement as r
from ops import light_native_retirement_live as live
from ops import native_maintenance_owner_attest as attest
from ops import native_maintenance_owner_host as host
from test_light_native_lane_issuer import request_bytes
from test_light_native_retirement import retained, store
from test_light_native_retirement_reference import reference
from test_light_native_retirement_observation_i2 import fingerprint


PRIVATE = 'SYNTHETIC_PRIVATE_DIAGNOSTIC_SENTINEL_4729'
ORIGINAL_LOCAL_AST_SHA256 = '7c589eaed44cd3f02563c28b4533bd4b594aef9502b8a02a08b4e7edf21c91ea'


def test_instrumentation_erases_to_exact_original_guard_ast():
    # Independent static complement to the filesystem behavior cases below.
    # This digest is the pre-instrumentation local() AST, not a new implementation.
    node = ast.parse(inspect.getsource(live.local)).body[0]
    class Erase(ast.NodeTransformer):
        def visit_If(self, item):
            if (isinstance(item.test, ast.Compare)
                    and isinstance(item.test.left, ast.Name)
                    and item.test.left.id == 'diagnostic'):
                return None
            return self.generic_visit(item)
        def visit_Assign(self, item):
            if any(isinstance(x, ast.Name) and x.id == '_parse' for x in item.targets):
                return None
            return self.generic_visit(item)
        def visit_Call(self, item):
            if isinstance(item.func, ast.Name) and item.func.id == '_local_condition':
                assert len(item.args) == 3 and isinstance(item.args[2], ast.Lambda)
                return self.visit(item.args[2].body)
            return self.generic_visit(item)
        def visit_Name(self, item):
            if item.id == '_parse':
                return ast.Attribute(value=ast.Name(id='r', ctx=ast.Load()), attr='parse', ctx=ast.Load())
            return item
    node = Erase().visit(node)
    pairs = [(a, b) for a, b in zip(node.args.kwonlyargs, node.args.kw_defaults) if a.arg != 'diagnostic']
    node.args.kwonlyargs = [x[0] for x in pairs]
    node.args.kw_defaults = [x[1] for x in pairs]
    assert hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest() == ORIGINAL_LOCAL_AST_SHA256


def prior(t, name):
    return t.root / t.previous['plan_sha256'] / name


def local_outcome(t, diagnostic=None):
    with r.Snapshot(t.root, 'unused') as view:
        try:
            result = live.local(view, t.record, t.pins, observation=True, diagnostic=diagnostic)
            view.check()
            return ('PASS', result)
        except Exception as exc:
            # Comparison is internal to synthetic tests; never a public report.
            return ('FAIL', type(exc), str(exc))


CASES = [
    ('good', None, None),
    ('baseline-syntax', 'L078', 'JSON_SYNTAX'),
    ('before-array', None, None),
    ('baseline-array', None, None),
    ('issuer-keys', 'L012', 'PREDICATE_FALSE'),
    ('intake-key', 'L035', 'JSON_KEYS'),
    ('execution-type', 'L095', 'JSON_TYPE'),
    ('complete-type', 'L036', 'JSON_TYPE'),
    ('restore-value', 'L037', 'PREDICATE_FALSE'),
    ('prepare-sequence', 'L033', 'PREDICATE_FALSE'),
    ('cycle-extra', 'L017', 'PREDICATE_FALSE'),
    ('plan-extra', 'L022', 'PREDICATE_FALSE'),
    ('entry-extra', 'L010', 'PREDICATE_FALSE'),
    ('historical-missing', 'L083', 'MISSING'),
    ('helper-value', 'L032', 'PREDICATE_FALSE'),
    ('original-hash', 'L049', 'HASH_MISMATCH'),
    ('original-mode', 'L049', 'METADATA'),
    ('original-missing', 'L049', 'MISSING'),
]


def alter(t, fault):
    if fault in ('baseline-syntax', 'baseline-array', 'before-array'):
        name = 'before.json' if fault == 'before-array' else 'baseline.json'
        body = b'{' if fault == 'baseline-syntax' else b'[]'
        store(t.root, t.plan + '/' + name, body)
        t.pins[name] = r.sha(body)
    elif fault == 'issuer-keys':
        store(t.root, t.entry + '/intent.json', {'cycle': {}})
        t.pins['issuer-intent'] = r.sha((t.root / t.entry / 'intent.json').read_bytes())
    elif fault == 'intake-key':
        prior(t, 'intake.json').write_bytes(r.encoded({PRIVATE: True}))
    elif fault == 'execution-type':
        prior(t, 'execution.json').write_bytes(b'[]')
    elif fault == 'complete-type':
        prior(t, 'complete.json').write_bytes(b'null')
    elif fault == 'restore-value':
        prior(t, 'controls-restored.json').write_bytes(b'{}')
    elif fault == 'prepare-sequence':
        path = prior(t, 'prepare.json'); value = r.parse(path.read_bytes())
        value['predecessor']['sequence'] = 0; path.write_bytes(r.encoded(value))
    elif fault in ('cycle-extra', 'plan-extra', 'entry-extra'):
        where = {'cycle-extra': t.cycle, 'plan-extra': t.plan, 'entry-extra': t.entry}[fault]
        store(t.root, where + '/synthetic-unexpected.json', b'{}')
    elif fault == 'historical-missing':
        (t.root / 'controllers' / t.record['historical_controller_source'] / 'package.json').unlink()
    elif fault == 'helper-value':
        path = t.root / 'controllers' / t.record['historical_controller_source'] / 'ops/light_native_lane_controller.py'
        path.write_bytes(PRIVATE.encode())
    elif fault == 'original-hash':
        (t.root / t.plan / 'baseline.json').write_bytes(PRIVATE.encode())
    elif fault == 'original-mode':
        (t.root / t.plan / 'baseline.json').chmod(0o644)
    elif fault == 'original-missing':
        (t.root / t.plan / 'baseline.json').unlink()


@pytest.mark.parametrize('fault,checkpoint,reason', CASES)
def test_diagnostic_preserves_actual_local_outcome_and_first_failure(retained, fault, checkpoint, reason):
    t = retained; alter(t, fault)
    plain = local_outcome(t)
    diagnostic = live.LocalDiagnostic()
    observed = local_outcome(t, diagnostic)
    assert plain == observed
    if checkpoint is None:
        assert observed[0] == 'PASS'
    else:
        assert observed[0] == 'FAIL' and diagnostic.checkpoint == checkpoint
        # Reconstitute only a synthetic exception for the fixed enum classifier.
        exc = observed[1](observed[2])
        assert diagnostic.reason(exc) == reason
        safe = diagnostic.result(exc)
        assert PRIVATE not in r.encoded(safe).decode()
        assert not safe['local_checks_final']
    if fault.startswith('original-'):
        assert all(row == [0, 0, 0, 0] for row in diagnostic.trace[1:])


def test_repeated_unpinned_reads_preserve_pin_pass_and_do_not_misattribute_schema(retained):
    t = retained; diagnostic = live.LocalDiagnostic()
    assert local_outcome(t, diagnostic)[0] == 'PASS'
    assert all(diagnostic.trace[n][2] == 1 for n in range(11))
    assert all(row[3] == 0 for row in diagnostic.trace)
    # A directory predicate failure must not become the last-read file's schema failure.
    store(t.root, t.entry + '/unexpected.json', b'{}')
    diagnostic = live.LocalDiagnostic()
    assert local_outcome(t, diagnostic)[0] == 'FAIL'
    assert diagnostic.checkpoint == 'L010'
    assert all(row[3] == 0 for row in diagnostic.trace)


def test_trusted_prefix_history_reads_do_not_add_diagnostic_rejection(retained):
    t = retained
    path = 'cycles/' + t.previous['plan_sha256'] + '/complete.json'
    raw = r.encoded({'synthetic_prior_completed_cycle': True})
    store(t.root, path, raw)
    diagnostic = live.LocalDiagnostic()
    with r.Snapshot(t.root, 'unused') as view:
        traced = diagnostic.wrap(view, t.record)
        assert traced.read(path, r.sha(raw)) == view.read(path, r.sha(raw)) == raw
        assert diagnostic.role == diagnostic.helper_index == -1
        assert all(row == [0, 0, 0, 0] for row in diagnostic.trace)
        with pytest.raises(RuntimeError): traced.read(path, '0' * 64)
        with pytest.raises(RuntimeError): traced.read('cycles/' + t.previous['plan_sha256'] + '/missing.json')
        view.check()


@pytest.fixture
def diagnostic_reference(reference):
    t = reference; private = r.parse(t.private)
    private['action'] = 'diagnose-local'
    scope = dict(version=1, operation='READ_ONLY_DIAGNOSE_LOCAL_RETIREMENT_JOURNALS',
        source=private['source'], controller_sha256=private['accepted_controller_sha256'],
        runtime_sha256=private['accepted_runtime_sha256'], record_sha256=private['record_sha256'],
        historical_journal_sha256=t.pins)
    private['agreement'].update(evidence='OWNER_ACCEPTED_LOCAL_JOURNAL_DIAGNOSTIC',
        operation_digest=r.sha(r.encoded(scope)))
    private['accepted_agreement_sha256'] = r.sha(r.encoded(private['agreement']))
    t.private = r.encoded(private)
    t.public.update(action='diagnose-local-reference', agreement=private['agreement'],
        accepted_agreement_sha256=private['accepted_agreement_sha256'], private_request_sha256=r.sha(t.private))
    return t


@pytest.fixture
def local_only(diagnostic_reference, monkeypatch):
    t = diagnostic_reference
    assert os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0
    uname = os.uname()
    monkeypatch.setattr(os, 'uname', lambda: type(uname)((uname.sysname, 'autopilot-lite-vnic',
        uname.release, uname.version, uname.machine)))
    def forbidden(*args, **kwargs):
        raise AssertionError('FORBIDDEN_EXTERNAL_OR_WRITE_PORT_' + PRIVATE)
    for module, name in ((live, 'write'), (live, 'observer'), (live, 'inspect_retirement'),
            (r, 'write_proposal'), (owner, 'phase'), (owner, 'retain'), (owner, 'bootstrap_helpers'),
            (owner.install.hold, 'attest'), (owner.execution, 'stopped'),
            (owner.release, 'validate'), (owner.release.staging, 'verify_release'),
            (host, 'loaded_runtime'), (attest, 'parameters'), (socket, 'socket'),
            (subprocess, 'run'), (subprocess, 'Popen')):
        monkeypatch.setattr(module, name, forbidden)
    def run():
        raw = r.encoded(t.public)
        return live.inspect_local(b'UNUSED_DRIVER', 'UNUSED_CREDENTIAL', t.request.controller,
            t.request.runtime, raw, r.sha(raw), t.request.guard)
    t.run = run
    return t


@pytest.mark.parametrize('proposal', ['absent', 'exact', 'conflict'])
def test_actual_bounded_local_diagnostic_has_no_external_or_write_path(local_only, proposal, capsys):
    t = local_only
    if proposal != 'absent':
        store(t.root, t.entry + '/' + r.NAME, t.raw if proposal == 'exact' else b'{')
    before = fingerprint(t.root)
    result = t.run()
    assert result['state'] == 'LOCAL_CHECKED' and result['checkpoint'] == 'DONE'
    assert result['reason'] == 'NONE' and result['local_checks_final'] is True
    assert result['record_sha256'] == t.public['record_sha256']
    assert live.public_local_result(result, t.public['record_sha256']) == result
    assert all(result[k] is False for k in ('proposal_observation_final', 'hold_db_provider_verified',
        'incident_closed', 'execution_acknowledged', 'new_task_authorized'))
    assert len(r.encoded(result)) < 4096 and PRIVATE not in str(result)
    assert fingerprint(t.root) == before and capsys.readouterr() == ('', '')


@pytest.mark.parametrize('fault,checkpoint,reason', [
    ('intake-key', 'L035', 'JSON_KEYS'), ('execution-type', 'L095', 'JSON_TYPE'),
    ('complete-type', 'L036', 'JSON_TYPE'), ('restore-value', 'L037', 'PREDICATE_FALSE')])
def test_real_supervisor_preserves_precise_safe_refusal(local_only, fault, checkpoint, reason, capsys):
    t = local_only; alter(t, fault); before = fingerprint(t.root)
    result = t.run()
    assert result['state'] == 'LOCAL_REFUSED' and not result['local_checks_final']
    assert result['checkpoint'] == checkpoint and result['reason'] == reason
    assert PRIVATE not in str(result) and len(r.encoded(result)) < 4096
    assert fingerprint(t.root) == before and capsys.readouterr() == ('', '')


@pytest.mark.parametrize('fault', ['record', 'extra', 'checkpoint', 'reason', 'role-bool',
    'helper-bool', 'trace-bool', 'live-proof', 'final', 'private'])
def test_egress_rejects_unbound_or_private_diagnostic(local_only, fault):
    t = local_only; result = deepcopy(t.run())
    if fault == 'record': result['record_sha256'] = '0' * 64
    elif fault == 'extra': result['raw'] = PRIVATE
    elif fault == 'checkpoint': result['checkpoint'] = 'L098'
    elif fault == 'reason': result['reason'] = PRIVATE
    elif fault == 'role-bool': result['role'] = True
    elif fault == 'helper-bool': result['helper_index'] = False
    elif fault == 'trace-bool': result['trace'][0][0] = True
    elif fault == 'live-proof': result['hold_db_provider_verified'] = True
    elif fault == 'final': result['local_checks_final'] = False
    else: result['trace'][0][3] = PRIVATE
    with pytest.raises(RuntimeError): live.public_local_result(result, t.public['record_sha256'])


@pytest.mark.parametrize('fault', ['timeout', 'crash'])
def test_local_supervisor_failure_is_unknown_not_diagnostic_success(local_only, monkeypatch, fault):
    t = local_only; original = bounded.run
    monkeypatch.setattr(bounded, 'run', lambda callback: original(callback, seconds=0.2))
    def broken(*args, **kwargs):
        if fault == 'crash': os._exit(23)
        time.sleep(1)
    monkeypatch.setattr(live, 'local', broken)
    before = fingerprint(t.root)
    with pytest.raises(RuntimeError, match='^LANE_RETIREMENT_UNKNOWN$'): t.run()
    assert fingerprint(t.root) == before
