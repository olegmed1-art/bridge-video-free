"""The producer's literal summaries must pass the real, unchanged receiver."""
from __future__ import annotations
import json
import re
from pathlib import Path
import pytest
from oracle_autopilot.github_codex_callback import CallbackContractError, parse_terminal_event
from test_oracle_autopilot_github_codex_callback import _terminal_event

MIGRATION = Path(__file__).resolve().parents[1] / 'database/migrations/0336_autopilot_terminal_summary_contract.sql'
CONTRACT = json.loads(re.search(r"contract jsonb := '(.*?)'::jsonb", MIGRATION.read_text()).group(1))


def terminal(status: str, summary: str) -> dict:
    event = _terminal_event()
    body = event['comment']['body']
    body = body.replace('status=SUCCEEDED', f'status={status}')
    body = body.replace('result_code=READ_ONLY_AUDIT_COMPLETE', 'result_code=TARGET_PR_NOT_UPDATED' if status == 'BLOCKED' else 'result_code=READ_ONLY_AUDIT_COMPLETE')
    event['comment']['body'] = re.sub(r'^summary=.*$', lambda _: 'summary=' + summary, body, flags=re.MULTILINE)
    return event


@pytest.mark.parametrize('status', ['SUCCEEDED', 'BLOCKED'])
def test_canonical_summary_is_accepted_without_changing_outcome(status):
    result = parse_terminal_event(terminal(status, CONTRACT[status]))
    assert result.status == status
    assert result.result_code == ('TARGET_PR_NOT_UPDATED' if status == 'BLOCKED' else 'READ_ONLY_AUDIT_COMPLETE')
    assert result.summary == CONTRACT[status]


@pytest.mark.parametrize('summary', [
    'GitHub credentials were unavailable.', 'Missing token.', 'secret', 'password',
    'api key unavailable', 'private-key unavailable', 'https://example.com',
    'a@example.com', 'a'*32, 'Z'*40, 'x'*161, 'bad\x7f',
])
def test_unsafe_summary_remains_rejected(summary):
    with pytest.raises(CallbackContractError, match='CODEX_RESULT_SUMMARY_INVALID'):
        parse_terminal_event(terminal('BLOCKED', summary))


def test_valid_summary_does_not_bypass_bot_identity():
    event = terminal('SUCCEEDED', CONTRACT['SUCCEEDED'])
    event['comment']['user']['id'] = 1
    with pytest.raises(CallbackContractError, match='CODEX_RESULT_ACTOR_INVALID'):
        parse_terminal_event(event)


def test_diagnostics_remain_outside_summary_not_discarded():
    assert 'before the result envelope' in CONTRACT['instruction']
    assert 'preserve status and result_code' in CONTRACT['instruction']
