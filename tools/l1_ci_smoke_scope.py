"""Decide whether the existing *read-only* production smoke may receive its DSN.

Use only verified main-push before/after Git trees, never a PR title, caller
flag, partial webhook path list or HEAD^ assumption. This is a test-scope gate,
not deployment authorization. No credential, DB or network access occurs here.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

L1_PATHS = frozenset({
    'bridge_school_api/l1_canonical_runtime.py',
    'bridge_school_api/l1_canonical_runtime_v3.py',
    'bridge_school_api/l1_canonical_runtime_v4.py',
    'tests/test_l1_canonical_runtime.py',
    'tests/test_l1_canonical_runtime_v3.py',
    'tests/test_l1_observation_safety.py',
})
CI_PATHS = frozenset({
    '.github/workflows/bridge-school-api-ci.yml',
    '.github/workflows/issue-881-contract-ci.yml',
    '.github/workflows/issue-881-current-main-authoritative-ci.yml',
    '.github/workflows/secret-gate.yml',
    '.github/workflows/universal-video-ci.yml',
    # The separate L1 PR must wire its new negative cases into this workflow.
    '.github/workflows/l1-canonical-runtime-v4-ci.yml',
    'tests/test_api_smoke_workflow_contract.py',
    'tools/l1_ci_smoke_scope.py',
    'tests/test_l1_ci_smoke_scope.py',
})


def classify_diff(data: bytes) -> tuple[bool, str]:
    """Full NUL-delimited --name-status/--no-renames diff, not a path sample."""
    if type(data) is not bytes or not data or not data.endswith(b'\0'):
        return False, 'unknown_or_empty_diff'
    fields = data[:-1].split(b'\0')
    if len(fields) % 2:
        return False, 'malformed_diff'
    seen = set()
    outside = False
    for status, raw_path in zip(fields[::2], fields[1::2]):
        # Deletions/typechanges/unmerged/rename records are not an approved
        # narrow edit. Withhold credentials instead of guessing applicability.
        if status not in (b'A', b'M'):
            return False, 'unsupported_change_status'
        try:
            path = raw_path.decode('utf-8', errors='strict')
        except UnicodeError:
            return False, 'malformed_path'
        parts = path.split('/')
        if (not path or any(ord(char)<32 or ord(char)==127 for char in path)
                or '\\' in path or any(part in ('', '.', '..') for part in parts)
                or path in seen):
            return False, 'malformed_path'
        seen.add(path)
        outside |= path not in L1_PATHS | CI_PATHS
    return (True, 'ordinary_read_only_smoke') if outside else (False, 'exact_l1_or_ci_only')


def main_push_scope(environ, run=subprocess.run) -> tuple[bool, str]:
    if environ.get('CI_EVENT_NAME') != 'push' or environ.get('CI_REF') != 'refs/heads/main':
        return False, 'not_main_push'
    before, after = environ.get('CI_BEFORE', ''), environ.get('CI_AFTER', '')
    if (not isinstance(before, str) or not isinstance(after, str)
            or not re.fullmatch('[0-9a-f]{40}', before)
            or not re.fullmatch('[0-9a-f]{40}', after)
            or before == '0'*40 or after == '0'*40 or before == after):
        return False, 'invalid_push_range'

    def git(*args):
        return run(['git', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   check=True, timeout=20).stdout

    try:
        if git('rev-parse', 'HEAD').strip() != after.encode('ascii'):
            return False, 'checkout_not_event_after'
        if git('rev-parse', '--is-shallow-repository').strip() != b'false':
            return False, 'shallow_history'
        for value in (before, after):
            if git('cat-file', '-t', value).strip() != b'commit':
                return False, 'missing_commit_object'
        git('merge-base', '--is-ancestor', before, after)
        return classify_diff(git('diff', '--name-status', '--no-renames', '-z',
                                 before, after, '--'))
    except (subprocess.SubprocessError, OSError, UnicodeError):
        return False, 'unverified_push_diff'


def main() -> int:
    allowed, reason = main_push_scope(os.environ)
    # Only fixed values leave this process; raw paths/commands/errors and all
    # environment values are omitted. Unknown state withholds DSN explicitly.
    print('allow_smoke=' + ('true' if allowed else 'false'))
    print('reason=' + reason)
    print('production-smoke-scope: ' + reason, file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
