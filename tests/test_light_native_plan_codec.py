"""Portable actual-code codec boundaries; no host, DB or provider evidence.

AST selection avoids Linux/driver imports while executing the existing pure
validators and Plan class. Package bodies and observation ports are synthetic;
the original encoders, hashes, policy/package/Plan validators are not mocked.
"""
import ast
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops import light_native_service_authority as host


ROOT = Path(__file__).resolve().parents[1]


def selected(path, names, namespace):
    nodes = []
    for node in ast.parse((ROOT/path).read_bytes()).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in names for target in node.targets):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
    return SimpleNamespace(**namespace)


def contracts():
    identifiers = selected('ops/native_maintenance_bundle.py', {'identifier'}, dict(re=re))
    observed = []
    release = selected('ops/light_native_pilot_release.py',
        {'SUPERVISOR_HELPERS', 'OWNER_HELPERS', 'EXTRA', 'HELPERS', 'MARKERS', 'require', 'encoded'},
        dict(json=json, source=identifiers,
             staging=SimpleNamespace(require_current_main=lambda source: observed.append(source))))
    wire = selected('ops/light_native_lane_execution.py', {'encoded', 'parse', 'parse_plan'},
                    dict(json=json, require=release.require))
    engine = selected('database/native_cli_permission_engine.py', {'encode'}, dict(json=json))
    flags = selected('oracle_autopilot/light_native_adapter.py', {'FALSE_FLAGS'}, {})
    hold = selected('ops/oracle_light_active_hold_attest.py', {'HOST', 'ROLE'}, {})
    target = selected('ops/native_permission_hold_guard.py', {'EXPECTED_TARGET'}, dict(hold=hold))
    intake = selected('database/light_native_pilot_intake.py',
        {'require', 'encoded', 'digest', 'strict', 'Plan'},
        dict(hashlib=hashlib, json=json, re=re, engine=engine, EXPECTED_TARGET=target.EXPECTED_TARGET))
    owner = selected('ops/light_native_lane_controller.py',
        {'EXTRA', 'RETIREMENT_HELPERS', 'encoded', 'parse', 'parse_plan', 'sha',
         'validate_package', 'scope', 'sequence'},
        dict(hashlib=hashlib, base64=base64, execution=wire, release=release,
             require=release.require, install=SimpleNamespace(RETAINED_SOURCE=host.RETAINED_SOURCE),
             ROOT=Path('/synthetic-owner')))
    agreement_codec = selected('ops/native_maintenance_workflow_pause.py',
        {'encoded', 'digest'}, dict(json=json, hashlib=hashlib))
    agreement = selected('ops/native_maintenance_agreement.py', {'COVERAGE', 'Agreement'},
        dict(datetime=datetime, timezone=timezone, re=re, json=json, time=time,
             digest=agreement_codec.digest, require=release.require))
    issuer = selected('ops/light_native_lane_issuer.py',
        {'POLICY_KEYS', 'policy_shape', 'validate', 'derive'},
        dict(base64=base64, datetime=datetime, timezone=timezone, time=time, owner=owner,
             Agreement=agreement.Agreement, COVERAGE=agreement.COVERAGE,
             EXPECTED_TARGET=target.EXPECTED_TARGET, require=release.require))
    cycle = selected('ops/light_native_lane_cycle.py',
        {'STEPS', 'BINDINGS', 'KEYS', 'validated', 'validate_scope'},
        dict(owner=owner, require=release.require, time=time))
    retirement = selected('ops/light_native_retirement.py', {'Refusal', 'need', 'parse'}, dict(json=json))
    return SimpleNamespace(wire=wire, engine=engine, intake=intake, owner=owner,
        agreement=agreement, flags=flags, issuer=issuer, cycle=cycle, retirement=retirement,
        observed=observed)


