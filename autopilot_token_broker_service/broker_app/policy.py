"""Fail-closed Phase 3B draft-repair request policy.

The public request carries the complete intended repair.  The broker recomputes
the action fingerprint from that request before any GitHub credential is
minted, so caller-supplied metadata cannot widen the mutation surface.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


REPOSITORY = "olegmed1-art/bridge-video-free"
BASE_BRANCH = "main"
BRANCH_PREFIX = "autopilot/repair/"
MAX_FILES = 3
MAX_FILE_BYTES = 16_384
MAX_TOTAL_BYTES = 32_768
ALLOWED_PATH_PATTERNS = (
    re.compile(r"oracle_autopilot/[A-Za-z0-9_/-]+\.py"),
    re.compile(r"tests/test_oracle_autopilot_[A-Za-z0-9_/-]+\.py"),
    re.compile(r"docs/evidence/autopilot/[A-Za-z0-9_.-]+\.md"),
)
FORBIDDEN_PATH_PREFIXES = (".github/", "database/", "deploy/", "ops/")


class RepairFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1, max_length=200)
    operation: Literal["CREATE", "UPDATE"]
    content_utf8: str = Field(min_length=1)
    expected_blob_sha: str | None = None

    @model_validator(mode="after")
    def validate_change(self) -> "RepairFileChange":
        path = self.path
        if (
            path.startswith(("/", "\\"))
            or "\\" in path
            or "//" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(path.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES)
            or not any(pattern.fullmatch(path) for pattern in ALLOWED_PATH_PATTERNS)
        ):
            raise ValueError("PHASE3B_PATH_NOT_ALLOWED")
        if "\x00" in self.content_utf8:
            raise ValueError("PHASE3B_CONTENT_INVALID")
        if len(self.content_utf8.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("PHASE3B_FILE_SIZE_INVALID")
        if self.operation == "CREATE" and self.expected_blob_sha is not None:
            raise ValueError("PHASE3B_CREATE_PRECONDITION_INVALID")
        if self.operation == "UPDATE" and (
            not isinstance(self.expected_blob_sha, str)
            or re.fullmatch(r"[0-9a-f]{40}", self.expected_blob_sha) is None
        ):
            raise ValueError("PHASE3B_UPDATE_PRECONDITION_INVALID")
        return self


def expected_branch_name(task_key: str) -> str:
    digest = hashlib.sha256(task_key.encode("utf-8")).hexdigest()[:16]
    return f"{BRANCH_PREFIX}{digest}"


def _canonical_payload(values: dict[str, object]) -> dict[str, object]:
    changes = values["changes"]
    assert isinstance(changes, tuple)
    return {
        "allow_force_push": values["allow_force_push"],
        "allow_merge": values["allow_merge"],
        "base_branch": values["base_branch"],
        "branch_name": values["branch_name"],
        "changes": [
            {
                "content_sha256": hashlib.sha256(
                    change.content_utf8.encode("utf-8")
                ).hexdigest(),
                "expected_blob_sha": change.expected_blob_sha,
                "operation": change.operation,
                "path": change.path,
            }
            for change in changes
        ],
        "expected_base_sha": values["expected_base_sha"],
        "production_mutation": values["production_mutation"],
        "repository": values["repository"],
        "require_draft": values["require_draft"],
        "task_key": values["task_key"],
        "title": values["title"],
    }


def repair_fingerprint(values: dict[str, object]) -> str:
    encoded = json.dumps(
        _canonical_payload(values), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_changes_to_tuple(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


class DraftRepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository: Literal["olegmed1-art/bridge-video-free"]
    base_branch: Literal["main"]
    expected_base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    branch_name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=120)
    changes: Annotated[
        tuple[RepairFileChange, ...], BeforeValidator(_json_changes_to_tuple)
    ] = Field(min_length=1, max_length=MAX_FILES)
    manifest_version: Literal[1]
    require_draft: Literal[True]
    allow_merge: Literal[False]
    allow_force_push: Literal[False]
    production_mutation: Literal[False]

    @model_validator(mode="after")
    def validate_request(self) -> "DraftRepairRequest":
        if self.branch_name != expected_branch_name(self.task_key):
            raise ValueError("PHASE3B_BRANCH_INVALID")
        if (
            not self.title.startswith("[Autopilot draft] ")
            or any(ord(character) < 32 or ord(character) == 127 for character in self.title)
        ):
            raise ValueError("PHASE3B_TITLE_INVALID")
        paths = [change.path for change in self.changes]
        if len(paths) != len(set(paths)):
            raise ValueError("PHASE3B_DUPLICATE_PATH")
        if sum(len(change.content_utf8.encode("utf-8")) for change in self.changes) > MAX_TOTAL_BYTES:
            raise ValueError("PHASE3B_TOTAL_SIZE_INVALID")
        values = {
            "allow_force_push": self.allow_force_push,
            "allow_merge": self.allow_merge,
            "base_branch": self.base_branch,
            "branch_name": self.branch_name,
            "changes": self.changes,
            "expected_base_sha": self.expected_base_sha,
            "production_mutation": self.production_mutation,
            "repository": self.repository,
            "require_draft": self.require_draft,
            "task_key": self.task_key,
            "title": self.title,
        }
        if not hmac_compare(self.action_fingerprint, repair_fingerprint(values)):
            raise ValueError("PHASE3B_FINGERPRINT_INVALID")
        return self


def hmac_compare(left: str, right: str) -> bool:
    """Compare public fingerprints without accidental early-exit differences."""

    return hmac.compare_digest(left, right)
