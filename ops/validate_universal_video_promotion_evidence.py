#!/usr/bin/env python3
"""Fail-closed validation for Universal Video promotion evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any


AUTHORITATIVE_WORKFLOW_NAME = "Issue 881 Authoritative External Pre-Canary Evidence"
AUTHORITATIVE_WORKFLOW_PATH = (
    ".github/workflows/issue-881-authoritative-external-evidence.yml"
)
DIRECTOR_LOGIN = "olegmed1-art"
REPOSITORY_FULL_NAME = "olegmed1-art/bridge-video-free"
ARTIFACT_PREFIX = "issue-881-authoritative-external-"
EVIDENCE_FILENAME = "issue-881-authoritative-external-evidence.txt"
MAX_ARCHIVE_BYTES = 2_000_000
MAX_EVIDENCE_BYTES = 1_000_000
SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


class EvidenceValidationError(ValueError):
    """Promotion evidence is missing, ambiguous, stale, or inconsistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceValidationError(message)


def _read_json(path: Path) -> Any:
    _require(path.is_file() and not path.is_symlink(), "unsafe JSON input")
    _require(path.stat().st_size <= MAX_ARCHIVE_BYTES, "JSON input is too large")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError("invalid JSON input") from exc


def select_authoritative_artifact(
    run: Any, artifact_pages: Any, expected_commit: str
) -> dict[str, Any]:
    """Validate the run identity and select its one immutable evidence artifact."""

    _require(SHA_RE.fullmatch(expected_commit) is not None, "invalid expected commit")
    _require(isinstance(run, dict), "invalid workflow run")
    _require(run.get("name") == AUTHORITATIVE_WORKFLOW_NAME, "wrong evidence workflow")
    _require(run.get("path") == AUTHORITATIVE_WORKFLOW_PATH, "wrong workflow path")
    _require(run.get("event") == "workflow_dispatch", "wrong workflow event")
    _require(run.get("status") == "completed", "evidence workflow is not complete")
    _require(run.get("conclusion") == "success", "evidence workflow did not succeed")
    _require(run.get("head_sha") == expected_commit, "evidence head mismatch")
    repository = run.get("repository")
    _require(
        isinstance(repository, dict)
        and repository.get("full_name") == REPOSITORY_FULL_NAME,
        "wrong evidence repository",
    )
    actor = run.get("actor")
    _require(
        isinstance(actor, dict) and actor.get("login") == DIRECTOR_LOGIN,
        "evidence run was not dispatched by the Director",
    )
    triggering_actor = run.get("triggering_actor")
    _require(
        isinstance(triggering_actor, dict)
        and triggering_actor.get("login") == DIRECTOR_LOGIN,
        "evidence run or rerun was not initiated by the Director",
    )
    run_id = run.get("id")
    _require(isinstance(run_id, int) and run_id > 0, "invalid workflow run id")

    pages = artifact_pages if isinstance(artifact_pages, list) else [artifact_pages]
    _require(pages and all(isinstance(page, dict) for page in pages), "invalid artifact pages")
    artifacts: list[dict[str, Any]] = []
    for page in pages:
        items = page.get("artifacts")
        _require(isinstance(items, list), "invalid artifact page")
        _require(all(isinstance(item, dict) for item in items), "invalid artifact record")
        artifacts.extend(items)

    expected_name = f"{ARTIFACT_PREFIX}{expected_commit}"
    matches = [
        artifact
        for artifact in artifacts
        if artifact.get("name") == expected_name and artifact.get("expired") is False
    ]
    _require(len(matches) == 1, "authoritative artifact is missing or ambiguous")
    artifact = matches[0]
    artifact_id = artifact.get("id")
    artifact_digest = artifact.get("digest")
    artifact_size = artifact.get("size_in_bytes")
    artifact_run = artifact.get("workflow_run")
    _require(isinstance(artifact_id, int) and artifact_id > 0, "invalid artifact id")
    _require(
        isinstance(artifact_digest, str) and DIGEST_RE.fullmatch(artifact_digest),
        "artifact has no immutable SHA-256 digest",
    )
    _require(
        isinstance(artifact_size, int) and 0 < artifact_size <= MAX_ARCHIVE_BYTES,
        "artifact archive size is unsafe",
    )
    _require(
        isinstance(artifact_run, dict)
        and artifact_run.get("id") == run_id
        and artifact_run.get("head_sha") == expected_commit,
        "artifact is not bound to the selected workflow run",
    )
    return {"artifact_id": artifact_id, "artifact_digest": artifact_digest}


