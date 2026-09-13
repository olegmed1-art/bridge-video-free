"""Deterministic routing policy for Assistant Lab autonomous work.

The router never invents a new execution capability. It selects among capability
names supplied by the caller and escalates to the owner only when the required
capability is explicitly owner-only or no approved autonomous channel exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .capability_registry import (
    Capability,
    CapabilityRegistry,
    CapabilityState,
    ExecutionChannel,
)


class RouteDisposition(str, Enum):
    EXECUTE = "execute"
    OWNER_REQUIRED = "owner_required"
    BLOCKED = "blocked"


_CHANNEL_PRIORITY = {
    ExecutionChannel.NATIVE_CONNECTOR: 0,
    ExecutionChannel.RESIDENT_WORKER: 1,
    ExecutionChannel.GITHUB_ACTIONS: 2,
    ExecutionChannel.OCI_INSTANCE_AGENT: 3,
    ExecutionChannel.OWNER_ACTION: 4,
}


@dataclass(frozen=True)
class RouteDecision:
    disposition: RouteDisposition
    capability: str | None
    channel: ExecutionChannel | None
    reason: str


class AutonomyRouter:
    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def choose(self, required: Iterable[str], *, allow_mutation: bool = True) -> RouteDecision:
        names = tuple(dict.fromkeys(str(name) for name in required))
        if not names:
            return RouteDecision(RouteDisposition.BLOCKED, None, None, "no capability requested")

        candidates: list[Capability] = []
        owner_candidates: list[Capability] = []
        known = False
        unknown_state = False

        for name in names:
            item = self.registry.get(name)
            if item is None:
                continue
            known = True
            if item.mutating and not allow_mutation:
                continue
            if item.owner_only:
                if item.state is not CapabilityState.UNAVAILABLE:
                    owner_candidates.append(item)
                continue
            if item.state is CapabilityState.AVAILABLE:
                candidates.append(item)
            elif item.state is CapabilityState.UNKNOWN:
                unknown_state = True

        if candidates:
            chosen = min(candidates, key=lambda item: _CHANNEL_PRIORITY[item.channel])
            return RouteDecision(
                RouteDisposition.EXECUTE,
                chosen.name,
                chosen.channel,
                "approved autonomous capability is available",
            )

        if owner_candidates:
            chosen = min(owner_candidates, key=lambda item: _CHANNEL_PRIORITY[item.channel])
            return RouteDecision(
                RouteDisposition.OWNER_REQUIRED,
                chosen.name,
                chosen.channel,
                "account owner action is required by capability policy",
            )

        if unknown_state:
            return RouteDecision(
                RouteDisposition.BLOCKED,
                None,
                None,
                "approved capability exists but runtime availability is not proven",
            )

        if known:
            return RouteDecision(
                RouteDisposition.BLOCKED,
                None,
                None,
                "all approved capabilities are unavailable or disallowed for this operation",
            )

        return RouteDecision(
            RouteDisposition.BLOCKED,
            None,
            None,
            "no approved capability exists; infrastructure change is required",
        )


__all__ = ["AutonomyRouter", "RouteDecision", "RouteDisposition"]
