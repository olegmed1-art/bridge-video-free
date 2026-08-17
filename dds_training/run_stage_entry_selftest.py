from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        work = root / "work"
        work.mkdir()
        predictions = root / "locked_predictions.jsonl"
        predictions.write_text("", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "DDS_TEST_MODE": "",
                "DDS_PREFLIGHT_MODE": "",
                "DDS_TRAINING_CONFIRM": "YES",
                "DDS_RUN_AUTH_FILE": "",
                "DDS_RUN_APPROVAL_TOKEN": "",
                "DDS_RUN_AUTH_CONTEXT": "",
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("run_stage.py")),
                "evaluate",
                "--stage",
                "pilot",
                "--work",
                str(work),
                "--predictions",
                str(predictions),
                "--splits",
                "train",
                "--start",
            ],
            cwd=Path(__file__).resolve().parent,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
        combined = completed.stdout + completed.stderr
        assert completed.returncode != 0, combined
        assert "authorized_run_stage.py" in combined, combined
        assert not (work / "training.sqlite3").exists(), "Blocked direct command created SQLite"
        assert not list(work.iterdir()), "Blocked direct command wrote files before authorization"
        print(
            json.dumps(
                {
                    "ok": True,
                    "direct_cli_blocked": True,
                    "blocked_before_sqlite_write": True,
                    "blocked_before_any_work_file": True,
                    "dds_called": False,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
