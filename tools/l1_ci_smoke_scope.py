"""Classify complete main-push trees before the existing read-only smoke DSN.

Only the exact reviewed six-file L1 delta may be exempted, before its fixed
deadline. CI/bootstrap/future workflows have no exemption. This is not merge,
deployment or canon activation authorization. Uncertainty fails the static job.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import subprocess
import sys

APPROVAL_SOURCE = 'c50f03ddefc08223e30d1595339b7e363b8f1b9c'
APPROVAL_BASE = '9073b891faacb3b48d6f9976117e79d6a6d92e23'
EXPIRES_AT = datetime(2026, 10, 21, tzinfo=timezone.utc)  # exclusive; no env override
# path, status, old mode, new mode, old blob, new blob. All six are required.
APPROVED_DELTA = (
    ('bridge_school_api/l1_canonical_runtime.py','M','100644','100644',
     'a5bb7b7b214f98191a760feea1b0c0e9c33ae0e1','7c1e82a1c1792bc3d476d5e214ebc2aba3661880'),
    ('bridge_school_api/l1_canonical_runtime_v3.py','M','100644','100644',
     'f2f36a3205dbf8b4c701699ccc44585f96c561bd','c4719a33d198f5ed9a96ebb765bd55f77e3020b2'),
    ('bridge_school_api/l1_canonical_runtime_v4.py','M','100644','100644',
     'c7edeb45c9181e864df01bea33cc469dc3595a8e','431675261bac129b0ba08ce42583b001d3219e50'),
    ('tests/test_l1_canonical_runtime.py','M','100644','100644',
     '73a198bd7e15bc21a98f0ed5e28a23d5336bd221','0cf6fff4293b9f848d9cb085dd8f096a5da056b5'),
    ('tests/test_l1_canonical_runtime_v3.py','M','100644','100644',
     '560da9203acbf496ed6a5f8b7c8345b9dffa954d','7e40a3bf52468760013ad129a73fd4cf0492b7ae'),
    ('tests/test_l1_observation_safety.py','A','000000','100644',
     '0000000000000000000000000000000000000000','05da22ceb627d24b18458583590764f1bdc6441e'),
)
_HEADER = re.compile(rb':([0-7]{6}) ([0-7]{6}) ([0-9a-f]{40}) ([0-9a-f]{40}) ([A-Z][0-9]*)')
_ZERO = '0'*40


class ScopeError(ValueError):
    """Fixed diagnostic code only; no Git/environment/service data escapes."""


def parse_raw_diff(data: bytes) -> tuple[tuple[str, ...], ...]:
    if type(data) is not bytes or not data or len(data)>1024*1024 or not data.endswith(b'\0'):
        raise ScopeError('unknown_or_empty_diff')
    fields = data[:-1].split(b'\0')
    if len(fields)%2:
        raise ScopeError('malformed_raw_diff')
    entries=[];seen=set()
    for header,raw_path in zip(fields[::2],fields[1::2]):
        match=_HEADER.fullmatch(header)
        if not match:
            raise ScopeError('malformed_raw_diff')
        old_mode,new_mode,old_blob,new_blob,status=(x.decode('ascii') for x in match.groups())
        if status not in ('A','M'):
            raise ScopeError('unsupported_change_status')
        if (new_mode not in ('100644','100755') or new_blob==_ZERO
                or (status=='A' and (old_mode!='000000' or old_blob!=_ZERO))
                or (status=='M' and (old_mode not in ('100644','100755') or old_blob==_ZERO
                                     or (old_mode,old_blob)==(new_mode,new_blob)))):
            raise ScopeError('invalid_raw_identity')
        try:
            path=raw_path.decode('utf-8',errors='strict')
        except UnicodeError:
            raise ScopeError('malformed_path') from None
        if (not path or any(ord(c)<32 or ord(c)==127 for c in path) or '\\' in path
                or any(p in ('','.','..') for p in path.split('/')) or path in seen):
            raise ScopeError('malformed_path')
        seen.add(path);entries.append((path,status,old_mode,new_mode,old_blob,new_blob))
    return tuple(entries)


def classify_entries(entries, *, now=None) -> tuple[bool,str]:
    paths={e[0] for e in entries};approved_paths={e[0] for e in APPROVED_DELTA}
    if not paths & approved_paths:
        return True,'ordinary_read_only_smoke'
    # Partial, changed or mixed L1 edits are neither an exemption nor an
    # automatic new credential lane. They require a newly reviewed exact scope.
    if len(entries)!=len(APPROVED_DELTA) or set(entries)!=set(APPROVED_DELTA):
        raise ScopeError('accepted_L1_delta_mismatch')
    instant=datetime.now(timezone.utc) if now is None else now
    if not isinstance(instant,datetime) or instant.utcoffset() is None:
        raise ScopeError('approval_clock_unverified')
    if instant>=EXPIRES_AT:
        raise ScopeError('accepted_L1_approval_expired')
    return False,'exact_accepted_L1_delta'


def classify_diff(data: bytes, *, now=None) -> tuple[bool,str]:
    return classify_entries(parse_raw_diff(data),now=now)


def main_push_scope(environ, run=subprocess.run, *, now=None) -> tuple[bool,str]:
    event,ref=environ.get('CI_EVENT_NAME'),environ.get('CI_REF')
    if type(event) is not str or type(ref) is not str or not ref:
        raise ScopeError('missing_event_context')
    if event=='pull_request' or (event=='push' and ref.startswith('refs/heads/') and ref!='refs/heads/main'):
        return False,'not_main_push'
    if event!='push' or ref!='refs/heads/main':
        raise ScopeError('unsupported_event_context')
    before,after=environ.get('CI_BEFORE'),environ.get('CI_AFTER')
    if (type(before) is not str or type(after) is not str
            or not re.fullmatch('[0-9a-f]{40}',before) or not re.fullmatch('[0-9a-f]{40}',after)
            or before==_ZERO or after==_ZERO or before==after):
        raise ScopeError('invalid_push_range')

    def git(*args):
        result=run(['git',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=20).stdout
        if type(result) is not bytes:
            raise ScopeError('unverified_git_output')
        return result

    try:
        if git('rev-parse','HEAD').strip()!=after.encode('ascii'):
            raise ScopeError('checkout_not_event_after')
        if git('rev-parse','--is-shallow-repository').strip()!=b'false':
            raise ScopeError('shallow_history')
        for commit in (before,after):
            if git('cat-file','-t',commit).strip()!=b'commit':
                raise ScopeError('missing_commit_object')
        git('merge-base','--is-ancestor',before,after)
        entries=parse_raw_diff(git('diff','--raw','--no-abbrev','--no-renames','-z',before,after,'--'))
        decision=classify_entries(entries,now=now)
        if not decision[0]:
            # A tree containing references to unavailable blobs is not complete
            # evidence for the content-bound exemption.
            blobs={e[i] for e in entries for i in (4,5)}-{_ZERO}
            for blob in sorted(blobs):
                if git('cat-file','-t',blob).strip()!=b'blob':
                    raise ScopeError('approved_blob_unavailable')
        return decision
    except (subprocess.SubprocessError,OSError,UnicodeError):
        raise ScopeError('unverified_push_diff') from None


def main(environ=None, run=subprocess.run, *, now=None) -> int:
    try:
        allowed,reason=main_push_scope(os.environ if environ is None else environ,run,now=now)
        status='run' if allowed else ('not_applicable' if reason=='not_main_push' else 'exempt')
        code=0
    except ScopeError as exc:
        allowed,reason,status,code=False,str(exc),'error',2
    print('allow_smoke='+('true' if allowed else 'false'))
    print('scope_status='+status)
    print('reason='+reason)
    print('production-smoke-scope: '+status+': '+reason,file=sys.stderr)
    return code


if __name__=='__main__':
    raise SystemExit(main())
