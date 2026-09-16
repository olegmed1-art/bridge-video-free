"""Bounded, data-only publication. Never execute or checkout a task's code.

The SQL fence is deliberately mandatory: an owner comment/eyes reaction alone
cannot authorize a write after the dispatch has expired, completed or changed.
The publisher has no PAT and no merge/deploy/Actions-write capability.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg

from oracle_autopilot.github_codex_callback import (
    CODEX_APP_ID, CODEX_APP_SLUG, CODEX_BOT_ID, CODEX_BOT_LOGIN,
    REPOSITORY, REPOSITORY_ID, SHA_PATTERN, VIEW_TASK_PATTERN,
    CallbackContractError, CodexCommand, CodexTerminal, _default_open,
    _github_request, _mapping, _positive_bigint, _validate_repository_event,
    parse_command_event, validate_callback_dsn,
)

MARKER = "AUTOPILOT_CODEX_PUBLICATION_V1"
MAX_FILES = 8
MAX_BYTES = 32_768
BINDING_FIELDS = ("dispatch_id", "dispatch_epoch", "role", "task_fingerprint",
                  "target_pr", "expected_head_sha")
MUTATION = """mutation($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) { commit { oid } }
}"""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CallbackContractError(code)


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "PUBLICATION_DUPLICATE_KEY")
        result[key] = value
    return result


def safe_path(path: Any) -> bool:
    # Exact paths only, no glob, Unicode alias, dot component, symlink or config.
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_/-]+\.(?:py|sql|md)", path):
        return False
    if len(path) > 200 or any(part in {"", ".", ".."} for part in path.split("/")):
        return False
    if path.endswith("AGENTS.md") or re.search(r"(?i)(secret|credential|token|password)", path):
        return False
    # No self-modification of the privileged receiver, orchestration, governance,
    # build/install hooks, Canon, or migration history. Publication only modifies
    # existing files, so allowing database/migrations/** could rewrite applied SQL.
    return (path.startswith(("tests/", "database/tests/", "docs/evidence/"))
            or ("/" not in path and path.endswith(".py")
                and path not in {"setup.py", "conftest.py", "sitecustomize.py", "usercustomize.py"}))


def blob_sha(content: str) -> str:
    raw = content.encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


@dataclass(frozen=True)
class Publication:
    comment_id: int
    event_pr: int
    body: dict[str, Any]

    @property
    def fingerprint(self) -> str:
        data = json.dumps({"comment_id": self.comment_id, "event_pr": self.event_pr, **self.body},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(data.encode()).hexdigest()

    @property
    def message(self) -> str:
        return f"Slavik bounded repair\n\n{MARKER}\npayload_sha256={self.fingerprint}"


def parse_publication_event(event: Any) -> Publication:
    issue, comment = _validate_repository_event(event)
    actor = _mapping(comment.get("user"), "PUBLICATION_ACTOR_INVALID")
    app = _mapping(comment.get("performed_via_github_app"), "PUBLICATION_APP_INVALID")
    require(actor.get("id") == CODEX_BOT_ID and actor.get("login") == CODEX_BOT_LOGIN
            and comment.get("author_association") == "NONE", "PUBLICATION_ACTOR_INVALID")
    require(app.get("id") == CODEX_APP_ID and app.get("slug") == CODEX_APP_SLUG,
            "PUBLICATION_APP_INVALID")
    raw = comment.get("body")
    require(isinstance(raw, str) and len(raw.encode()) <= 60_000, "PUBLICATION_BODY_INVALID")
    lines = raw.splitlines()
    # A request is NOT a terminal success. No mixed envelopes or prose parsing.
    require(len(lines) >= 2 and lines[0] == MARKER, "PUBLICATION_BODY_INVALID")
    suffix = [line.strip() for line in lines[2:] if line.strip()]
    require(not suffix or (len(suffix) == 1 and VIEW_TASK_PATTERN.fullmatch(suffix[0]) is not None),
            "PUBLICATION_BODY_INVALID")
    try:
        body = json.loads(lines[1], object_pairs_hook=strict_object)
    except (ValueError, TypeError, RecursionError) as exc:
        raise CallbackContractError("PUBLICATION_JSON_INVALID") from exc
    require(isinstance(body, dict) and set(body) == set(BINDING_FIELDS) | {"command_comment_id", "files"},
            "PUBLICATION_FIELDS_INVALID")
    _positive_bigint(body["command_comment_id"], "PUBLICATION_COMMAND_INVALID")
    files = body["files"]
    require(isinstance(files, list) and 1 <= len(files) <= MAX_FILES, "PUBLICATION_FILES_INVALID")
    seen: set[str] = set()
    size = 0
    for entry in files:
        require(isinstance(entry, dict) and set(entry) == {"path", "base_blob_sha", "content", "sha256"},
                "PUBLICATION_FILE_INVALID")
        path, content = entry["path"], entry["content"]
        require(safe_path(path) and path not in seen, "PUBLICATION_PATH_DENIED")
        seen.add(path)
        require(isinstance(content, str) and "\0" not in content, "PUBLICATION_CONTENT_INVALID")
        encoded = content.encode("utf-8")
        size += len(encoded)
        require(size <= MAX_BYTES and hashlib.sha256(encoded).hexdigest() == entry["sha256"],
                "PUBLICATION_CONTENT_HASH_INVALID")
        require(isinstance(entry["base_blob_sha"], str)
                and SHA_PATTERN.fullmatch(entry["base_blob_sha"]) is not None
                and blob_sha(content) != entry["base_blob_sha"], "PUBLICATION_BASE_INVALID")
    return Publication(_positive_bigint(comment.get("id"), "PUBLICATION_COMMENT_INVALID"),
                       issue["number"], body)


def bind_command(publication: Publication, event: dict[str, Any], comment: dict[str, Any]) -> CodexCommand:
    command = parse_command_event({**event, "comment": comment})
    require(command.comment_id == publication.body["command_comment_id"], "PUBLICATION_COMMAND_MISMATCH")
    require(all(type(publication.body[key]) is type(getattr(command, key))
                and publication.body[key] == getattr(command, key) for key in BINDING_FIELDS),
            "PUBLICATION_BINDING_MISMATCH")
    require(command.mode == "REPAIR" and command.execution_scope == "REPOSITORY" and command.can_repair,
            "PUBLICATION_REPAIR_NOT_ALLOWED")
    allowed = command.task_spec.get("expected_changed_files")
    require(isinstance(allowed, list) and 1 <= len(allowed) <= MAX_FILES
            and all(safe_path(path) for path in allowed) and len(set(allowed)) == len(allowed),
            "PUBLICATION_ALLOWLIST_REQUIRED")
    require({entry["path"] for entry in publication.body["files"]} <= set(allowed),
            "PUBLICATION_OUTSIDE_ASSIGNMENT")
    return command


class GitHub:
    def __init__(self, token: str, opener: Any = _default_open):
        self.token, self.opener = token, opener

    def call(self, path: str, data: Any = None) -> dict[str, Any]:
        url = "https://api.github.com" + ("/graphql" if path == "graphql"
              else f"/repos/{REPOSITORY}/" + path)
        request = _github_request(url, self.token)
        if data is not None:
            require(path == "graphql", "PUBLICATION_WRITE_ENDPOINT_DENIED")
            request.data = json.dumps(data).encode()
            request.add_header("Content-Type", "application/json")
            request.method = "POST"
        try:
            with self.opener(request, 10) as response:
                require(response.status == 200 and response.geturl() == url,
                        "PUBLICATION_HTTP_INVALID")
                raw = response.read(4_194_305)
        except (urllib.error.URLError, TimeoutError) as exc:
            # Do not print provider error bodies, headers, request data or DSNs.
            raise CallbackContractError("PUBLICATION_HTTP_UNAVAILABLE") from exc
        require(len(raw) <= 4_194_304, "PUBLICATION_RESPONSE_TOO_LARGE")
        try:
            result = _mapping(json.loads(raw), "PUBLICATION_RESPONSE_INVALID")
        except (ValueError, TypeError) as exc:
            raise CallbackContractError("PUBLICATION_RESPONSE_INVALID") from exc
        require(not result.get("errors"), "PUBLICATION_GITHUB_REJECTED")
        return result


def target(github: GitHub, command: CodexCommand) -> tuple[str, str]:
    pr = github.call(f"pulls/{command.target_pr}")
    head, base = pr.get("head", {}), pr.get("base", {})
    repo = head.get("repo") or {}
    base_repo = base.get("repo") or {}
    branch, sha = head.get("ref"), head.get("sha")
    require(pr.get("number") == command.target_pr and pr.get("state") == "open"
            and not pr.get("merged") and repo.get("id") == REPOSITORY_ID
            and repo.get("full_name") == REPOSITORY and base_repo.get("id") == REPOSITORY_ID
            and base_repo.get("full_name") == REPOSITORY, "PUBLICATION_TARGET_INVALID")
    require(isinstance(branch, str) and re.fullmatch(r"(?:autopilot|codex|fix)/[A-Za-z0-9_/-]+", branch) is not None
            and not branch.startswith("autopilot/dispatch/")
            and branch != base.get("ref") and branch != repo.get("default_branch")
            and all(part not in {"", ".", ".."} for part in branch.split("/")),
            "PUBLICATION_BRANCH_DENIED")
    require(isinstance(sha, str) and SHA_PATTERN.fullmatch(sha) is not None, "PUBLICATION_HEAD_INVALID")
    info = github.call("branches/" + quote(branch, safe=""))
    require(info.get("name") == branch and info.get("protected") is False
            and info.get("commit", {}).get("sha") == sha, "PUBLICATION_BRANCH_UNVERIFIED")
    return branch, sha


def verify_files(github: GitHub, publication: Publication, sha: str, *, base: bool) -> None:
    commit = github.call(f"git/commits/{sha}")
    require(commit.get("sha") == sha, "PUBLICATION_COMMIT_INVALID")
    tree_sha = commit.get("tree", {}).get("sha")
    require(isinstance(tree_sha, str) and SHA_PATTERN.fullmatch(tree_sha) is not None,
            "PUBLICATION_TREE_INVALID")
    tree = github.call(f"git/trees/{tree_sha}?recursive=1")
    require(tree.get("sha") == tree_sha and tree.get("truncated") is False
            and isinstance(tree.get("tree"), list), "PUBLICATION_TREE_INVALID")
    entries = {entry["path"]: entry for entry in tree["tree"]}
    for file in publication.body["files"]:
        entry = entries.get(file["path"], {})
        parts = file["path"].split("/")
        for index in range(1, len(parts)):
            parent = entries.get("/".join(parts[:index]), {})
            require(parent.get("mode") == "040000" and parent.get("type") == "tree",
                    "PUBLICATION_PARENT_NOT_TREE")
        expected = file["base_blob_sha"] if base else blob_sha(file["content"])
        require(entry.get("mode") == "100644" and entry.get("type") == "blob"
                and entry.get("sha") == expected, "PUBLICATION_FILE_READBACK_FAILED")


def verify_commit(github: GitHub, publication: Publication, command: CodexCommand, sha: str) -> None:
    commit = github.call(f"commits/{sha}")
    require(commit.get("sha") == sha
            and [p.get("sha") for p in commit.get("parents", [])] == [command.expected_head_sha]
            and commit.get("commit", {}).get("message") == publication.message,
            "PUBLICATION_COMMIT_MISMATCH")
    files = commit.get("files", [])
    require(len(files) == len(publication.body["files"])
            and {file.get("filename") for file in files} == {file["path"] for file in publication.body["files"]}
            and all(file.get("status") == "modified" for file in files), "PUBLICATION_DIFF_MISMATCH")
    verify_files(github, publication, sha, base=False)


def authorize(cursor: Any, command: CodexCommand, publication: Publication) -> dict[str, Any]:
    cursor.execute("SELECT autopilot.authorize_codex_publication(%s::jsonb,%s,%s)",
                   (json.dumps(asdict(command)), publication.comment_id, publication.fingerprint))
    row = cursor.fetchone()
    require(row is not None and isinstance(row[0], dict), "PUBLICATION_AUTHORIZATION_MISSING")
    require(row[0].get("state") in {"SENT", "CALLBACK_ACCEPTED"}, "PUBLICATION_AUTHORIZATION_INVALID")
    return row[0]


def publish(github: GitHub, cursor: Any, event: dict[str, Any]) -> dict[str, Any]:
    publication = parse_publication_event(event)
    comment_path = f"issues/comments/{publication.body['command_comment_id']}"
    owner_comment = github.call(comment_path)
    command = bind_command(publication, event, owner_comment)
    # Only DB-bound active assignments can reach the sole GitHub write below.
    authorization = authorize(cursor, command, publication)
    branch, live_sha = target(github, command)
    verify_files(github, publication, command.expected_head_sha, base=True)
    if live_sha == command.expected_head_sha:
        require(authorization["state"] == "SENT", "PUBLICATION_ALREADY_COMPLETED")
        require(github.call(comment_path) == owner_comment, "PUBLICATION_COMMAND_CHANGED")
        # Re-read the bot result too: deleted/edited messages cannot be replayed.
        fresh = github.call(f"issues/comments/{publication.comment_id}")
        require(parse_publication_event({**event, "comment": fresh}) == publication,
                "PUBLICATION_REQUEST_CHANGED")
        require(target(github, command) == (branch, live_sha), "PUBLICATION_TARGET_CHANGED")
        authorize(cursor, command, publication)  # locks retained; wall clock rechecked
        github.publication_attempted = True
        response = github.call("graphql", {"query": MUTATION, "variables": {"input": {
            "branch": {"repositoryNameWithOwner": REPOSITORY, "branchName": branch},
            "expectedHeadOid": command.expected_head_sha,
            "message": {"headline": "Slavik bounded repair", "body": publication.message.split("\n\n", 1)[1]},
            "fileChanges": {"additions": [{"path": file["path"],
                "contents": base64.b64encode(file["content"].encode()).decode()}
                for file in publication.body["files"]]},
        }}})
        live_sha = response.get("data", {}).get("createCommitOnBranch", {}).get("commit", {}).get("oid")
        require(isinstance(live_sha, str) and SHA_PATTERN.fullmatch(live_sha) is not None,
                "PUBLICATION_COMMIT_INVALID")
    # Replay after response loss is read-only unless the exact old head remains.
    # An unrelated new head is never adopted or overwritten.
    verify_commit(github, publication, command, live_sha)
    require(target(github, command) == (branch, live_sha), "PUBLICATION_READBACK_HEAD_CHANGED")
    github.verified_head_sha = live_sha
    terminal = CodexTerminal(publication.comment_id, publication.event_pr,
        command.dispatch_id, command.dispatch_epoch, command.role, command.task_fingerprint,
        command.target_pr, "SUCCEEDED", "BOUNDED_REPAIR_PUBLISHED", live_sha,
        "Bounded repair published and read back; CI and independent verification remain separate.")
    if authorization["state"] == "CALLBACK_ACCEPTED":
        require(authorization.get("terminal_body") == terminal.body, "PUBLICATION_RECEIPT_MISMATCH")
    else:
        cursor.execute("""SELECT accepted, resulting_state
            FROM autopilot.accept_role_dispatch_codex_terminal(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (terminal.delivery_id, terminal.payload_fingerprint, True, REPOSITORY,
             terminal.event_pr, CODEX_BOT_LOGIN, CODEX_BOT_ID, "NONE", CODEX_APP_SLUG,
             CODEX_APP_ID, json.dumps(terminal.body)))
        row = cursor.fetchone()
        require(row is not None and type(row[0]) is bool and row[1] == "DONE",
                "PUBLICATION_TERMINAL_NOT_ACCEPTED")
    return {"status": "PUBLISHED", "dispatch_id": command.dispatch_id,
            "target_pr": command.target_pr, "target_head_sha": live_sha,
            "payload_sha256": publication.fingerprint, "ci_status": "NOT_ATTESTED"}


def main() -> None:
    github = None
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        github = GitHub(os.environ["GITHUB_TOKEN"])
        with psycopg.connect(validate_callback_dsn(os.environ["AUTOPILOT_CALLBACK_DATABASE_URL"]),
                connect_timeout=10, application_name="school-autopilot-publication") as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("SET LOCAL statement_timeout = '15s'")
                cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '120s'")
                result = publish(github, cursor, event)
            connection.commit()
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        code = str(exc) if isinstance(exc, CallbackContractError) else "PUBLICATION_FAILED_CLOSED"
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", code):
            code = "PUBLICATION_FAILED_CLOSED"
        status = "BLOCKED"
        result = {"status": status, "result_code": code}
        if github is not None and getattr(github, "verified_head_sha", None):
            result.update(status="PUBLISHED_RECEIPT_PENDING", target_head_sha=github.verified_head_sha)
        elif github is not None and getattr(github, "publication_attempted", False):
            result["status"] = "PUBLICATION_OUTCOME_UNKNOWN"
        print(json.dumps(result))
        sys.exit(1)


if __name__ == "__main__":
    main()