def fixture(c, *, text='Read \u043c\u043e\u0441\u0442 \U0001f0a1 exactly.', utf8_plan=False):
    spec = dict(assignment_schema='SLAVIK_DISPATCH_ASSIGNMENT_V1', repository=host.REPOSITORY,
        target_pr=2193, expected_head_sha='a'*40, execution_mode='READ_ONLY',
        exact_head_binding=True, cost_cap_microusd=0, max_repair_attempts=0,
        preserve=[text], **dict.fromkeys(c.flags.FALSE_FLAGS, False))
    plan = dict(version=1, source=host.RETAINED_SOURCE, repository=host.REPOSITORY,
        target_pr=spec['target_pr'], expected_head_sha=spec['expected_head_sha'],
        branch='fix/unicode-audit', work_key='unicode-audit', objective=text,
        priority=0, task_spec_json=spec)
    plan_raw = c.wire.encoded(plan) if utf8_plan else c.engine.encode(plan)
    controller = c.wire.encoded(dict(version=1, kind='LIGHT_LANE_CONTROLLER', source='d'*40,
        helpers=dict.fromkeys((*c.owner.release.HELPERS, *c.owner.EXTRA), '# synthetic '+text+'\n')))
    runtime = c.engine.encode(dict(source=host.RETAINED_SOURCE, note=text))
    now = int(time.time())
    stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    policy = dict(version=1, action='issue', source='d'*40,
        accepted_controller_sha256=host.sha(controller), accepted_runtime_sha256=host.sha(runtime),
        not_before=stamp(now-60), expires_at=stamp(now+3600),
        predecessor=dict(plan_sha256='b'*64, terminal_sha256='c'*64, sequence=2),
        plans=[dict(plan_base64=base64.b64encode(plan_raw).decode(),
                    accepted_plan_sha256=host.sha(plan_raw))],
        authority=dict(owner=host.OWNER, coverage=c.agreement.COVERAGE,
            delegation='FINITE_ISSUER_AGREEMENTS', evidence=text))
    raw = c.wire.encoded(policy)
    # Only the trusted manifest projection used by the pure policy validator;
    # no installation, process, package-runtime or host authority is asserted.
    manifest = SimpleNamespace(policy_sha256=host.sha(raw), source=policy['source'],
        controller_sha256=host.sha(controller), runtime_sha256=host.sha(runtime),
        not_before=policy['not_before'], expires_at=policy['expires_at'])
    return SimpleNamespace(plan=plan, plan_raw=plan_raw, controller=controller, runtime=runtime,
        policy=policy, raw=raw, accepted=host.sha(raw), manifest=manifest, now=now)


