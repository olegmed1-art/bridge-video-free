"""Shadow worker entrypoint with bounded IBF read-only dispatch.

This is staged code only. Deployments must explicitly switch the Oracle shadow
service to this module before migration 0306 is applied. Importing this module
does not mutate the existing worker module; patching occurs only inside main().
"""

from __future__ import annotations

from . import worker as base
from .contract import ClaimedTask
from .ibf_read_only import fetch_ibf_read_only_snapshot


_ORIGINAL_EXECUTE_TASK = base.execute_task
IBF_EVIDENCE_CLASS = "IBF_READ_ONLY_ANALYSIS_EVIDENCE"


def execute_task(config: base.WorkerConfig, task: ClaimedTask) -> None:
    """Dispatch IBF retrieval while preserving every existing worker behavior."""

    if task.goal_type != "IBF_READ_ONLY_ANALYSIS":
        _ORIGINAL_EXECUTE_TASK(config, task)
        return

    base.validate_task_contract(task)
    snapshot = fetch_ibf_read_only_snapshot(task.goal_json)
    if (
        snapshot.get("production_mutation") is not False
        or snapshot.get("model_calls") != 0
        or snapshot.get("cost_actual_microusd") != 0
    ):
        raise base.AutopilotContractError("AUTOPILOT_IBF_EVIDENCE_INVALID")
    base._complete(
        config,
        task,
        evidence_class=IBF_EVIDENCE_CLASS,
        summary=snapshot,
    )


def main() -> None:
    """Run the established polling loop with only the bounded IBF dispatch added."""

    previous = base.execute_task
    base.execute_task = execute_task
    try:
        base.main()
    finally:
        base.execute_task = previous


if __name__ == "__main__":
    main()