def _one_exact(lines: list[str], key: str, expected: str) -> None:
    matching = [line for line in lines if line.startswith(f"{key}=")]
    _require(matching == [f"{key}={expected}"], f"invalid or ambiguous {key}")


def verify_evidence_archive(
    archive_path: Path,
    expected_artifact_digest: str,
    expected_commit: str,
    expected_image_digest: str,
) -> None:
    """Verify archive integrity and bind its exact PASS receipt to the request."""

    _require(SHA_RE.fullmatch(expected_commit) is not None, "invalid expected commit")
    _require(
        DIGEST_RE.fullmatch(expected_image_digest) is not None,
        "invalid expected image digest",
    )
    _require(
        DIGEST_RE.fullmatch(expected_artifact_digest) is not None,
        "invalid expected artifact digest",
    )
    _require(
        archive_path.is_file() and not archive_path.is_symlink(),
        "unsafe artifact archive",
    )
    archive_size = archive_path.stat().st_size
    _require(0 < archive_size <= MAX_ARCHIVE_BYTES, "artifact archive size is unsafe")
    archive_bytes = archive_path.read_bytes()
    actual_digest = "sha256:" + hashlib.sha256(archive_bytes).hexdigest()
    _require(actual_digest == expected_artifact_digest, "artifact digest mismatch")

    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            _require(len(entries) == 1, "artifact archive must contain exactly one file")
            entry = entries[0]
            _require(not entry.is_dir(), "artifact evidence file is missing")
            _require(entry.filename == EVIDENCE_FILENAME, "unexpected artifact filename")
            _require(not (entry.flag_bits & 0x1), "encrypted artifact is forbidden")
            _require(
                0 < entry.file_size <= MAX_EVIDENCE_BYTES,
                "artifact evidence size is unsafe",
            )
            evidence_bytes = archive.read(entry)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise EvidenceValidationError("invalid artifact archive") from exc

    _require(len(evidence_bytes) <= MAX_EVIDENCE_BYTES, "artifact evidence is too large")
    try:
        evidence = evidence_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceValidationError("artifact evidence is not UTF-8") from exc
    _require("\x00" not in evidence, "artifact evidence contains NUL")
    lines = evidence.splitlines()

    _one_exact(lines, "runtime_sha", expected_commit)
    _one_exact(lines, "image_digest", expected_image_digest)
    for key, value in (
        ("real_media_canary_run", "false"),
        ("source_media_downloaded", "false"),
        ("drive_write_performed", "false"),
        ("automatic_batch_release", "false"),
        ("canonical_promotion_allowed", "false"),
        ("publication_state", "NOT_PUBLISHED"),
    ):
        _one_exact(lines, key, value)

    install_line = (
        "UNIVERSAL_VIDEO_CONTAINER_INSTALL_PASS "
        f"commit={expected_commit} image_digest={expected_image_digest} activated=0"
    )
    attest_line = (
        "UNIVERSAL_VIDEO_PRECANARY_ATTEST_PASS "
        f"commit={expected_commit} image_digest={expected_image_digest} "
        "video_job_submitted=false drive_write_performed=false "
        "canonical_promotion_allowed=false publication_state=NOT_PUBLISHED"
    )
    runtime_line = (
        "UNIVERSAL_VIDEO_PRECANARY_RUNTIME "
        f"commit={expected_commit} image_digest={expected_image_digest}"
    )
    for label, expected in (
        ("install receipt", install_line),
        ("attestation receipt", attest_line),
        ("runtime receipt", runtime_line),
    ):
        _require(lines.count(expected) == 1, f"invalid or ambiguous {label}")

    restore_lines = [
        line for line in lines if line.startswith("UNIVERSAL_VIDEO_PRECANARY_RESTORE_PASS ")
    ]
    _require(len(restore_lines) == 1, "restore receipt is missing or ambiguous")
    restore_match = re.fullmatch(
        r"UNIVERSAL_VIDEO_PRECANARY_RESTORE_PASS "
        r"source_service_before=(active|inactive) source_service=\1 "
        r"container_service_before=(active|inactive) container_target=(active|inactive) "
        r"container_service=\3 prior_container_recovery=[01]",
        restore_lines[0],
    )
    _require(restore_match is not None, "restore receipt is inconsistent")

    window_lines = [
        line for line in lines if line.startswith("UNIVERSAL_VIDEO_PRECANARY_WINDOW ")
    ]
    _require(len(window_lines) == 1, "pre-canary window receipt is missing or ambiguous")
    _require(
        "workload_fence=exclusive" in window_lines[0]
        and "services_quiescent=true" in window_lines[0]
        and "restore_on_exit=true" in window_lines[0],
        "pre-canary window was not safely fenced",
    )

    gate_records: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceValidationError("invalid JSON gate receipt") from exc
        _require(isinstance(record, dict), "invalid JSON gate receipt")
        gate = record.get("gate")
        if isinstance(gate, str):
            gate_records.setdefault(gate, []).append(record)

    for gate in (
        "IMPORT_CLOSURE",
        "SYNTHETIC_RESULT_CONTRACT",
        "SOURCE_IDENTITY_METADATA_ONLY",
    ):
        records = gate_records.get(gate, [])
        _require(len(records) == 1, f"{gate} receipt is missing or ambiguous")
        record = records[0]
        _require(record.get("status") == "PASS", f"{gate} did not pass")
        _require(record.get("canonical_promotion_allowed") is False, f"unsafe {gate} authority")
        _require(record.get("publication_state") == "NOT_PUBLISHED", f"unsafe {gate} publication")

    source_record = gate_records["SOURCE_IDENTITY_METADATA_ONLY"][0]
    _require(source_record.get("source_media_downloaded") is False, "source media was downloaded")
    _require(source_record.get("video_job_submitted") is False, "video job was submitted")
    synthetic_record = gate_records["SYNTHETIC_RESULT_CONTRACT"][0]
    _require(synthetic_record.get("drive_write_performed") is False, "Drive write was performed")
    _require(synthetic_record.get("video_job_submitted") is False, "video job was submitted")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select-artifact")
    select.add_argument("--run-json", type=Path, required=True)
    select.add_argument("--artifacts-json", type=Path, required=True)
    select.add_argument("--expected-commit", required=True)

    verify = subparsers.add_parser("verify-archive")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--expected-artifact-digest", required=True)
    verify.add_argument("--expected-commit", required=True)
    verify.add_argument("--expected-image-digest", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "select-artifact":
            selection = select_authoritative_artifact(
                _read_json(args.run_json),
                _read_json(args.artifacts_json),
                args.expected_commit,
            )
            print(json.dumps(selection, sort_keys=True, separators=(",", ":")))
        else:
            verify_evidence_archive(
                args.archive,
                args.expected_artifact_digest,
                args.expected_commit,
                args.expected_image_digest,
            )
            print("UNIVERSAL_VIDEO_PROMOTION_EVIDENCE_PASS")
    except EvidenceValidationError as exc:
        print(f"UNIVERSAL_VIDEO_PROMOTION_EVIDENCE_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
