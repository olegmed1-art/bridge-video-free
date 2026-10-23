"""Static workflow inventory; never confuse this with live client/source-fence proof."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github/workflows'

# Every workflow which receives the privileged Neon owner DSN on this revision.
# New entries fail the static gate until a reviewer classifies the client.
OWNER_WORKFLOWS = {
    'autopilot-production-canary-cleanup-once.yml': 'legacy_autopilot_writer',
    'autopilot-readonly-github-schema-once.yml': 'autopilot_read_probe',
    'autopilot-runtime-role-hardening.yml': 'legacy_autopilot_writer',
    'autopilot-source-migration-preflight.yml': 'autopilot_read_probe',
    'autopilot-temp-neon-0308-structured-artifact-once.yml': 'legacy_autopilot_writer',
    'autopilot-temp-neon-owner-preflight-once.yml': 'autopilot_read_probe',
    'database-production.yml': 'shared_database_administration',
    'issue-881-authoritative-external-evidence.yml': 'school_or_legacy',
    'neon-independent-backup-restore.yml': 'shared_backup',
    'oracle-autopilot-production-canary.yml': 'legacy_autopilot_writer',
    'oracle-instance-idle-candidate.yml': 'legacy_oracle_management',
    'oracle-light-fresh-candidate.yml': 'light_oracle_probe',
    'oracle-manual-hold.yml': 'legacy_oracle_management',
    'oracle-operator-v2.yml': 'legacy_oracle_management',
    'recovery-registry-population.yml': 'shared_recovery',
    'research-job-dds3-canary.yml': 'school_research',
    'research-job-neon-operator.yml': 'school_research',
}

ROUTED_WORKFLOWS = {
    'autopilot-chatgpt-role-callback.yml': {'role-callback'},
    'autopilot-codex-event-callback.yml': {'codex-ack', 'codex-terminal', 'codex-publication'},
    'autopilot-mailbox-pre-rotation.yml': {'mailbox'},
    'autopilot-paused-reconcile.yml': {'diagnostics', 'reconcile', 'next-step'},
    'autopilot-reconcile-diagnostic.yml': {'diagnostics'},
    'database-health-monitor.yml': {'health'},
}

ROUTE_CALL = re.compile(r'\bpython(?:3)?\s+-m\s+ops\.github_autopilot_db_route\s+([a-z-]+)\b')


def inventory() -> dict:
    files = {p.name: p.read_text() for p in WORKFLOWS.glob('*.yml')}
    owner = {name for name, source in files.items() if 'secrets.NEON_DATABASE_URL' in source}
    routed_calls = {name: set(ROUTE_CALL.findall(source)) for name, source in files.items()}
    routed = {name for name, calls in routed_calls.items() if calls}
    errors = []
    for description, actual, expected in (
        ('owner DSN workflows', owner, set(OWNER_WORKFLOWS)),
        ('routed workflows', routed, set(ROUTED_WORKFLOWS)),
    ):
        if actual != expected:
            errors.append({'group': description, 'added': sorted(actual - expected),
                           'removed': sorted(expected - actual)})
    for name, expected in ROUTED_WORKFLOWS.items():
        if name in files and routed_calls[name] != expected:
            errors.append({'group': 'workflow_route_targets', 'workflow': name,
                           'added': sorted(routed_calls[name] - expected),
                           'removed': sorted(expected - routed_calls[name])})
    route_source = (ROOT / 'ops/github_autopilot_db_route.py').read_text()
    for target in ('role-callback', 'codex-ack', 'codex-terminal', 'codex-publication',
                   'diagnostics', 'reconcile', 'next-step', 'health', 'mailbox'):
        if f"'{target}':" not in route_source:
            errors.append({'group': 'route_target', 'missing': target})
    return {'revision_scope': 'static_main_checkout', 'owner_workflows': OWNER_WORKFLOWS,
            'routed_workflows': {key: sorted(value) for key, value in ROUTED_WORKFLOWS.items()},
            'errors': errors,
            'live_sessions_verified': False, 'owner_credential_denial_verified': False,
            'shadow_database_verified': False, 'branch_runs_verified': False,
            'cutover_ready': False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--cutover', action='store_true', help='fail closed until independent live proofs are supplied')
    args = parser.parse_args()
    report = inventory()
    print(json.dumps(report, sort_keys=True))
    # Static inventory can pass; it must never certify a live cutover from source text alone.
    return 1 if report['errors'] or args.cutover else 0


if __name__ == '__main__':
    raise SystemExit(main())
