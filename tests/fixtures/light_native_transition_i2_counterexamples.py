import dataclasses, importlib.util, pathlib, sys
root = pathlib.Path.cwd()
spec = importlib.util.spec_from_file_location('target_tests', root/'tests'/'test_light_native_legacy_transition.py')
t = importlib.util.module_from_spec(spec); sys.modules[spec.name] = t; spec.loader.exec_module(t)
results = []

# 1. Native HOLD drift after a valid drain read but before the drop effect must block the effect.
case = t.TransitionTests(); case.setUp()
def drift_after_drain(proof):
    case.ports.native = dataclasses.replace(case.ports.native, admission='ACTIVE')
    return proof
case.ports.drain_change = drift_after_drain
out = case.run_forward()
assert out.state == 'UNKNOWN', out
assert case.ports.effect_counts['drop'] == 0, case.ports.effect_counts
results.append(('native_drift_after_drain', out.state, case.ports.effect_counts['drop']))

# 2. A canonical tamper that extends the retained boottime anchor must never start ACTIVE.
case = t.TransitionTests(); case.setUp()
out = case.run_forward(t.request(recovery=True))
assert out.state == 'COMPLETED_STOPPED_HOLD', out
anchor = t.k.parse(case.journal.rows['recovery-anchor'])
anchor['expires_ns'] += 10**15
case.journal.rows['recovery-anchor'] = t.k.encoded(anchor)
out = case.rollback()
assert out.state == 'UNKNOWN', out
assert case.ports.effect_counts['start:ACTIVE'] == 0, case.ports.effect_counts
results.append(('extended_recovery_anchor', out.state, case.ports.effect_counts['start:ACTIVE']))

# 3. A forged late ACTIVE receipt cannot validate current state or trigger a second start.
case = t.TransitionTests(); case.setUp()
out = case.run_forward(t.request(recovery=True))
assert out.state == 'COMPLETED_STOPPED_HOLD', out
out = case.rollback()
assert out.state == 'ACTIVE_RESTORED', out
started = t.k.parse(case.journal.rows['active-started'])
started['invocation_id'] = 'f' * 32
case.journal.rows['active-started'] = t.k.encoded(started)
count = case.ports.effect_counts['start:ACTIVE']
out = case.rollback()
assert out.state == 'UNKNOWN', out
assert case.ports.effect_counts['start:ACTIVE'] == count == 1, case.ports.effect_counts
results.append(('forged_active_receipt', out.state, case.ports.effect_counts['start:ACTIVE']))

for row in results:
    print('%s state=%s active_effect_count=%s' % row)
