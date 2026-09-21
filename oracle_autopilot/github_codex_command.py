"""Pure command rendering/readback for the owner bridge; no network or writes.

Authorization is separate: a committed 0370 one-shot claim, fresh GitHub and
database reads, and the pinned owner connector are required before any POST.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .github_codex_callback import (
    COMMAND_FIELDS, COMMAND_MARKER, OWNER_LOGIN, OWNER_ID, CODEX_APP_ID,
    CODEX_APP_SLUG, REPOSITORY_ID, REPOSITORY, TASK_COMMAND_PREFIX,
    CallbackContractError, parse_command_event,
)

POLICY_PATH = Path(__file__).resolve().parents[1] / 'ops/autopilot/codex-execution-policy-v1.txt'
POLICY_SHA256 = '123c9a6f82b69bfe3c46bacf65152c94d77b31b72e00a6b5ffa66ac69e80c902'


def command_sha256(body: str) -> str:
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _event(comment: dict, target_pr: int) -> dict:
    return {'action': 'created',
            'repository': {'id': REPOSITORY_ID, 'full_name': REPOSITORY},
            'issue': {'number': target_pr, 'pull_request': {
                'url': f'https://api.github.com/repos/{REPOSITORY}/pulls/{target_pr}'}},
            'comment': comment}


def render_command(binding: dict) -> str:
    """Render deterministically; reject missing/malformed or injected fields.

    The synthetic parser record below checks syntax only, not owner identity.
    Real readback must validate the actual returned GitHub record separately.
    """
    if (binding.get('repository') != REPOSITORY
            or binding.get('mode') not in ('READ_ONLY', 'REPAIR')
            or binding.get('execution_scope') != 'REPOSITORY'
            or type(binding.get('can_repair')) is not bool
            or (binding['mode'] == 'REPAIR' and not binding['can_repair'])
            or not isinstance(binding.get('task_spec_json'), dict)):
        raise CallbackContractError('CODEX_RENDER_SCOPE_INVALID')
    values = {}
    for key in COMMAND_FIELDS:
        if key not in binding:
            raise CallbackContractError('CODEX_RENDER_FIELD_MISSING')
        value = binding[key]
        if key == 'task_spec_json':
            value = json.dumps(value, sort_keys=True, separators=(',', ':'),
                               ensure_ascii=True, allow_nan=False)
        elif key == 'can_repair':
            value = 'true' if value else 'false'
        elif key in ('dispatch_pr', 'dispatch_epoch', 'target_pr'):
            if type(value) is not int:
                raise CallbackContractError('CODEX_RENDER_INTEGER_INVALID')
            value = str(value)
        if not isinstance(value, str) or any(ord(ch) < 32 for ch in value):
            raise CallbackContractError('CODEX_RENDER_FIELD_INVALID')
        values[key] = value
    policy = POLICY_PATH.read_bytes()
    if hashlib.sha256(policy).hexdigest() != POLICY_SHA256:
        raise CallbackContractError('CODEX_POLICY_DIGEST_INVALID')
    body = '\n'.join([TASK_COMMAND_PREFIX, '', COMMAND_MARKER,
                       *(f'{key}={values[key]}' for key in COMMAND_FIELDS), '',
                       policy.decode('utf-8').format(**values).rstrip('\n')])
    # Reuse the receiving parser, so sender/receiver cannot drift silently.
    parse_command_event(_event({
        'id': 1, 'created_at': '2000-01-01T00:00:00Z', 'body': body,
        'issue_url': f'https://api.github.com/repos/{REPOSITORY}/issues/{binding["target_pr"]}',
        'user': {'login': OWNER_LOGIN, 'id': OWNER_ID},
        'author_association': 'OWNER',
        'performed_via_github_app': {'slug': CODEX_APP_SLUG, 'id': CODEX_APP_ID},
    }, binding['target_pr']))
    return body


def readback_command(comments: list[dict], *, complete: bool, binding: dict,
                     expected_body: str) -> int | None:
    """All-page exact-body/identity readback; None is UNKNOWN, never retry.

    Caller must attest successful complete pagination on the exact target PR.
    Any same-dispatch mention with a conflicting body/actor is fail-closed.
    This proves command existence, not ACK, terminal, or ownership of the POST.
    """
    if complete is not True:
        raise CallbackContractError('CODEX_COMMENT_PAGINATION_INCOMPLETE')
    if expected_body != render_command(binding):
        raise CallbackContractError('CODEX_READBACK_EXPECTED_BODY_INVALID')
    matches = [c for c in comments if binding['dispatch_id'] in str(c.get('body', ''))]
    if not matches:
        return None
    if len(matches) != 1:
        raise CallbackContractError('CODEX_DUPLICATE_CONFLICT')
    comment = matches[0]
    if (comment.get('body') != expected_body
            or comment.get('issue_url') !=
            f'https://api.github.com/repos/{REPOSITORY}/issues/{binding["target_pr"]}'):
        raise CallbackContractError('CODEX_READBACK_CONFLICT')
    return parse_command_event(_event(comment, binding['target_pr'])).comment_id
