from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from v23_runtime import ALGORITHM_VERSION, audit_stage2_readiness

STAGE2_CONFIRM = "YES"


def load_committed_readiness(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Stage-2 launch blocked: readiness manifest is missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("algorithm_version") != ALGORITHM_VERSION:
        raise SystemExit(
            f"Stage-2 launch blocked: readiness version {data.get('algorithm_version')} != {ALGORITHM_VERSION}"
        )
    return data


def readiness_report(manifest_path: Path) -> dict:
    committed = load_committed_readiness(manifest_path)
    runtime = audit_stage2_readiness(committed.get("capabilities"))
    blockers = runtime["mass_start_blockers"]
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "committed_status": committed.get("status"),
        "runtime_status": runtime["status"],
        "mass_training_started": False,
        "blockers": blockers,
        "ready": committed.get("status") == "ready" and runtime["status"] == "ready" and not blockers,
    }


def require_stage2_launch(args) -> dict:
    report = readiness_report(Path(args.readiness))
    if not report["ready"]:
        names = [row["capability"] for row in report["blockers"]]
        raise SystemExit(f"Stage-2 launch blocked by readiness gate: {names}")
    if os.environ.get("DDS_STAGE2_CONFIRM") != STAGE2_CONFIRM:
        raise SystemExit("Stage-2 launch blocked: set DDS_STAGE2_CONFIRM=YES only after explicit user approval")
    if args.user_approval != "ЭТАП-2-ОДОБРЕН":
        raise SystemExit("Stage-2 launch blocked: explicit user approval token is missing")
    return report


def cmd_check(args) -> None:
    print(json.dumps(readiness_report(Path(args.readiness)), ensure_ascii=False, indent=2))


def cmd_delegate(args) -> None:
    report = require_stage2_launch(args)
    if not args.command:
        raise SystemExit("No delegated Stage-2 command supplied after --")
    print(json.dumps({**report, "launch_authorized": True, "delegated_command": args.command}, ensure_ascii=False))
    completed = subprocess.run(args.command, check=False)
    raise SystemExit(completed.returncode)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fail-closed Stage-2 launcher for DDS learning v2.3")
    p.add_argument(
        "--readiness",
        default=str(Path(__file__).with_name("STAGE2_READINESS_V23.json")),
        help="Committed readiness manifest",
    )
    sp = p.add_subparsers(dest="action", required=True)

    q = sp.add_parser("check")
    q.set_defaults(func=cmd_check)

    q = sp.add_parser("delegate")
    q.add_argument("--user-approval", required=True)
    q.add_argument("command", nargs=argparse.REMAINDER)
    q.set_defaults(func=cmd_delegate)
    return p


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
