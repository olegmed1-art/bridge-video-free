#!/usr/bin/env python3
"""Keep the checkpoint candidate from changing the reviewed production entrypoints.

The baseline is a commit identity supplied by the PR event (or explicitly by a
manual reviewer), never a version label copied from the candidate artifacts.
This is a change guard, not an attestation of production correctness.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

PROTECTED_PATHS = (
    ".github/workflows/bridge-video-3.1-free.yml",
    "run_drive_3_1_free_generic.py",
)


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True,
            text=True, encoding="utf-8", timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("BLOCKED: baseline Git evidence unavailable or dirty") from exc


def _entries(repo: Path, revision: str) -> dict[str, str]:
    entries = {}
    for entry in _git(repo, "ls-tree", "-z", revision, "--", *PROTECTED_PATHS).split("\0"):
        if not entry:
            continue
        metadata, path = entry.split("\t", 1)
        mode, kind, _ = metadata.split()
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError("BLOCKED: protected entrypoint must be a regular file")
        entries[path] = metadata
    if set(entries) != set(PROTECTED_PATHS):
        raise ValueError("BLOCKED: protected entrypoint missing")
    return entries


def verify_baseline(repo: Path, baseline_sha: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", baseline_sha) is None:
        raise ValueError("BLOCKED: exact baseline commit SHA required")
    if _git(repo, "cat-file", "-t", baseline_sha) != "commit":
        raise ValueError("BLOCKED: baseline must be a commit")
    candidate_sha = _git(repo, "rev-parse", "--verify", "HEAD")
    if candidate_sha == baseline_sha:
        raise ValueError("BLOCKED: candidate cannot serve as its own baseline")
    baseline = _entries(repo, baseline_sha)
    candidate = _entries(repo, candidate_sha)
    if baseline != candidate:
        raise ValueError("BLOCKED: production entrypoints differ from reviewed baseline")
    _git(repo, "diff", "--no-ext-diff", "--no-textconv", "--quiet", "HEAD", "--", *PROTECTED_PATHS)
    print(f"PRODUCTION_ENTRYPOINT_BASELINE_PASS baseline={baseline_sha} candidate={candidate_sha}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-sha", required=True)
    args = parser.parse_args()
    try:
        verify_baseline(Path.cwd(), args.baseline_sha)
    except ValueError as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
