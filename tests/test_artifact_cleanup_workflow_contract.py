#!/usr/bin/env python3
from pathlib import Path

p = Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'artifact-cleanup.yml'
text = p.read_text(encoding='utf-8')

required = [
    'schedule:',
    "cron: '47 */3 * * *'",
    'pull_request:',
    "if: github.event_name != 'pull_request'",
    'actions: write',
    'contents: read',
    'persist-credentials: false',
    'Run fail-closed cleanup unit and workflow contract tests',
    'Install pinned PostgreSQL client after contract tests',
    'BRIDGE_WORKER_DATABASE_URL: ${{ secrets.BRIDGE_WORKER_DATABASE_URL }}',
    'GITHUB_TOKEN: ${{ github.token }}',
    'test "$GITHUB_REF_NAME" = main',
    '--max-artifacts 1000',
    'case "$GITHUB_EVENT_NAME" in',
    'schedule)',
    'push)',
    'workflow_dispatch)',
    'test "$GITHUB_ACTOR" = olegmed1-art',
    'test "$GITHUB_TRIGGERING_ACTOR" = olegmed1-art',
    'args+=(--execute)',
    'do not bind safe cleanup to a particular runtime actor identity.',
    "'tools/run_artifact_cleanup_canonical.py'",
    'python tools/run_artifact_cleanup_canonical.py',
]
for item in required:
    assert item in text, item

for forbidden in [
    'BRIDGE_PRODUCTION_DATABASE_URL',
    'DATABASE_URL: ${{ secrets.DATABASE_URL }}',
    'permissions: write-all',
    'persist-credentials: true',
    'Push is deliberately dry-run during deployment validation.',
    '--max-artifacts 200',
    'python tools/github_actions_artifact_cleanup.py "${args[@]}"',
]:
    assert forbidden not in text, forbidden

# Pull requests receive no database secret because the cleanup job is skipped.
assert text.index("if: github.event_name != 'pull_request'") < text.index('BRIDGE_WORKER_DATABASE_URL: ${{ secrets.BRIDGE_WORKER_DATABASE_URL }}')
# Owner identity checks must remain in manual workflow_dispatch handling, not push handling.
push_block = text[text.index('push)'):text.index('workflow_dispatch)')]
assert 'GITHUB_ACTOR' not in push_block
assert 'GITHUB_TRIGGERING_ACTOR' not in push_block
manual_block = text[text.index('workflow_dispatch)'):text.index('*)')]
assert 'test "$GITHUB_ACTOR" = olegmed1-art' in manual_block
assert 'test "$GITHUB_TRIGGERING_ACTOR" = olegmed1-art' in manual_block
print('ARTIFACT_CLEANUP_WORKFLOW_CONTRACT: PASS')
