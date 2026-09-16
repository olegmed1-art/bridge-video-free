from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from oracle_autopilot import github_codex_publication as publication
from oracle_autopilot import github_codex_publication_permit as permit
from oracle_autopilot.github_codex_callback import CallbackContractError
from test_oracle_autopilot_github_codex_callback import _event, _terminal_event


def fixture():
    owner = _event()
    owner["comment"]["updated_at"] = owner["comment"]["created_at"]
    body = owner["comment"]["body"].replace("mode=READ_ONLY", "mode=REPAIR")
    body = body.replace('task_spec_json={"fixture":"codex-event-cycle"}',
                        'task_spec_json={"expected_changed_files":["tests/test_example.py"]}')
    owner["comment"]["body"] = body
    command = publication.parse_command_event(owner)
    file = {"path": "tests/test_example.py", "base_blob_sha": publication.blob_sha("before\n"),
            "content": "after\n", "sha256": hashlib.sha256(b"after\n").hexdigest()}
    payload = {key: getattr(command, key) for key in publication.BINDING_FIELDS}
    payload.update(command_comment_id=command.comment_id, files=[file])
    request_event = _terminal_event()
    request_event["comment"]["body"] = publication.MARKER + "\n" + json.dumps(payload)
    request_event["comment"]["updated_at"] = request_event["comment"]["created_at"]
    request = publication.parse_publication_event(request_event)
    approval_payload = {
        "command_comment_id": command.comment_id,
        "publication_comment_id": request.comment_id,
        "payload_sha256": request.fingerprint,
        "dispatch_id": command.dispatch_id,
        "dispatch_epoch": command.dispatch_epoch,
        "role": command.role,
        "task_fingerprint": command.task_fingerprint,
        "target_pr": command.target_pr,
        "expected_head_sha": command.expected_head_sha,
    }
    approval = {
        "id": 5_669_799_001,
        "created_at": "2026-09-14T19:40:00Z",
        "updated_at": "2026-09-14T19:40:00Z",
        "issue_url": owner["comment"]["issue_url"],
        "author_association": "OWNER",
        "user": {"login": "olegmed1-art", "id": 315_099_490},
        "performed_via_github_app": None,
        "body": permit.APPROVAL_MARKER + "\n" + json.dumps(approval_payload),
    }
    return owner, request_event, approval, approval_payload


class FakeGitHub:
    def __init__(self, owner, request, approval):
        self.records = {
            owner["comment"]["id"]: deepcopy(owner["comment"]),
            request["comment"]["id"]: deepcopy(request["comment"]),
            approval["id"]: deepcopy(approval),
        }
        self.counts = {key: 0 for key in self.records}
        self.change_id = None

    def call(self, path, data=None):
        assert data is None
        identifier = int(path.rsplit("/", 1)[1])
        self.counts[identifier] += 1
        value = deepcopy(self.records[identifier])
        if self.change_id == identifier and self.counts[identifier] >= 2:
            value["body"] += "\n"
        return value


def run_verify(data):
    owner, request, approval, _ = data
    github = FakeGitHub(owner, request, approval)
    result = permit.verify(github, owner["comment"]["id"], request["comment"]["id"], approval["id"])
    return github, result


def test_distinct_direct_owner_approval_binds_exact_records():
    data = fixture()
    github, result = run_verify(data)
    owner, request, approval, _ = data
    evidence = result["evidence"]
    assert evidence["command_comment_id"] == owner["comment"]["id"]
    assert evidence["publication_comment_id"] == request["comment"]["id"]
    assert evidence["approval_comment_id"] == approval["id"]
    assert evidence["payload_sha256"] == publication.parse_publication_event(request).fingerprint
    assert set(result["record_sha256"]) == {"command", "publication", "approval"}
    assert all(count == 2 for count in github.counts.values())


@pytest.mark.parametrize("key,value", [
    ("dispatch_id", "12345678-1234-4234-8234-123456789012"),
    ("dispatch_epoch", 999),
    ("task_fingerprint", "0" * 64),
    ("target_pr", 999999),
    ("expected_head_sha", "0" * 40),
    ("payload_sha256", "0" * 64),
    ("publication_comment_id", 42),
])
def test_wrong_task_dispatch_head_or_payload_rejected(key, value):
    owner, request, approval, approval_payload = fixture()
    approval_payload[key] = value
    approval["body"] = permit.APPROVAL_MARKER + "\n" + json.dumps(approval_payload)
    github = FakeGitHub(owner, request, approval)
    with pytest.raises(CallbackContractError, match="PERMIT_APPROVAL_BINDING_INVALID"):
        permit.verify(github, owner["comment"]["id"], request["comment"]["id"], approval["id"])


def test_copied_connector_envelope_is_not_direct_owner_approval():
    owner, request, approval, _ = fixture()
    approval["performed_via_github_app"] = deepcopy(owner["comment"]["performed_via_github_app"])
    github = FakeGitHub(owner, request, approval)
    with pytest.raises(CallbackContractError, match="PERMIT_APPROVAL_APP_MEDIATED"):
        permit.verify(github, owner["comment"]["id"], request["comment"]["id"], approval["id"])


def test_edited_owner_approval_is_rejected_even_if_body_matches():
    owner, request, approval, _ = fixture()
    approval["updated_at"] = "2026-09-14T19:40:01Z"
    github = FakeGitHub(owner, request, approval)
    with pytest.raises(CallbackContractError, match="PERMIT_APPROVAL_EDITED"):
        permit.verify(github, owner["comment"]["id"], request["comment"]["id"], approval["id"])


def test_changed_record_on_mandatory_refetch_fails_closed():
    owner, request, approval, _ = fixture()
    github = FakeGitHub(owner, request, approval)
    github.change_id = request["comment"]["id"]
    with pytest.raises(CallbackContractError, match="PERMIT_RECORD_CHANGED"):
        permit.verify(github, owner["comment"]["id"], request["comment"]["id"], approval["id"])


def test_comment_id_reuse_is_rejected_before_fetch():
    owner, request, approval, _ = fixture()
    github = FakeGitHub(owner, request, approval)
    with pytest.raises(CallbackContractError, match="PERMIT_COMMENT_REUSE_INVALID"):
        permit.verify(github, owner["comment"]["id"], request["comment"]["id"], request["comment"]["id"])


class Cursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        evidence = json.loads(params[0])
        self.row = ({"state": "ISSUED", "dispatch_id": evidence["dispatch_id"]},)

    def fetchone(self):
        return self.row


def test_issue_uses_only_owner_sql_issuer_with_exact_evidence():
    _, verified = run_verify(fixture())
    cursor = Cursor()
    result = permit.issue(cursor, verified, 300)
    assert result["state"] == "ISSUED"
    sql, params = cursor.calls[0]
    assert sql == "SELECT autopilot.issue_codex_publication_permit(%s::jsonb,%s)"
    assert json.loads(params[0]) == verified["evidence"]
    assert params[1] == 300


def test_issuer_is_disconnected_from_runtime_and_actions():
    workflow = Path(".github/workflows/autopilot-codex-event-callback.yml").read_text()
    source = Path(permit.__file__).read_text()
    assert "github_codex_publication_permit" not in workflow
    assert "issue_codex_publication_permit" not in workflow
    assert "AUTOPILOT_OWNER_DATABASE_URL" not in workflow
    assert "graphql" not in source.lower()
    assert "createCommitOnBranch" not in source
