from __future__ import annotations

"""Repository-wide clean-tree audit for DDS tests and preparation workflows."""

import argparse
import json
import subprocess
from pathlib import Path
from typing import Iterable

DEFAULT_ALLOWED_PREFIXES = (
    "dds_training/.venv",
    "dds_training/.build",
    "dds_training/.tools",
    "dds_training/.wheel-cache",
    "dds_training/work",
    "dds_training/checkpoints",
    "dds_training/__pycache__",
)


class SourceIntegrityError(RuntimeError):
    """Raised when tests or preparation changed repository source state."""


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise SourceIntegrityError(
            f"git {' '.join(args)} failed ({completed.returncode}): "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
    return completed.stdout


def _normalize(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def _allowed(path: str, prefixes: Iterable[str]) -> bool:
    normalized = _normalize(path)
    for prefix in prefixes:
        clean = _normalize(prefix).rstrip("/")
        if normalized == clean or normalized.startswith(clean + "/"):
            return True
    return False


def _status_entries(repo: Path) -> list[dict]:
    text = _git(
        repo,
        "-c",
        "core.quotepath=false",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).decode("utf-8", errors="replace")
    entries = []
    for line in text.splitlines():
        if not line:
            continue
        status = line[:2]
        raw_path = line[3:] if len(line) >= 4 else ""
        # A rename/copy is always a mutation. Keep the whole printable path for
        # evidence; no attempt to waive only one side of it is permitted.
        entries.append({"status": status, "path": raw_path})
    return entries


def _ignored_entries(repo: Path) -> list[str]:
    raw = _git(
        repo,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
    )
    return sorted(
        value.decode("utf-8", errors="replace")
        for value in raw.split(b"\0")
        if value
    )


def audit_repository(
    repo: Path,
    *,
    allowed_prefixes: Iterable[str] = DEFAULT_ALLOWED_PREFIXES,
) -> dict:
    repo = repo.resolve()
    if not (repo / ".git").exists():
        raise SourceIntegrityError(f"Not a Git worktree: {repo}")
    prefixes = tuple(_normalize(value) for value in allowed_prefixes)
    status = _status_entries(repo)
    unexpected_status = [row for row in status if not _allowed(row["path"], prefixes)]
    ignored = _ignored_entries(repo)
    unexpected_ignored = [path for path in ignored if not _allowed(path, prefixes)]
    report = {
        "schema": "dds-source-integrity-v1",
        "status": "ok" if not unexpected_status and not unexpected_ignored else "error",
        "repo": str(repo),
        "allowed_prefixes": list(prefixes),
        "working_tree_entries": status,
        "unexpected_working_tree_entries": unexpected_status,
        "ignored_files": ignored,
        "unexpected_ignored_files": unexpected_ignored,
    }
    return report


def require_clean_repository(
    repo: Path,
    *,
    allowed_prefixes: Iterable[str] = DEFAULT_ALLOWED_PREFIXES,
) -> dict:
    report = audit_repository(repo, allowed_prefixes=allowed_prefixes)
    if report["status"] != "ok":
        raise SourceIntegrityError(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Fail on tracked, untracked or ignored source-tree residue")
    p.add_argument("--repo", default="..")
    p.add_argument("--allow-prefix", action="append", default=[])
    p.add_argument("--out")
    args = p.parse_args()
    allowed = tuple(args.allow_prefix) if args.allow_prefix else DEFAULT_ALLOWED_PREFIXES
    try:
        report = require_clean_repository(Path(args.repo), allowed_prefixes=allowed)
    except SourceIntegrityError as exc:
        print(str(exc))
        raise SystemExit(2) from exc
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
