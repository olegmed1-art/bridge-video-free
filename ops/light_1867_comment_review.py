"""Read-only evidence for a proposed recovery exception; never a send permit.

Run from an authenticated gh environment. No SQL, POST, or automation changes.
The current live bridge still rejects these diagnostic mentions.
"""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone

REPO = 'olegmed1-art/bridge-video-free'
TARGET = 1769
HEAD = '2586929313ab40326d64353b513ff86e5ae3350c'
DISPATCH = '322dd440-30b9-49d2-8e1a-f5ecc1d2b99b'
DIAGNOSTICS = {
    5801585590: 'd4f3f7da327f7b9a767b82986cf58f92dbf73ecac7016a267a9f8185683564e8',
    5801658871: '946e3550a8998b39fac5ecf07bb34315685d6ce842269f101bdc42834df761ba',
    5801800189: '8baec372b681f82ace58becf5e1e1056a0917f76b3032c9d530e83bb0940bc23',
    5802079929: 'ae9c25f77c7b0021834539ffced27767fa62f628ed6692eb94d5d7c73496ab8e',
    5803910509: '0b69559dff2390dbed6d572f3f18e223f0d9e2f4fd41440a298a1d60d283c455',
}


def inspect(comments):
    """Caller must fetch every page. Pure inspection does not prove completeness."""
    if not isinstance(comments, list):
        raise ValueError('COMMENTS_INVALID')
    seen, matched, inventory = set(), set(), []
    for comment in comments:
        if not isinstance(comment, dict):
            raise ValueError('COMMENT_INVALID')
        cid, body = comment.get('id'), comment.get('body')
        if type(cid) is not int or cid <= 0 or not isinstance(body, str) or cid in seen:
            raise ValueError('COMMENT_ID_OR_BODY_INVALID')
        seen.add(cid)
        digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
        inventory.append([cid, digest])
        if cid in DIAGNOSTICS:
            if digest != DIAGNOSTICS[cid]:
                raise ValueError('DIAGNOSTIC_CHANGED')
            matched.add(cid)
        # Case-insensitive is deliberately stricter than the current renderer.
        if DISPATCH in body.casefold():
            if 'slavik_codex_dispatch_v1' in body.casefold() or body.lstrip().casefold().startswith('@codex'):
                raise ValueError('COMMAND_EVIDENCE_PRESENT')
            if cid not in DIAGNOSTICS:
                raise ValueError('UNREVIEWED_DISPATCH_MENTION')
    if matched != set(DIAGNOSTICS):
        raise ValueError('DIAGNOSTIC_MISSING')
    digest = hashlib.sha256(json.dumps(sorted(inventory), separators=(',', ':')).encode()).hexdigest()
    return {'comments': len(comments), 'diagnostics': sorted(matched), 'inventory_sha256': digest}


def gh_json(endpoint, paginate=False):
    args = ['gh', 'api', '--method', 'GET', endpoint]
    if paginate:
        args += ['--paginate', '--slurp']
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)
    return json.loads(result.stdout)


def collect():
    started = datetime.now(timezone.utc)
    endpoint = f'repos/{REPO}/issues/{TARGET}/comments?per_page=100'
    def scan():
        pages = gh_json(endpoint, paginate=True)
        if not isinstance(pages, list) or not pages or any(not isinstance(p, list) for p in pages):
            raise ValueError('PAGINATION_INVALID')
        return inspect([c for page in pages for c in page])
    def target():
        pr = gh_json(f'repos/{REPO}/pulls/{TARGET}')
        if pr.get('number') != TARGET or pr.get('head', {}).get('sha') != HEAD or pr.get('state') != 'open':
            raise ValueError('TARGET_DRIFT')
    target()
    first, second = scan(), scan()
    target()
    if first != second:
        raise ValueError('COMMENT_SCAN_DRIFT')
    finished = datetime.now(timezone.utc)
    if (finished - started).total_seconds() > 120:
        raise ValueError('SCAN_EXPIRED')
    return {**second, 'started_at': started.isoformat(), 'finished_at': finished.isoformat(),
            'target_pr': TARGET, 'expected_head_sha': HEAD,
            'diagnostic_review': 'MATCH', 'send_authorized': False,
            'live_bridge_gate': 'BLOCKED', 'database_writes': False}


if __name__ == '__main__':
    try:
        print(json.dumps(collect(), sort_keys=True))
    except (ValueError, subprocess.SubprocessError, OSError) as exc:
        # Never print gh stderr: it can contain authentication details.
        print(json.dumps({'diagnostic_review': 'REJECTED', 'send_authorized': False,
                          'reason': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}))
        sys.exit(1)
