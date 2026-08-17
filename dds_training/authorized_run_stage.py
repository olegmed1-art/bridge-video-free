from __future__ import annotations

"""Authorized entrypoint for ``run_stage.py evaluate``.

The wrapper validates an explicit, data-bound authorization before importing the
solver execution path.  Other run_stage commands remain available directly
because they do not expose DDS answers or start mass evaluation.
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import run_stage
from run_authorization import AuthorizationError, validate_authorization


def _requested_task_count(args) -> int:
    work = Path(args.work)
    task_path = run_stage._task_path(work, args.tasks_file)
    tasks = run_stage.load_jsonl(task_path)
    requested = [
        task
        for task in tasks
        if task.get("split") in set(args.splits) and run_stage.task_in_stage(task, args.stage)
    ]
    if args.limit:
        requested = requested[: args.limit]
    return len(requested)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Explicitly authorized DDS evaluation wrapper")
    p.add_argument("--authorization", required=True, help="Path to dds-run-authorization-v1 JSON")
    p.add_argument(
        "--approval-token",
        default=None,
        help="Separate approval token; normally supplied through DDS_RUN_APPROVAL_TOKEN",
    )
    p.add_argument(
        "evaluate_args",
        nargs=argparse.REMAINDER,
        help="Arguments for run_stage.py evaluate; prepend -- before them",
    )
    return p


def main() -> None:
    outer = parser().parse_args()
    remainder = list(outer.evaluate_args)
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    if not remainder:
        raise SystemExit("No evaluate arguments supplied after --")
    delegated = run_stage.parser().parse_args(["evaluate", *remainder])
    if delegated.run_id is None:
        delegated.run_id = uuid.uuid4().hex[:12]
    token = outer.approval_token or os.environ.get("DDS_RUN_APPROVAL_TOKEN", "")
    if not token:
        raise SystemExit("DDS mass evaluation blocked: approval token is missing")
    predictions = Path(delegated.predictions)
    context = validate_authorization(
        path=Path(outer.authorization),
        token=token,
        stage=delegated.stage,
        splits=delegated.splits,
        work=Path(delegated.work),
        predictions=predictions,
        requested_tasks=_requested_task_count(delegated),
        open_sealed=bool(delegated.open_sealed),
    )
    os.environ["DDS_RUN_AUTH_FILE"] = str(Path(outer.authorization).resolve())
    os.environ["DDS_RUN_APPROVAL_TOKEN"] = token
    os.environ["DDS_RUN_AUTH_CONTEXT"] = context.context_sha256
    os.environ["DDS_TRAINING_CONFIRM"] = "YES"
    print(
        json.dumps(
            {
                "authorization_valid": True,
                "authorization_id": context.authorization_id,
                "stage": context.stage,
                "splits": context.splits,
                "corpus_sha256": context.corpus_sha256,
                "predictions_sha256": context.predictions_sha256,
                "expires_at": context.expires_at,
                "requested_tasks": _requested_task_count(delegated),
                "run_id": delegated.run_id,
                "dds_called_during_authorization": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    try:
        delegated.func(delegated)
    except AuthorizationError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