class PlanCodecContracts(unittest.TestCase):
    def setUp(self):
        self.c = contracts()

    def modules(self):
        c = self.c
        return patch.dict(sys.modules, {'database.light_native_pilot_intake': c.intake,
            'oracle_autopilot.light_native_adapter': c.flags,
            'ops.native_maintenance_agreement': c.agreement})

    def validate(self, f):
        guard_calls = []
        guard = SimpleNamespace(assert_current=lambda: guard_calls.append(True))
        result = self.c.issuer.validate(f.raw, f.accepted, f.controller, f.runtime, guard)
        self.assertEqual(guard_calls, [True])
        self.assertEqual(self.c.observed[-1], f.policy['source'])
        return result, guard

    def test_unicode_host_to_issuer_to_cycle_and_actual_plan_preserves_bytes(self):
        c = self.c
        f = fixture(c)
        accepted = host.validate_policy(f.raw, f.manifest)
        self.assertEqual(accepted.plans[0].plan_sha256, host.sha(f.plan_raw))
        self.assertIs(accepted.raw, f.raw)
        self.assertIn(b'\\u043c', f.plan_raw)
        self.assertIn('мост'.encode(), f.raw)
        policy, guard = self.validate(f)
        outer = c.issuer.derive(policy, f.accepted, 0, policy['predecessor'], f.now)
        raw = c.owner.encoded(outer)
        prepare = c.cycle.validated(raw, host.sha(raw), f.controller, f.runtime, guard)
        with self.modules():
            c.cycle.validate_scope(prepare)
            plan, _ = c.owner.scope(prepare)
            agreement = c.agreement.Agreement(prepare['agreement'],
                prepare['accepted_agreement_sha256'], plan.scope)
            agreement.assert_held(plan.scope_digest)
        self.assertEqual(plan.raw, f.plan_raw)
        self.assertEqual(plan.digest, host.sha(f.plan_raw))
        self.assertEqual(plan.value, f.plan)
        self.assertEqual(base64.b64decode(prepare['plan_base64'], validate=True), f.plan_raw)
        self.assertEqual(prepare['accepted_plan_sha256'], host.sha(f.plan_raw))
        self.assertEqual(c.owner.encoded(policy), f.raw)

    def test_ascii_plan_remains_compatible(self):
        f = fixture(self.c, text='Read source only.')
        self.assertEqual(f.plan_raw, self.c.wire.encoded(f.plan))
        policy, _ = self.validate(f)
        with self.modules():
            plan, _ = self.c.owner.scope(self.c.issuer.derive(
                policy, f.accepted, 0, policy['predecessor'], f.now)['prepare'])
        self.assertEqual(plan.raw, f.plan_raw)
        self.assertEqual(plan.digest, host.sha(f.plan_raw))

    def test_wrong_utf8_plan_codec_refuses_even_with_matching_acceptance(self):
        f = fixture(self.c, utf8_plan=True)
        self.assertEqual(host.sha(f.plan_raw), f.policy['plans'][0]['accepted_plan_sha256'])
        with self.assertRaisesRegex(RuntimeError, '^LANE_EXEC_PLAN$'):
            self.validate(f)
        with self.modules(), self.assertRaisesRegex(RuntimeError, '^PILOT_INTAKE_NONCANONICAL$'):
            self.c.owner.scope(f.policy['plans'][0])

    def test_policy_and_generic_record_codec_remain_utf8(self):
        c = self.c
        f = fixture(c)
        self.assertEqual(c.owner.parse(f.raw), f.policy)
        ascii_policy = c.engine.encode(f.policy)
        self.assertNotEqual(ascii_policy, f.raw)
        with self.assertRaisesRegex(RuntimeError, '^LANE_EXEC_RECORD$'):
            c.issuer.validate(ascii_policy, host.sha(ascii_policy), f.controller, f.runtime,
                              SimpleNamespace(assert_current=lambda: None))
        with self.assertRaisesRegex(RuntimeError, '^LANE_EXEC_RECORD$'):
            c.owner.parse(f.plan_raw)
        self.assertEqual(c.owner.parse(c.owner.encoded(dict(note='мост'))), dict(note='мост'))

    def test_plan_parser_strict_canonical_boundary(self):
        c = self.c
        f = fixture(c)
        self.assertEqual(c.owner.parse_plan(f.plan_raw), f.plan)
        invalid = [b'', bytearray(f.plan_raw), f.plan_raw.decode(), b'[]', b'null',
            b'{"a":1,"a":1}', b'{"a":{"b":1,"b":1}}',
            b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}',
            b'{ "a":1}', b'{"b":1,"a":2}', b'{"a":"\\u0061"}',
            b'{"a":"\\u043C"}', b'{"a":1}\n', b'\xff', b'{',
            b'{"a":"'+b'a'*16384+b'"}']
        for raw in invalid:
            with self.subTest(raw_type=type(raw).__name__, size=len(raw)), \
                 self.assertRaisesRegex(RuntimeError, '^LANE_EXEC_PLAN$'):
                c.owner.parse_plan(raw)
        boundary = c.engine.encode(dict(a='a'*(16384-len(b'{"a":""}'))))
        self.assertEqual(len(boundary), 16384)
        self.assertEqual(c.owner.parse_plan(boundary), dict(a='a'*(16384-len(b'{"a":""}'))))

    def test_alternate_canonical_plan_bytes_cannot_reuse_original_pin(self):
        f = fixture(self.c)
        policy = deepcopy(f.policy)
        changed = deepcopy(f.plan)
        changed['objective'] += ' Changed.'
        policy['plans'][0]['plan_base64'] = base64.b64encode(self.c.engine.encode(changed)).decode()
        raw = self.c.owner.encoded(policy)
        with self.assertRaisesRegex(RuntimeError, '^LANE_ISSUER_SCOPE$'):
            self.c.issuer.validate(raw, host.sha(raw), f.controller, f.runtime,
                                  SimpleNamespace(assert_current=lambda: None))

    def test_deep_plan_json_refuses_with_fixed_code_within_size_bound(self):
        for raw in (b'{"a":'+b'['*3000+b'0'+b']'*3000+b'}',
                    b'{"a":'*2000+b'['*1000+b'0'+b']'*1000+b'}'*2000):
            with self.subTest(size=len(raw)):
                self.assertLessEqual(len(raw), 16384)
                with self.assertRaisesRegex(RuntimeError, '^LANE_EXEC_PLAN$'):
                    self.c.owner.parse_plan(raw)

    def test_retirement_decoder_already_accepts_original_ascii_plan(self):
        f = fixture(self.c)
        self.assertEqual(self.c.retirement.parse(f.plan_raw, limit=16384), f.plan)
        with self.modules():
            self.assertEqual(self.c.intake.Plan(f.plan_raw, host.sha(f.plan_raw)).value, f.plan)

    def test_packaged_parser_adds_no_helper_dependency(self):
        self.assertIn('ops/light_native_lane_execution.py', self.c.owner.EXTRA)
        self.assertIs(self.c.owner.parse_plan, self.c.wire.parse_plan)


if __name__ == '__main__':
    unittest.main()
