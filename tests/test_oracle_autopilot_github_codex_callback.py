from __future__ import annotations

import copy
import io
import json

import pytest

from oracle_autopilot.github_codex_callback import (
    CODEX_BOT_ID,
    CODEX_BOT_LOGIN,
    CallbackContractError,
    fetch_codex_ack,
    parse_command_event,
    parse_terminal_event,
    verify_pr_head,
)


COMMAND_BODY = """@codex

SLAVIK_CODEX_DISPATCH_V1
dispatch_id=6275443a-5868-4c1f-9406-c0d72b8068bd
dispatch_pr=1429
dispatch_epoch=55
role=AUTOPILOT
task_fingerprint=197a0c833e928a66d8e38b0c82f43e64bcc31c78ec8f8bc83602a82e1d35474e
target_pr=1150
expected_head_sha=2ceb48716988ec9cbd01be438a0ebf8b46836667
mode=READ_ONLY
execution_scope=REPOSITORY
can_repair=true
task_kind=REPOSITORY_AUDIT
objective=Audit the exact target head and report only bounded evidence.
task_spec_json={"fixture":"codex-event-cycle"}

Verify the exact target head before analysis. Do not merge or deploy.

End with one AUTOPILOT_CODEX_RESULT_V1 block."""

TERMINAL_BODY = """## Bounded result

The exact-head audit completed without mutation.

AUTOPILOT_CODEX_RESULT_V1
dispatch_id=6275443a-5868-4c1f-9406-c0d72b8068bd
dispatch_epoch=55
role=AUTOPILOT
task_fingerprint=197a0c833e928a66d8e38b0c82f43e64bcc31c78ec8f8bc83602a82e1d35474e
target_pr=1150
status=SUCCEEDED
result_code=READ_ONLY_AUDIT_COMPLETE
target_head_sha=2ceb48716988ec9cbd01be438a0ebf8b46836667
summary=Exact-head read-only audit completed with bounded evidence and no mutation.

 [View task →](https://chatgpt.com/s/cd_6aa84d08df788191a0cc124fa4fe6b02)"""


def _event(body: str = COMMAND_BODY) -> dict[str, object]:
    return {
        "action": "created",
        "repository": {
            "id": 1_330_085_090,
            "full_name": "olegmed1-art/bridge-video-free",
        },
        "issue": {
            "number": 1150,
            "pull_request": {
                "url": "https://api.github.com/repos/olegmed1-art/bridge-video-free/pulls/1150"
            },
        },
        "comment": {
            "id": 5_669_716_994,
            "created_at": "2026-09-14T19:37:32Z",
            "issue_url": "https://api.github.com/repos/olegmed1-art/bridge-video-free/issues/1150",
            "author_association": "OWNER",
            "user": {"login": "olegmed1-art", "id": 315_099_490},
            "performed_via_github_app": {
                "slug": "chatgpt-codex-connector",
                "id": 1_144_995,
            },
            "body": body,
        },
    }


def _terminal_event() -> dict[str, object]:
    event = _event(TERMINAL_BODY)
    comment = event["comment"]
    comment["id"] = 5_669_745_748  # type: ignore[index]
    comment["author_association"] = "NONE"  # type: ignore[index]
    comment["user"] = {"login": CODEX_BOT_LOGIN, "id": CODEX_BOT_ID}  # type: ignore[index]
    return event


def test_parses_owner_app_command_on_exact_target_pr():
    command = parse_command_event(_event())
    assert command.comment_id == 5_669_716_994
    assert command.command_pr == command.target_pr == 1150
    assert command.dispatch_pr == 1429
    assert command.execution_scope == "REPOSITORY"
    assert command.can_repair is True
    assert command.task_spec == {"fixture": "codex-event-cycle"}


def test_explicit_task_command_preserves_legacy_dispatch_binding():
    explicit = COMMAND_BODY.replace("@codex\n", "@codex execute this task\n", 1)
    assert parse_command_event(_event(explicit)) == parse_command_event(_event())


@pytest.mark.parametrize("prefix", ["@codex review", "@codex security review", "@codex arbitrary"])
def test_command_rejects_review_and_unrecognized_prefixes(prefix):
    with pytest.raises(CallbackContractError, match="COMMAND_BODY_INVALID"):
        parse_command_event(_event(COMMAND_BODY.replace("@codex\n", prefix + "\n", 1)))


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("repository", "id"), 1, "REPOSITORY_INVALID"),
        (("issue", "number"), 1429, "PR_INVALID"),
        (("comment", "user", "id"), 1, "COMMAND_ACTOR_INVALID"),
        (("comment", "author_association"), "COLLABORATOR", "COMMAND_ACTOR_INVALID"),
        (("comment", "performed_via_github_app", "id"), 1, "COMMAND_APP_INVALID"),
    ],
)
def test_command_rejects_wrong_event_or_provider_identity(path, value, code):
    event = copy.deepcopy(_event())
    target = event
    for key in path[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(CallbackContractError, match=code):
        parse_command_event(event)


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("role=AUTOPILOT", "role=autopilot", "ROLE_INVALID"),
        ("mode=READ_ONLY", "mode=WRITE", "MODE_INVALID"),
        ("can_repair=true", "can_repair=yes", "REPAIR_POLICY_INVALID"),
        (
            "task_spec_json={\"fixture\":\"codex-event-cycle\"}",
            "task_spec_json=[]",
            "TASK_SPEC_INVALID",
        ),
        ("dispatch_pr=1429", "target_pr=1150", "COMMAND_BODY_INVALID"),
    ],
)
def test_command_rejects_malformed_or_reordered_binding(old, new, code):
    with pytest.raises(CallbackContractError, match=code):
        parse_command_event(_event(COMMAND_BODY.replace(old, new, 1)))


