from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from source_integrity import SourceIntegrityError, audit_repository, require_clean_repository


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "DDS Selftest")
        git(repo, "config", "user.email", "dds-selftest@example.invalid")
        (repo / ".gitignore").write_text(".venv/\nignored-secret/\n", encoding="utf-8")
        (repo / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(repo, "add", ".gitignore", "source.py")
        git(repo, "commit", "-q", "-m", "fixture")

        clean = require_clean_repository(repo, allowed_prefixes=(".venv",))
        assert clean["status"] == "ok"

        (repo / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        changed = audit_repository(repo, allowed_prefixes=(".venv",))
        assert changed["status"] == "error", changed
        assert any(row["path"] == "source.py" for row in changed["unexpected_working_tree_entries"])
        git(repo, "checkout", "--", "source.py")

        (repo / "new.bin").write_bytes(b"unexpected")
        untracked = audit_repository(repo, allowed_prefixes=(".venv",))
        assert untracked["status"] == "error", untracked
        assert any(row["path"] == "new.bin" for row in untracked["unexpected_working_tree_entries"])
        (repo / "new.bin").unlink()

        hidden = repo / "ignored-secret"
        hidden.mkdir()
        (hidden / "token.bin").write_bytes(b"not allowed")
        ignored = audit_repository(repo, allowed_prefixes=(".venv",))
        assert ignored["status"] == "error", ignored
        assert "ignored-secret/token.bin" in ignored["unexpected_ignored_files"]
        for path in hidden.iterdir():
            path.unlink()
        hidden.rmdir()

        runtime = repo / ".venv"
        runtime.mkdir()
        (runtime / "cache.bin").write_bytes(b"allowed runtime residue")
        allowed = require_clean_repository(repo, allowed_prefixes=(".venv",))
        assert allowed["status"] == "ok", allowed
        assert allowed["ignored_file_count"] == 1
        assert ".venv/cache.bin" in allowed["ignored_files_sample"]

        try:
            require_clean_repository(repo, allowed_prefixes=())
        except SourceIntegrityError as exc:
            assert ".venv/cache.bin" in str(exc)
        else:
            raise AssertionError("Ignored runtime residue was accepted without an allow-prefix")

        print(
            json.dumps(
                {
                    "ok": True,
                    "tracked_mutation_detected": True,
                    "untracked_binary_detected": True,
                    "unexpected_ignored_file_detected": True,
                    "explicit_runtime_allowlist_tested": True,
                    "bounded_evidence_sample_tested": True,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
