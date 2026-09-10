from __future__ import annotations

import copy

import psycopg
import pytest

from oracle_autopilot.github_role_callback import (
    CallbackContractError,
    ingest_callback_with_retry,
    parse_issue_comment_event,
    validate_callback_dsn,
)


BODY = """AUTOPILOT_RESULT_V1
dispatch_id=462b8120-9039-4395-bbfb-2b4fbabdc486
dispatch_epoch=1
role=VIDEO
task_fingerprint=03cba6f982ba8f63c13bc37d4e9b443f7e83b92eae311cdba6be76568dd23676
target_pr=1125
status=BLOCKED
result_code=EXACT_HEAD_FAIL_CLOSED_GAP
target_head_sha=0c60d122ef93070f5566723ad8ca9123717dbcae
summary=Exact-head read-only assessment remains blocked on one public contract gap."""


def _event() -> dict[str, object]:
    return {
        "action": "created",
        "repository": {
            "id": 1330085090,
            "full_name": "olegmed1-art/bridge-video-free",
        },
        "issue": {
            "number": 1150,
            "pull_request": {
                "url": "https://api.github.com/repos/olegmed1-art/bridge-video-free/pulls/1150"
            },
        },
        "comment": {
            "id": 5620277568,
            "issue_url": "https://api.github.com/repos/olegmed1-art/bridge-video-free/issues/1150",
            "author_association": "OWNER",
            "user": {"login": "olegmed1-art", "id": 315099490},
            "performed_via_github_app": {
                "slug": "chatgpt-codex-connector",
                "id": 1144995,
            },
            "body": BODY,
        },
    }


def test_parses_live_shaped_provider_authenticated_callback():
    callback = parse_issue_comment_event(_event())
    assert callback.provider_event_id == "github-comment:5620277568"
    assert callback.dispatch_epoch == 1
    assert callback.role == "VIDEO"
    assert callback.status == "BLOCKED"
    assert len(callback.payload_fingerprint) == 64


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("repository", "id"), 1, "REPOSITORY_INVALID"),
        (("issue", "number"), 881, "MAILBOX_INVALID"),
        (("comment", "user", "id"), 1, "ACTOR_INVALID"),
        (("comment", "performed_via_github_app", "id"), 1, "APP_INVALID"),
        (("comment", "body"), BODY + "\nextra=value", "BODY_INVALID"),
    ],
)
def test_rejects_wrong_provider_identity_or_schema(path, value, code):
    event = copy.deepcopy(_event())
    target = event
    for key in path[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(CallbackContractError, match=code):
        parse_issue_comment_event(event)


def test_rejects_secret_like_summary():
    event = _event()
    event["comment"]["body"] = BODY.replace(
        "Exact-head read-only assessment remains blocked on one public contract gap.",
        "api_key=must-not-enter-public-callback",
    )
    with pytest.raises(CallbackContractError, match="SUMMARY_INVALID"):
        parse_issue_comment_event(event)


@pytest.mark.parametrize(
    "unsafe_summary",
    [
        "Details at https://private.example.invalid/report",
        "Contact operator@example.invalid",
        "credential material must never enter the callback",
        "0123456789abcdef0123456789abcdef",
        "x" * 161,
    ],
)
def test_rejects_public_summary_leakage_shapes(unsafe_summary):
    event = _event()
    event["comment"]["body"] = BODY.replace(
        "Exact-head read-only assessment remains blocked on one public contract gap.",
        unsafe_summary,
    )
    with pytest.raises(CallbackContractError, match="SUMMARY_INVALID"):
        parse_issue_comment_event(event)


def test_callback_dsn_requires_dedicated_direct_neon_login():
    valid = (
        "postgresql://autopilot_callback_login:secret@"
        "ep-callback.eu-central-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    assert validate_callback_dsn(valid) == valid
    for invalid in (
        valid.replace("autopilot_callback_login", "neondb_owner"),
        valid.replace("ep-callback.", "ep-callback-pooler."),
        valid.replace("channel_binding=require", "channel_binding=disable"),
    ):
        with pytest.raises(CallbackContractError, match="CALLBACK_DSN_INVALID"):
            validate_callback_dsn(invalid)


def test_callback_retries_only_until_dispatch_is_marked_sent(monkeypatch):
    callback = parse_issue_comment_event(_event())
    outcomes = [
        psycopg.OperationalError("AUTOPILOT_CALLBACK_DISPATCH_NOT_SENT"),
        psycopg.OperationalError("AUTOPILOT_CALLBACK_DISPATCH_NOT_SENT"),
        (True, "DONE"),
    ]
    sleeps: list[int] = []

    def fake_ingest(_dsn, _callback):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        "oracle_autopilot.github_role_callback.ingest_callback", fake_ingest
    )
    assert ingest_callback_with_retry(
        "unused", callback, sleeper=sleeps.append
    ) == (True, "DONE")
    assert sleeps == [1, 2]


def test_callback_does_not_retry_other_database_failures(monkeypatch):
    callback = parse_issue_comment_event(_event())
    sleeps: list[int] = []

    def fail_closed(_dsn, _callback):
        raise psycopg.OperationalError("AUTOPILOT_CALLBACK_BINDING_INVALID")

    monkeypatch.setattr(
        "oracle_autopilot.github_role_callback.ingest_callback", fail_closed
    )
    with pytest.raises(psycopg.OperationalError, match="BINDING_INVALID"):
        ingest_callback_with_retry("unused", callback, sleeper=sleeps.append)
    assert sleeps == []


def test_callback_retry_budget_is_bounded(monkeypatch):
    callback = parse_issue_comment_event(_event())
    sleeps: list[int] = []

    def never_sent(_dsn, _callback):
        raise psycopg.OperationalError("AUTOPILOT_CALLBACK_DISPATCH_NOT_SENT")

    monkeypatch.setattr(
        "oracle_autopilot.github_role_callback.ingest_callback", never_sent
    )
    with pytest.raises(psycopg.OperationalError, match="DISPATCH_NOT_SENT"):
        ingest_callback_with_retry("unused", callback, sleeper=sleeps.append)
    assert sleeps == [1, 2, 4, 8]
