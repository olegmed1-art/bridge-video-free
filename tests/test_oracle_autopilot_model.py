"""Bounded exhaustive checker for the Autopilot shadow state contract.

This model is intentionally independent of the PostgreSQL functions and worker
implementation.  SQL integration tests prove the implementation transitions;
this checker explores the abstract transition graph and rejects unsafe states.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


TERMINAL = frozenset(
    {"DONE", "OWNER_REQUIRED", "FAILED_CLOSED", "BUDGET_STOP", "CANCELLED"}
)


@dataclass(frozen=True)
class State:
    status: str = "READY"
    attempts: int = 0
    max_attempts: int = 2
    lease_epoch: int = 0
    leased: bool = False
    wait_active: bool = False
    event_seen: bool = False
    evidence_retained: bool = False
    reserved: int = 0
    cap: int = 1
    terminal_reason: bool = False


def successors(state: State) -> set[State]:
    if state.status in TERMINAL:
        return set()
    if state.status == "READY":
        if state.attempts >= state.max_attempts:
            return {
                replace(
                    state,
                    status="FAILED_CLOSED",
                    terminal_reason=True,
                )
            }
        return {
            replace(
                state,
                status="RUNNING",
                attempts=state.attempts + 1,
                lease_epoch=state.lease_epoch + 1,
                leased=True,
            )
        }
    if state.status == "RUNNING":
        retry_state = (
            replace(state, status="READY", leased=False)
            if state.attempts < state.max_attempts
            else replace(
                state,
                status="FAILED_CLOSED",
                leased=False,
                terminal_reason=True,
            )
        )
        return {
            replace(
                state,
                status="DONE",
                leased=False,
                evidence_retained=True,
                terminal_reason=True,
            ),
            replace(state, status="WAITING_EXTERNAL", leased=False, wait_active=True),
            replace(
                state,
                status="OWNER_REQUIRED",
                leased=False,
                evidence_retained=True,
                terminal_reason=True,
            ),
            replace(
                state,
                status="BUDGET_STOP",
                leased=False,
                terminal_reason=True,
            ),
            retry_state,
        }
    if state.status == "WAITING_EXTERNAL":
        expiry = replace(
            state,
            status="FAILED_CLOSED",
            wait_active=False,
            terminal_reason=True,
        )
        if state.attempts >= state.max_attempts:
            return {
                expiry,
                replace(expiry, event_seen=True),
            }
        return {
            expiry,
            replace(
                state,
                status="READY",
                wait_active=False,
                event_seen=True,
            ),
        }
    raise AssertionError(f"unmodelled state: {state.status}")


def assert_invariants(state: State) -> None:
    assert state.leased == (state.status == "RUNNING")
    assert state.wait_active == (state.status == "WAITING_EXTERNAL")
    assert state.attempts <= state.max_attempts
    assert state.reserved <= state.cap
    assert (state.status in TERMINAL) == state.terminal_reason
    if state.status == "DONE":
        assert state.evidence_retained
    if state.status in TERMINAL:
        assert not successors(state)
    if state.status == "READY" and state.event_seen:
        assert state.attempts < state.max_attempts


def test_bounded_state_space_preserves_safety_invariants():
    frontier = {State()}
    visited: set[State] = set()
    for _depth in range(10):
        next_frontier: set[State] = set()
        for state in frontier:
            assert_invariants(state)
            visited.add(state)
            next_frontier.update(successors(state) - visited)
        frontier = next_frontier
        if not frontier:
            break

    assert {state.status for state in visited} == {
        "READY",
        "RUNNING",
        "WAITING_EXTERNAL",
        "DONE",
        "OWNER_REQUIRED",
        "FAILED_CLOSED",
        "BUDGET_STOP",
    }
    assert all(state.lease_epoch == state.attempts for state in visited)
