from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sitecustomize import EXIT_UNAUTHORIZED_DDS


def run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "run_stage.py", "evaluate", "--start"],
        cwd=Path(__file__).resolve().parent,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def main() -> None:
    base = os.environ.copy()
    for name in (
        "DDS_TRAINING_CONFIRM",
        "DDS_AUTHORIZED_LAUNCH",
        "DDS_LAUNCH_RECEIPT_ID",
        "DDS_LAUNCH_CONSUMED_MARKER",
        "DDS_LAUNCH_SCOPE",
        "GITHUB_EVENT_NAME",
        "GITHUB_SHA",
    ):
        base.pop(name, None)

    blocked = run(base)
    assert blocked.returncode == EXIT_UNAUTHORIZED_DDS, (blocked.returncode, blocked.stderr)
    assert "DDS launch blocked before run_stage import" in blocked.stderr

    fake = dict(base)
    fake.update({
        "DDS_TRAINING_CONFIRM": "YES",
        "DDS_AUTHORIZED_LAUNCH": "YES",
        "DDS_LAUNCH_RECEIPT_ID": "a" * 64,
        "DDS_LAUNCH_CONSUMED_MARKER": "/tmp/not-present-dds-marker.json",
        "DDS_LAUNCH_SCOPE": "main_train",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_SHA": "b" * 40,
    })
    fake_result = run(fake)
    assert fake_result.returncode == EXIT_UNAUTHORIZED_DDS
    assert "cannot read consumed receipt marker" in fake_result.stderr

    with tempfile.TemporaryDirectory() as td:
        marker = Path(td) / "receipt.consumed"
        marker.write_text(json.dumps({
            "receipt_id": "c" * 64,
            "commit_sha": "d" * 40,
            "scope": "main_train",
        }), encoding="utf-8")
        authorized = dict(base)
        authorized.update({
            "DDS_TRAINING_CONFIRM": "YES",
            "DDS_AUTHORIZED_LAUNCH": "YES",
            "DDS_LAUNCH_RECEIPT_ID": "c" * 64,
            "DDS_LAUNCH_CONSUMED_MARKER": str(marker),
            "DDS_LAUNCH_SCOPE": "main_train",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": "d" * 40,
        })
        passed_guard = run(authorized)
        # It reaches run_stage argparse, which then rejects the deliberately
        # incomplete command.  The pre-import guard must not use exit 86 here.
        assert passed_guard.returncode != EXIT_UNAUTHORIZED_DDS, passed_guard.stderr
        assert "DDS launch blocked before run_stage import" not in passed_guard.stderr

    print(json.dumps({
        "ok": True,
        "direct_run_stage_blocked": True,
        "fake_marker_blocked": True,
        "consumed_marker_allows_only_downstream_validation": True,
        "unauthorized_exit_code": EXIT_UNAUTHORIZED_DDS,
    }, indent=2))


if __name__ == "__main__":
    main()
