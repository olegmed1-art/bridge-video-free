from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from oracle_autopilot import github_codex_publication as pub
from test_oracle_autopilot_github_codex_callback import _event, _terminal_event


def fixture():
    owner = _event()
    body = owner["comment"]["body"].replace("mode=READ_ONLY", "mode=REPAIR")
    body = body.replace('task_spec_json={"fixture":"codex-event-cycle"}',
                        'task_spec_json={"expected_changed_files":["tests/test_example.py"]}')
    owner["comment"]["body"] = body
    command = pub.parse_command_event(owner)
    file = {"path": "tests/test_example.py", "base_blob_sha": pub.blob_sha("before\n"),
            "content": "after\n", "sha256": hashlib.sha256(b"after\n").hexdigest()}
    payload = {key: getattr(command, key) for key in pub.BINDING_FIELDS}
    payload.update(command_comment_id=command.comment_id, files=[file])
    event = _terminal_event()
    event["comment"]["body"] = pub.MARKER + "\n" + json.dumps(payload)
    return owner, command, payload, event


class FakeCursor:
    def __init__(self, state="SENT", deny=False):
        self.state, self.deny, self.calls = state, deny, []
        self.terminal = None
        self.terminal_result = (True, "DONE")

    def execute(self, sql, params):
        self.calls.append((sql, params))
        if "authorize_codex_publication" in sql:
            if self.deny:
                raise pub.CallbackContractError("PUBLICATION_PROVENANCE_PERMIT_REQUIRED")
            self.row = ({"state": self.state, "terminal_body": self.terminal},)
        else:
            self.terminal = json.loads(params[-1])
            self.row = self.terminal_result

    def fetchone(self):
        return self.row


class FakeGitHub:
    def __init__(self, owner, command, payload, event):
        self.owner, self.command, self.payload, self.event = owner, command, payload, event
        self.live = command.expected_head_sha
        self.new = "b" * 40
        self.branch = "fix/bounded-repair"
        self.protected = False
        self.fork = False
        self.race = False
        self.tree_mode = "100644"
        self.parent_mode = "040000"
        self.writes = []
        self.calls = []
        self.fail_receipt_readback = False

    def call(self, path, data=None):
        self.calls.append(path)
        if path == f"issues/comments/{self.command.comment_id}":
            return copy.deepcopy(self.owner["comment"])
        if path == f"issues/comments/{self.event['comment']['id']}":
            return copy.deepcopy(self.event["comment"])
        if path.startswith("pulls/"):
            repo = {"id": pub.REPOSITORY_ID, "full_name": pub.REPOSITORY, "default_branch": "main"}
            head_repo = {**repo, "id": 1} if self.fork else repo
            return {"number": self.command.target_pr, "state": "open", "merged": False,
                    "head": {"repo": head_repo, "ref": self.branch, "sha": self.live},
                    "base": {"repo": repo, "ref": "main"}}
        if path.startswith("branches/"):
            return {"name": self.branch, "protected": self.protected, "commit": {"sha": self.live}}
        if path.startswith("git/commits/"):
            sha = path.split("/")[-1]
            return {"sha": sha, "tree": {"sha": sha}}
        if path.startswith("git/trees/"):
            sha = path.split("/")[-1].split("?")[0]
            new = sha == self.new
            file = self.payload["files"][0]
            return {"sha": sha, "truncated": False, "tree": [
                {"path": "tests", "mode": self.parent_mode, "type": "tree", "sha": "d" * 40},
                {"path": file["path"], "mode": self.tree_mode, "type": "blob",
                 "sha": pub.blob_sha(file["content"]) if new else file["base_blob_sha"]}]}
        if path == "graphql":
            assert len(self.writes) == 0
            value = data["variables"]["input"]
            assert value["expectedHeadOid"] == self.command.expected_head_sha
            assert value["branch"] == {"repositoryNameWithOwner": pub.REPOSITORY, "branchName": self.branch}
            assert set(value["fileChanges"]) == {"additions"}
            if self.race:
                self.live = "c" * 40
                raise pub.CallbackContractError("PUBLICATION_GITHUB_REJECTED")
            self.writes.append(data)
            self.live = self.new
            return {"data": {"createCommitOnBranch": {"commit": {"oid": self.new}}}}
        if path.startswith("commits/"):
            if self.fail_receipt_readback:
                raise pub.CallbackContractError("PUBLICATION_HTTP_UNAVAILABLE")
            return {"sha": self.live, "parents": [{"sha": self.command.expected_head_sha}],
                    "commit": {"message": pub.parse_publication_event(self.event).message},
                    "files": [{"filename": f["path"], "status": "modified"} for f in self.payload["files"]]}
        raise AssertionError(path)


