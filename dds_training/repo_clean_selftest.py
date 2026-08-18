from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from repo_clean import audit_repository


def run(root: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, (args, completed.stdout, completed.stderr)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run(root, "init", "-q")
        run(root, "config", "user.name", "DDS Test")
        run(root, "config", "user.email", "dds-test@example.invalid")
        tracked = root / "tracked.py"
        tracked.write_text("VALUE = 1\n", encoding="utf-8")
        run(root, "add", "tracked.py")
        run(root, "commit", "-qm", "fixture")

        assert audit_repository(root, allowed_prefixes=())["status"] == "ok"

        pbn = root / "leak.pbn"
        pbn.write_text("[Deal \"N:- - - -\"]\n", encoding="utf-8")
        report = audit_repository(root, allowed_prefixes=())
        assert report["status"] == "error"
        assert any(row["path"] == "leak.pbn" for row in report["unexpected"])
        pbn.unlink()

        binary = root / "snapshot.sqlite3"
        binary.write_bytes(b"SQLite format 3\x00fixture")
        report = audit_repository(root, allowed_prefixes=())
        assert report["status"] == "error"
        assert any(row["path"] == "snapshot.sqlite3" for row in report["unexpected"])
        binary.unlink()

        tracked.write_text("VALUE = 2\n", encoding="utf-8")
        report = audit_repository(root, allowed_prefixes=())
        assert report["status"] == "error"
        assert any(row["path"] == "tracked.py" and "M" in row["status"] for row in report["unexpected"])
        run(root, "checkout", "--", "tracked.py")

        runtime = root / "dds_training" / ".venv" / "bin"
        runtime.mkdir(parents=True)
        (runtime / "python").write_bytes(b"runtime")
        report = audit_repository(root)
        assert report["status"] == "ok", report
        assert report["allowed_runtime_entries"]

        print(json.dumps({
            "ok": True,
            "untracked_pbn_detected": True,
            "untracked_sqlite_detected": True,
            "tracked_modification_detected": True,
            "explicit_runtime_allowlist_tested": True,
        }, indent=2))


if __name__ == "__main__":
    main()
