from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "run_stage_v23.py", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )


def main() -> None:
    check = run("check")
    assert check.returncode == 0, check.stderr
    report = json.loads(check.stdout)
    assert report["ready"] is False
    blockers = {row["capability"] for row in report["blockers"]}
    assert blockers == {
        "dds_partial_position_adapter",
        "full_play_trajectory_integration",
        "stage2_sharded_workflow",
    }

    blocked = run(
        "delegate",
        "--user-approval",
        "ЭТАП-2-ОДОБРЕН",
        "--",
        sys.executable,
        "-c",
        "print('MUST-NOT-RUN')",
        env={**os.environ, "DDS_STAGE2_CONFIRM": "YES"},
    )
    assert blocked.returncode != 0
    assert "readiness gate" in blocked.stderr
    assert "MUST-NOT-RUN" not in blocked.stdout

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "ready.json"
        original = json.loads(Path("STAGE2_READINESS_V23.json").read_text(encoding="utf-8"))
        original["status"] = "ready"
        for value in original["capabilities"].values():
            if value.get("mass_start_required"):
                value["status"] = "ready"
                value.pop("reason", None)
        fake.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

        no_env = run(
            "--readiness",
            str(fake),
            "delegate",
            "--user-approval",
            "ЭТАП-2-ОДОБРЕН",
            "--",
            sys.executable,
            "-c",
            "print('MUST-NOT-RUN')",
        )
        assert no_env.returncode != 0 and "DDS_STAGE2_CONFIRM=YES" in no_env.stderr

        wrong_user = run(
            "--readiness",
            str(fake),
            "delegate",
            "--user-approval",
            "НЕВЕРНО",
            "--",
            sys.executable,
            "-c",
            "print('MUST-NOT-RUN')",
            env={**os.environ, "DDS_STAGE2_CONFIRM": "YES"},
        )
        assert wrong_user.returncode != 0 and "approval token" in wrong_user.stderr

        authorized = run(
            "--readiness",
            str(fake),
            "delegate",
            "--user-approval",
            "ЭТАП-2-ОДОБРЕН",
            "--",
            sys.executable,
            "-c",
            "print('AUTHORIZED-DRY-RUN')",
            env={**os.environ, "DDS_STAGE2_CONFIRM": "YES"},
        )
        assert authorized.returncode == 0, authorized.stderr
        assert "AUTHORIZED-DRY-RUN" in authorized.stdout

    print(
        json.dumps(
            {
                "ok": True,
                "current_readiness": report["ready"],
                "current_blockers": sorted(blockers),
                "mass_stage_blocked": True,
                "double_confirmation_tested": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
