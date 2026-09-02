from __future__ import annotations


BASE_STAGE_BOARD_RANGES = {
    "pilot": (1, 10_000),
    # Main is an expansion to 30k total, but its fresh blind base tasks are only
    # the newly introduced 20k deals. Pilot holdouts have already been opened
    # and must never masquerade as a fresh main-stage sealed test.
    "main": (10_001, 30_000),
}


def task_in_stage(task: dict, stage: str) -> bool:
    """Return whether a task belongs to this stage's fresh evaluation scope.

    Derived tasks are explicitly generated reinforcement and may be evaluated in
    whichever stage/run names them. Base PBN tasks are stage-scoped by board.
    Random base tasks are forbidden for `targeted`; that stage must come from the
    learning planner / derived task generators.
    """
    if task.get("split") == "derived":
        return True
    if stage == "targeted":
        return False
    if stage not in BASE_STAGE_BOARD_RANGES:
        raise ValueError(f"Unknown stage: {stage}")
    board = task.get("board")
    if board is None:
        raise ValueError(f"Base task {task.get('task_id')} has no board provenance")
    low, high = BASE_STAGE_BOARD_RANGES[stage]
    return low <= int(board) <= high


def expected_base_deals(stage: str) -> int:
    if stage not in BASE_STAGE_BOARD_RANGES:
        return 0
    low, high = BASE_STAGE_BOARD_RANGES[stage]
    return high - low + 1
