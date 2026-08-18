from __future__ import annotations

"""Fail if tests changed or added repository files outside runtime allowlists."""

import argparse
import json
import subprocess
from pathlib import Path

DEFAULT_ALLOWED_PREFIXES = (
    "dds_training/.venv/",
    "dds_training/.build/",
    "dds_training/.wheel-cache/",
    "dds_training/work/",
    "dds_training/checkpoints/",
)


class RepositoryDirtyError(RuntimeError):
    pass


def _repo_root(start: Path) -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RepositoryDirtyError(completed.stderr.strip() or "Not inside a Git repository")
    return Path(completed.stdout.strip()).resolve()


def status_entries(root: Path) -> list[dict[str, str]]:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RepositoryDirtyError(completed.stderr.decode("utf-8", errors="replace").strip())
    fields = completed.stdout.split(b"\0")
    entries: list[dict[str, str]] = []
    index = 0
    while index < len(fields):
        raw = fields[index]
        index += 1
        if not raw:
            continue
        text = raw.decode("utf-8", errors="surrogateescape")
        if len(text) < 4:
            raise RepositoryDirtyError(f"Unexpected git status record: {text!r}")
        status = text[:2]
        path = text[3:].replace("\\", "/")
        original = None
        if "R" in status or "C" in status:
            if index >= len(fields):
                raise RepositoryDirtyError("Truncated rename/copy record from git status")
            original = fields[index].decode("utf-8", errors="surrogateescape").replace("\\", "/")
            index += 1
        row = {"status": status, "path": path}
        if original is not None:
            row["original_path"] = original
        entries.append(row)
    return entries


def audit_repository(
    start: Path,
    *,
    allowed_prefixes: tuple[str, ...] = DEFAULT_ALLOWED_PREFIXES,
) -> dict:
    root = _repo_root(start)
    entries = status_entries(root)
    unexpected = []
    allowed = []
    for row in entries:
        paths = [row["path"]]
        if row.get("original_path"):
            paths.append(row["original_path"])
        is_allowed = all(any(path.startswith(prefix) for prefix in allowed_prefixes) for path in paths)
        (allowed if is_allowed else unexpected).append(row)
    return {
        "status": "ok" if not unexpected else "error",
        "repository_root": str(root),
        "unexpected": unexpected,
        "allowed_runtime_entries": allowed,
        "allowed_prefixes": list(allowed_prefixes),
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Check tracked and untracked repository cleanliness")
    p.add_argument("--root", default=".")
    p.add_argument("--json-out")
    p.add_argument("--allow-prefix", action="append", default=[])
    return p


def main() -> None:
    args = parser().parse_args()
    prefixes = tuple(args.allow_prefix) if args.allow_prefix else DEFAULT_ALLOWED_PREFIXES
    try:
        report = audit_repository(Path(args.root), allowed_prefixes=prefixes)
    except RepositoryDirtyError as exc:
        report = {"status": "error", "unexpected": [], "error": str(exc)}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    if report["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