def test_publishes_one_exact_head_commit_and_records_after_readback():
    data = fixture()
    github, cursor = FakeGitHub(*data), FakeCursor()
    result = pub.publish(github, cursor, data[-1])
    assert result["status"] == "PUBLISHED"
    assert result["ci_status"] == "NOT_ATTESTED"
    assert result["target_head_sha"] == github.new
    assert len(github.writes) == 1
    assert len(cursor.calls) == 3
    assert cursor.terminal["result_code"] == "BOUNDED_REPAIR_PUBLISHED"
    auth = cursor.calls[0][1]
    assert json.loads(auth[0]) == asdict(data[1])
    assert auth[1] == data[-1]["comment"]["id"]
    assert auth[2] == pub.parse_publication_event(data[-1]).fingerprint


def test_missing_authoritative_provenance_permit_never_writes():
    data = fixture()
    github = FakeGitHub(*data)
    with pytest.raises(pub.CallbackContractError, match="PROVENANCE_PERMIT_REQUIRED"):
        pub.publish(github, FakeCursor(deny=True), data[-1])
    assert github.writes == []


@pytest.mark.parametrize("setting,value,code", [
    ("fork", True, "TARGET_INVALID"), ("protected", True, "BRANCH_UNVERIFIED"),
    ("branch", "main", "BRANCH_DENIED"), ("branch", "autopilot/dispatch/id", "BRANCH_DENIED"),
    ("tree_mode", "120000", "FILE_READBACK_FAILED"),
    ("parent_mode", "120000", "PARENT_NOT_TREE"),
    ("race", True, "GITHUB_REJECTED"),
])
def test_security_and_cas_rejections_do_not_write(setting, value, code):
    data = fixture()
    github = FakeGitHub(*data)
    setattr(github, setting, value)
    with pytest.raises(pub.CallbackContractError, match=code):
        pub.publish(github, FakeCursor(), data[-1])
    assert github.writes == []


@pytest.mark.parametrize("path", [".github/workflows/callback.yml", "../x.py", "/test.py",
    "tests//x.py", "tests/../x.py", "tests/AGENTS.md", "tests/key_secret.py",
    "oracle_autopilot/github_codex_callback.py", "oracle_autopilot/__init__.py",
    "database/migrations/0001_global_registry.sql", "database/migrations/0099_history.sql",
    "database/migrations/0338_autopilot_bounded_publication_permit.sql", "docs/canon/x.md",
    "setup.py", "sitecustomize.py", "tests/х.py", "tests/*.py"])
def test_sensitive_paths_denied(path):
    assert not pub.safe_path(path)


def test_strict_json_rejects_duplicates_and_mixed_result():
    *_, event = fixture()
    raw = event["comment"]["body"]
    event["comment"]["body"] = raw.replace('{"dispatch_id":', '{"files": [], "dispatch_id":', 1)
    with pytest.raises(pub.CallbackContractError, match="DUPLICATE_KEY"):
        pub.parse_publication_event(event)
    event["comment"]["body"] = raw + "\nAUTOPILOT_CODEX_RESULT_V1\nstatus=SUCCEEDED"
    with pytest.raises(pub.CallbackContractError, match="BODY_INVALID"):
        pub.parse_publication_event(event)


