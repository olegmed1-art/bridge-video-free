"""Canonical capability registry for Assistant Lab autonomous routing.

The registry describes *approved execution channels*, not discovered credentials.
Runtime probes may mark a capability available/unavailable, but must not widen the
scope declared here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable, Mapping


class CapabilityState(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ExecutionChannel(str, Enum):
    NATIVE_CONNECTOR = "native_connector"
    RESIDENT_WORKER = "resident_worker"
    GITHUB_ACTIONS = "github_actions"
    OCI_INSTANCE_AGENT = "oci_instance_agent"
    OWNER_ACTION = "owner_action"


@dataclass(frozen=True)
class Capability:
    name: str
    channel: ExecutionChannel
    state: CapabilityState = CapabilityState.UNKNOWN
    mutating: bool = False
    owner_only: bool = False
    bounded: bool = True
    description: str = ""


# Ordered by preferred execution path. Lower index is preferred.
_DEFAULT_CAPABILITIES: tuple[Capability, ...] = (
    Capability("github.read", ExecutionChannel.NATIVE_CONNECTOR, description="Read repository, issues, PRs, checks and logs."),
    Capability("github.write", ExecutionChannel.NATIVE_CONNECTOR, mutating=True, description="Create/update repository content, branches, PRs and workflow control actions."),
    Capability("drive.read", ExecutionChannel.NATIVE_CONNECTOR, description="Read/search configured Google Drive material."),
    Capability("drive.write", ExecutionChannel.NATIVE_CONNECTOR, mutating=True, description="Write/update configured Drive artifacts."),
    Capability("neon.read", ExecutionChannel.NATIVE_CONNECTOR, description="Read Assistant Lab database state."),
    Capability("neon.write", ExecutionChannel.NATIVE_CONNECTOR, mutating=True, description="Perform bounded database writes."),
    Capability("dds3.compute", ExecutionChannel.RESIDENT_WORKER, description="Run real DDS3 jobs through the resident Assistant Lab worker."),
    Capability("ben.compute", ExecutionChannel.RESIDENT_WORKER, description="Run BEN jobs through the approved server worker."),
    Capability("worlds.generate", ExecutionChannel.RESIDENT_WORKER, description="Generate bounded full deal worlds from explicit hard constraints."),
    Capability("observer.execute", ExecutionChannel.RESIDENT_WORKER, mutating=True, description="Execute allowlisted Observer tools through Control Bridge/Control API."),
    Capability("oracle.audit", ExecutionChannel.OCI_INSTANCE_AGENT, description="Read-only Oracle host/service audit through OCI Instance Agent."),
    Capability("oracle.repair", ExecutionChannel.OCI_INSTANCE_AGENT, mutating=True, description="Bounded Assistant Lab repair through root-owned admin wrapper."),
    Capability("oracle.bootstrap", ExecutionChannel.OWNER_ACTION, mutating=True, owner_only=True, description="One-time root/IAM/secret bootstrap that cannot be delegated safely."),
    Capability("account.oauth", ExecutionChannel.OWNER_ACTION, mutating=True, owner_only=True, description="OAuth/account-owner authorization."),
    Capability("account.secret.create", ExecutionChannel.OWNER_ACTION, mutating=True, owner_only=True, description="Creation or entry of secret material in an account UI."),
)


class CapabilityRegistry:
    """Immutable-scope registry with runtime availability overlays."""

    def __init__(self, capabilities: Iterable[Capability] | None = None) -> None:
        items = tuple(capabilities or _DEFAULT_CAPABILITIES)
        names = [item.name for item in items]
        if len(names) != len(set(names)):
            raise ValueError("duplicate capability name")
        self._items = {item.name: item for item in items}
        self._order = tuple(names)

    def get(self, name: str) -> Capability | None:
        return self._items.get(name)

    def ordered(self) -> tuple[Capability, ...]:
        return tuple(self._items[name] for name in self._order)

    def with_states(self, states: Mapping[str, CapabilityState | str | bool]) -> "CapabilityRegistry":
        updated: list[Capability] = []
        for item in self.ordered():
            raw = states.get(item.name, item.state)
            if isinstance(raw, bool):
                state = CapabilityState.AVAILABLE if raw else CapabilityState.UNAVAILABLE
            else:
                state = CapabilityState(raw)
            updated.append(replace(item, state=state))
        unknown = set(states) - set(self._items)
        if unknown:
            raise ValueError(f"unknown capabilities: {sorted(unknown)}")
        return CapabilityRegistry(updated)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            item.name: {
                "channel": item.channel.value,
                "state": item.state.value,
                "mutating": item.mutating,
                "owner_only": item.owner_only,
                "bounded": item.bounded,
            }
            for item in self.ordered()
        }


def default_registry() -> CapabilityRegistry:
    return CapabilityRegistry()


__all__ = [
    "Capability",
    "CapabilityRegistry",
    "CapabilityState",
    "ExecutionChannel",
    "default_registry",
]