def test_parses_pinned_codex_app_terminal_with_provider_view_link():
    terminal = parse_terminal_event(_terminal_event())
    assert terminal.event_pr == terminal.target_pr == 1150
    assert terminal.status == "SUCCEEDED"
    assert terminal.result_code == "READ_ONLY_AUDIT_COMPLETE"
    assert terminal.delivery_id == "github-codex-result:5669745748"
    assert len(terminal.payload_fingerprint) == 64


def test_terminal_rejects_owner_or_unpinned_bot():
    event = _terminal_event()
    event["comment"]["user"] = {  # type: ignore[index]
        "login": "lookalike[bot]",
        "id": CODEX_BOT_ID,
    }
    with pytest.raises(CallbackContractError, match="RESULT_ACTOR_INVALID"):
        parse_terminal_event(event)


@pytest.mark.parametrize(
    "unsafe_summary",
    [
        "Details at https://private.example.invalid/report",
        "api_key must never be exposed",
        "0123456789abcdef0123456789abcdef",
        "x" * 161,
    ],
)
def test_terminal_rejects_unsafe_public_summary(unsafe_summary):
    event = _terminal_event()
    event["comment"]["body"] = TERMINAL_BODY.replace(  # type: ignore[index]
        "Exact-head read-only audit completed with bounded evidence and no mutation.",
        unsafe_summary,
    )
    with pytest.raises(CallbackContractError, match="RESULT_SUMMARY_INVALID"):
        parse_terminal_event(event)


class _Response:
    def __init__(self, url: str, values: list[dict[str, object]]) -> None:
        self.status = 200
        self._url = url
        self._body = io.BytesIO(json.dumps(values).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, limit: int) -> bytes:
        return self._body.read(limit)


def test_ack_polls_until_exact_codex_bot_eyes_reaction():
    command = parse_command_event(_event())
    calls: list[str] = []
    sleeps: list[int] = []
    responses = [
        [],
        [
            {
                "id": 417_240_549,
                "content": "eyes",
                "created_at": "2026-09-14T19:37:43Z",
                "user": {"login": CODEX_BOT_LOGIN, "id": CODEX_BOT_ID},
            }
        ],
    ]

    def opener(request, timeout):
        assert timeout == 10
        assert request.get_header("Authorization") == "Bearer test-token"
        calls.append(request.full_url)
        return _Response(request.full_url, responses.pop(0))

    ack = fetch_codex_ack(
        command,
        "test-token",
        opener=opener,
        sleeper=sleeps.append,
        delays=(0, 1),
    )
    assert len(calls) == 2
    assert sleeps == [1]
    assert ack.reaction_id == 417_240_549
    assert ack.delivery_id == "github-codex-ack:417240549"
    assert ack.body["command_comment_id"] == 5_669_716_994
    assert len(ack.payload_fingerprint) == 64


def test_ack_rejects_lookalike_and_exhausts_bounded_budget():
    command = parse_command_event(_event())
    sleeps: list[int] = []

    def opener(request, _timeout):
        return _Response(
            request.full_url,
            [
                {
                    "id": 1,
                    "content": "eyes",
                    "created_at": "2026-09-14T19:37:43Z",
                    "user": {"login": CODEX_BOT_LOGIN, "id": 1},
                }
            ],
        )

    with pytest.raises(CallbackContractError, match="ACK_NOT_FOUND"):
        fetch_codex_ack(
            command,
            "test-token",
            opener=opener,
            sleeper=sleeps.append,
            delays=(0, 1, 2),
        )
    assert sleeps == [1, 2]


@pytest.mark.parametrize(
    "base_ref",
    ["main", "codex/oracle-autopilot-lite-shadow", "fix/uv-intake-structured-smoke-20260830"],
)
def test_live_target_pr_gate_accepts_open_exact_head_on_any_base(base_ref):
    expected = "2ceb48716988ec9cbd01be438a0ebf8b46836667"
    payload = {
        "number": 1150,
        "state": "open",
        "head": {"sha": expected},
        "base": {"ref": base_ref},
    }

    def exact_opener(request, timeout):
        assert timeout == 10
        response = _Response(request.full_url, [])
        response._body = io.BytesIO(json.dumps(payload).encode())
        return response

    verify_pr_head(1150, expected, "test-token", opener=exact_opener)

    for invalid_fields in (
        {"head": {"sha": "a" * 40}},
        {"state": "closed"},
        {"number": 1429},
    ):
        original = copy.deepcopy(payload)
        payload.update(invalid_fields)
        with pytest.raises(CallbackContractError, match="PR_HEAD_MISMATCH"):
            verify_pr_head(1150, expected, "test-token", opener=exact_opener)
        payload.clear()
        payload.update(original)
