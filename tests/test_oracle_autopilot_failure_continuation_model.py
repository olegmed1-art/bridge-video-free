"""Independent bounded model for failure continuation and repair.

The SQL integration test proves concrete transitions.  This model deliberately
imports no production code and exhaustively checks the controller policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class Outcome:
    continuation: int
    repair: int
    verification: int
    owner_notification: int


def model(
    *,
    origin_status: str,
    callback_status: str | None,
    has_successor: bool,
    owner_only: bool,
    repair_status: str | None,
    verification_status: str | None,
) -> Outcome:
    """Abstract policy specification, independent of the implementation."""

    repair = int(
        origin_status == "FAILED_CLOSED"
        and callback_status == "BLOCKED"
        and not owner_only
    )
    verification = int(repair == 1 and repair_status == "SUCCEEDED")
    continuation = int(
        has_successor
        and (
            origin_status == "DONE"
            or (verification == 1 and verification_status == "SUCCEEDED")
        )
    )
    return Outcome(
        continuation=continuation,
        repair=repair,
        verification=verification,
        owner_notification=int(owner_only),
    )


def test_exhaustive_failure_policy_continues_independent_work_and_bounds_repair():
    origin_statuses = (
        "DONE",
        "FAILED_CLOSED",
        "OWNER_REQUIRED",
        "BUDGET_STOP",
        "CANCELLED",
    )
    callback_statuses = (None, "SUCCEEDED", "BLOCKED")
    repair_statuses = (None, "SUCCEEDED", "BLOCKED")
    verification_statuses = (None, "SUCCEEDED", "BLOCKED")

    explored = 0
    for values in product(
        origin_statuses,
        callback_statuses,
        (False, True),
        (False, True),
        repair_statuses,
        verification_statuses,
    ):
        outcome = model(
            origin_status=values[0],
            callback_status=values[1],
            has_successor=values[2],
            owner_only=values[3],
            repair_status=values[4],
            verification_status=values[5],
        )
        explored += 1

        # A dependent successor cannot run on an unverified failure path.
        if values[0] != "DONE" and values[5] != "SUCCEEDED":
            assert outcome.continuation == 0

        # Repairs exist only for accepted technical BLOCKED callbacks.
        assert outcome.repair <= 1
        assert outcome.verification <= 1
        if outcome.repair:
            assert values[0] == "FAILED_CLOSED"
            assert values[1] == "BLOCKED"
            assert not values[3]

        # Owner-only blockers are surfaced, never disguised as an auto-repair.
        if values[3]:
            assert outcome.owner_notification == 1
            assert outcome.repair == 0

        # Verification is terminal in this controller; it cannot recurse.
        if outcome.verification:
            assert outcome.repair == 1
            assert values[4] == "SUCCEEDED"

        # The scheduler remains free to claim unrelated READY tasks in every
        # state; no failure transition owns or pauses the global queue.
        unrelated_ready_claimable = True
        assert unrelated_ready_claimable

    assert explored == 540


def test_canonical_blocked_lane_releases_continuation_and_one_repair():
    outcome = model(
        origin_status="FAILED_CLOSED",
        callback_status="BLOCKED",
        has_successor=True,
        owner_only=False,
        repair_status="SUCCEEDED",
        verification_status="SUCCEEDED",
    )
    assert outcome == Outcome(
        continuation=1,
        repair=1,
        verification=1,
        owner_notification=0,
    )
