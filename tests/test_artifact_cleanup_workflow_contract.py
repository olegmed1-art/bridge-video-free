#!/usr/bin/env python3
from pathlib import Path

p = Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'artifact-cleanup.yml'
text = p.read_text(encoding='utf-8')

required = [
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
    'test "$GITHUB_ACTOR" = olegmed1-art',
    'test "$GITHUB_TRIGGERING_ACTOR" = olegmed1-art',
    'python tools/github_actions_artifact_cleanup.py',
]
for item in required:
    assert item in text, item

for forbidden in [
    'BRIDGE_PRODUCTION_DATABASE_URL',
    'DATABASE_URL: ${{ secrets.DATABASE_URL }}',
    'permissions: write-all',
    'persist-credentials: true',
]:
    assert forbidden not in text, forbidden

# Pull requests receive no database secret because the cleanup job is skipped.
assert text.index("if: github.event_name != 'pull_request'") < text.index('BRIDGE_WORKER_DATABASE_URL: ${{ secrets.BRIDGE_WORKER_DATABASE_URL }}')
# The push path is intentionally audit-only on first deployment.
assert 'Push is deliberately dry-run during deployment validation.' in text
print('ARTIFACT_CLEANUP_WORKFLOW_CONTRACT: PASS')