@pytest.mark.parametrize("key,value", [("target_pr", True), ("dispatch_epoch", "55"),
    ("expected_head_sha", "e" * 40), ("role", "OTHER"), ("command_comment_id", 42)])
def test_request_cannot_change_command_binding(key, value):
    owner, _, payload, event = fixture()
    payload[key] = value
    event["comment"]["body"] = pub.MARKER + "\n" + json.dumps(payload)
    request = pub.parse_publication_event(event)
    with pytest.raises(pub.CallbackContractError):
        pub.bind_command(request, event, owner["comment"])


def test_unchanged_or_badly_hashed_content_denied():
    *_, payload, event = fixture()
    payload["files"][0]["sha256"] = "0" * 64
    event["comment"]["body"] = pub.MARKER + "\n" + json.dumps(payload)
    with pytest.raises(pub.CallbackContractError, match="CONTENT_HASH_INVALID"):
        pub.parse_publication_event(event)


def test_verified_replay_does_not_publish_twice():
    data = fixture()
    github, cursor = FakeGitHub(*data), FakeCursor()
    result = pub.publish(github, cursor, data[-1])
    cursor.state = "CALLBACK_ACCEPTED"
    assert pub.publish(github, cursor, data[-1]) == result
    assert len(github.writes) == 1


def test_repair_with_bad_terminal_state_is_not_success():
    data = fixture()
    github, cursor = FakeGitHub(*data), FakeCursor()
    cursor.terminal_result = (False, "FAILED_CLOSED")
    with pytest.raises(pub.CallbackContractError, match="TERMINAL_NOT_ACCEPTED"):
        pub.publish(github, cursor, data[-1])
    assert github.verified_head_sha == github.new


def test_blob_digest_agrees_with_independent_git_engine():
    for content in ["after\n", "", "Русский текст\n", "a\r\nb\n"]:
        result = subprocess.run(["git", "hash-object", "--stdin"], input=content.encode(),
                                check=True, capture_output=True)
        assert pub.blob_sha(content) == result.stdout.decode().strip()


def test_privileged_module_does_not_execute_proposed_code():
    source = Path(pub.__file__).read_text()
    for forbidden in ("subprocess", "os.system", "exec(", "eval(", "force=True", "force: true"):
        assert forbidden not in source
    assert '"expectedHeadOid": command.expected_head_sha' in source
    assert 'PUBLICATION_OUTCOME_UNKNOWN' in source
    assert 'PUBLISHED_RECEIPT_PENDING' in source


@pytest.mark.parametrize("phase,expected", [
    ("before", "BLOCKED"), ("sent", "PUBLICATION_OUTCOME_UNKNOWN"),
    ("verified", "PUBLISHED_RECEIPT_PENDING"), ("commit", "PUBLISHED_RECEIPT_PENDING")])
def test_main_reports_partial_outcomes_without_exposing_exception(monkeypatch, capsys, phase, expected):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def cursor(self): return self
        def execute(self, *args): pass
        def commit(self): raise RuntimeError("fake-private-dsn-must-not-leak")

    def fail(github, cursor, event):
        if phase != "before":
            github.publication_attempted = True
        if phase in {"verified", "commit"}:
            github.verified_head_sha = "b" * 40
        if phase == "commit":
            return {"status": "PUBLISHED"}
        raise RuntimeError("fake-private-dsn-must-not-leak")

    monkeypatch.setenv("GITHUB_EVENT_PATH", "/unused/event")
    monkeypatch.setenv("GITHUB_TOKEN", "not-a-real-credential")
    monkeypatch.setenv("AUTOPILOT_CALLBACK_DATABASE_URL", "not-a-real-dsn")
    monkeypatch.setattr(pub.Path, "read_text", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(pub, "validate_callback_dsn", lambda value: value)
    monkeypatch.setattr(pub.psycopg, "connect", lambda *args, **kwargs: Connection())
    monkeypatch.setattr(pub, "publish", fail)
    with pytest.raises(SystemExit) as exc:
        pub.main()
    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "fake-private-dsn" not in output
    assert json.loads(output)["status"] == expected
