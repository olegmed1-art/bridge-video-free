"""Review-only one-attempt observer; never calls backup.main/stats/dump.

Receipt fields are fixed constants, validated GitHub identifiers and booleans.
No SQL/client response, exception text, URI or credential is serialized.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.request import urlopen

from ops import neon_backup_source as backup

REPOSITORY = 'olegmed1-art/bridge-video-free'
BRANCH = 'test/neon-backup-maintenance-review-20261001'
MAIN = '1440920191e1778fb9a9ba24e6701937a1a7459c'


def context():
    source = os.environ.get('EXPECTED_REVIEW', '')
    backup.require(re.fullmatch('[0-9a-f]{40}', source) is not None)
    expected = dict(GITHUB_REPOSITORY=REPOSITORY, GITHUB_REF='refs/heads/' + BRANCH,
                    GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_ACTOR='olegmed1-art',
                    GITHUB_TRIGGERING_ACTOR='olegmed1-art', GITHUB_RUN_ATTEMPT='1',
                    GITHUB_SHA=source, EXPECTED_MAIN=MAIN)
    backup.require(all(os.environ.get(k) == v for k, v in expected.items()))
    backup.require(re.fullmatch('[0-9]+', os.environ.get('GITHUB_RUN_ID', '')) is not None)
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=5)
    backup.require(result.returncode == 0 and result.stdout.strip() == source)
    return source


def check_main():
    # Public source metadata only; no auth/secret is sent to this URL.
    with urlopen('https://api.github.com/repos/' + REPOSITORY + '/git/ref/heads/main', timeout=10) as response:
        data = json.load(response)
    backup.require(data['object']['sha'] == MAIN, 'SOURCE_DRIFT')


def observe():
    start = time.monotonic()
    receipt = dict(schema='backup-source-preflight-receipt-v2', status='FAIL', failure_code='TOOL_FAILED',
                   phase='context', source_sha=None, run_id=None, attempt=1,
                   expected_identity=dict(project=backup.IDENTITY['neon.project_id'][0],
                       branch=backup.IDENTITY['neon.branch_id'][0],
                       endpoint=backup.IDENTITY['neon.endpoint_id'][0],
                       host=backup.HOST, database='neondb', role='neondb_owner'),
                   gates=dict(auth='NOT_PROVEN', identity='NOT_PROVEN',
                              acl='NOT_PROVEN', rls='NOT_PROVEN', readonly='NOT_PROVEN'),
                   limits=dict(job_seconds=120, observer_seconds=100, connect_seconds=10,
                               sql_seconds=10, client_seconds=45, cleanup_seconds=15),
                   production_writes=False, dump=False, upload=False, restore=False,
                   credential_route='LIGHT_MAINTENANCE_DATABASE_URL', retry=False)
    try:
        receipt['source_sha'] = context()
        receipt['run_id'] = os.environ['GITHUB_RUN_ID']
        receipt['phase'] = 'main_before'
        check_main()
        receipt['phase'] = 'credential_policy'
        pg = backup.parameters(os.environ.pop('DATABASE_URL', ''))
        receipt['phase'] = 'auth_identity_acl_rls_readonly'
        backup.preflight(pg, gates=receipt['gates'])  # One psql invocation; no retry.
        receipt['phase'] = 'main_after'
        check_main()
        receipt['phase'] = 'complete'
        receipt['status'] = 'PASS'
        receipt['failure_code'] = 'NONE'
    except BaseException as exc:
        # Failure never claims an unevaluated gate passed, nor reveals client errors.
        phase = receipt['phase']
        if phase == 'context':
            receipt['failure_code'] = 'CONTEXT_REFUSED'
        elif phase == 'credential_policy':
            receipt['failure_code'] = 'URI_POLICY_REFUSED'
        elif phase in ('main_before', 'main_after'):
            receipt['failure_code'] = ('SOURCE_DRIFT' if isinstance(exc, backup.BackupFailure)
                                       and exc.code == 'SOURCE_DRIFT' else 'SOURCE_CHECK_FAILED')
        elif isinstance(exc, backup.BackupFailure) and exc.code in backup.FAILURE_CODES:
            receipt['failure_code'] = exc.code
    receipt['elapsed_ms'] = int((time.monotonic() - start) * 1000)
    receipt['observed_at_utc'] = datetime.now(timezone.utc).isoformat()
    return receipt


def main():
    receipt = observe()
    payload = json.dumps(receipt, sort_keys=True)
    # Logs + job summary retain sanitized evidence, without an artifact upload.
    print(payload, flush=True)
    Path('backup-preflight-receipt.json').write_text(payload + '\n', encoding='utf-8')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write('```json\n' + payload + '\n```\n')
    return 0 if receipt['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
