from __future__ import annotations

"""Authorized wrapper around ``run_stage.py evaluate``.

Mass/holdout workflows must call this wrapper. Direct ``run_stage.py evaluate
--start`` is prohibited in GitHub workflow policy tests. The wrapper verifies
and atomically consumes a receipt, validates the requested split against its
scope, then supplies the internal launch proof only to the child process.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from launch_authorization import AuthorizationError, verify_and_consume

SCOPE_SPLITS = {
    "pilot_train": ("train",),
    "derived": ("derived",),
    "main_train": ("train",),
    "validation": ("validation",),
    "sealed_test": ("sealed_test",),
}


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise AuthorizationError(f"Required environment value is missing: {name}")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="One-time authorized DDS stage evaluator")
    p.add_argument("--receipt", required=True)
    p.add_argument("--nonce", required=True)
    p.add_argument("--scope", required=True, choices=sorted(SCOPE_SPLITS))
    p.add_argument("--manifest", required=True)
    p.add_argument("--consume-dir", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--predictions", required=True)
    p.add_argument("--tasks-file")
    p.add_argument("--run-id", required=True)
    p.add_argument("--checkpoint-every", type=int, default=100)
    p.add_argument("--snapshot-every", type=int, default=1000)
    p.add_argument("--milestone-every", type=int, default=5000)
    p.add_argument("--max-followup-sources", type=int)
    p.add_argument("--no-generate-followups", action="store_true")
    return p


def build_command(args: argparse.Namespace) -> list[str]:
    splits = SCOPE_SPLITS[args.scope]
    command = [
        sys.executable,
        "run_stage.py",
        "evaluate",
        "--stage",
        args.stage,
        "--work",
        args.work,
        "--predictions",
        args.predictions,
        "--splits",
        *splits,
        "--start",
        "--run-id",
        args.run_id,
        "--checkpoint-every",
        str(args.checkpoint_every),
        "--snapshot-every",
        str(args.snapshot_every),
        "--milestone-every",
        str(args.milestone_every),
    ]
    if args.tasks_file:
        command.extend(["--tasks-file", args.tasks_file])
    if args.max_followup_sources is not None:
        command.extend(["--max-followup-sources", str(args.max_followup_sources)])
    if args.no_generate_followups:
        command.append("--no-generate-followups")
    if args.scope == "sealed_test":
        command.append("--open-sealed")
    return command


def consumed_marker_path(consume_dir: Path, receipt_id: str) -> Path:
    return consume_dir / f"{receipt_id}.consumed"


def build_child_environment(
    *,
    base_env: dict[str, str],
    receipt_id: str,
    scope: str,
    consume_dir: Path,
) -> dict[str, str]:
    marker = consumed_marker_path(consume_dir, receipt_id)
    if not marker.is_file():
        raise AuthorizationError(f"Consumed receipt marker is missing before DDS child launch: {marker}")
    env = dict(base_env)
    env["DDS_TRAINING_CONFIRM"] = "YES"
    env["DDS_AUTHORIZED_LAUNCH"] = "YES"
    env["DDS_LAUNCH_RECEIPT_ID"] = receipt_id
    env["DDS_LAUNCH_CONSUMED_MARKER"] = str(marker)
    env["DDS_LAUNCH_SCOPE"] = scope
    return env


def main() -> None:
    args = parser().parse_args()
    consume_dir = Path(args.consume_dir)
    try:
        receipt = verify_and_consume(
            receipt_path=Path(args.receipt),
            manifest_path=Path(args.manifest),
            nonce=args.nonce,
            scope=args.scope,
            repository=_required_env("GITHUB_REPOSITORY"),
            ref_name=_required_env("GITHUB_REF_NAME"),
            commit_sha=_required_env("GITHUB_SHA"),
            actor=_required_env("GITHUB_ACTOR"),
            triggering_actor=_required_env("GITHUB_TRIGGERING_ACTOR"),
            event_name=_required_env("GITHUB_EVENT_NAME"),
            consume_dir=consume_dir,
        )
        env = build_child_environment(
            base_env=os.environ.copy(),
            receipt_id=str(receipt["receipt_id"]),
            scope=args.scope,
            consume_dir=consume_dir,
        )
    except AuthorizationError as exc:
        print(json.dumps({"authorized": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc

    command = build_command(args)
    print(json.dumps({
        "authorized": True,
        "receipt_id": receipt["receipt_id"],
        "scope": args.scope,
        "stage": args.stage,
        "splits": list(SCOPE_SPLITS[args.scope]),
        "consumed_marker": env["DDS_LAUNCH_CONSUMED_MARKER"],
        "command": command,
    }, ensure_ascii=False), flush=True)
    completed = subprocess.run(command, env=env, check=False)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
