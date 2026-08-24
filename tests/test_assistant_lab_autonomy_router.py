from assistant_lab.autonomy_router import AutonomyRouter, RouteDisposition
from assistant_lab.capability_registry import (
    CapabilityRegistry,
    CapabilityState,
    ExecutionChannel,
    default_registry,
)


def test_prefers_native_connector_over_oci():
    registry = default_registry().with_states({
        "github.write": True,
        "oracle.repair": True,
    })
    decision = AutonomyRouter(registry).choose(["oracle.repair", "github.write"])
    assert decision.disposition is RouteDisposition.EXECUTE
    assert decision.capability == "github.write"
    assert decision.channel is ExecutionChannel.NATIVE_CONNECTOR


def test_prefers_resident_worker_for_compute():
    registry = default_registry().with_states({
        "dds3.compute": CapabilityState.AVAILABLE,
    })
    decision = AutonomyRouter(registry).choose(["dds3.compute"])
    assert decision.disposition is RouteDisposition.EXECUTE
    assert decision.channel is ExecutionChannel.RESIDENT_WORKER


def test_owner_only_capability_escalates():
    registry = default_registry().with_states({
        "account.secret.create": True,
    })
    decision = AutonomyRouter(registry).choose(["account.secret.create"])
    assert decision.disposition is RouteDisposition.OWNER_REQUIRED
    assert decision.capability == "account.secret.create"


def test_unknown_runtime_state_blocks_without_owner_escalation():
    decision = AutonomyRouter(default_registry()).choose(["oracle.repair"])
    assert decision.disposition is RouteDisposition.BLOCKED
    assert "not proven" in decision.reason


def test_unregistered_capability_fails_closed():
    decision = AutonomyRouter(default_registry()).choose(["arbitrary.shell"])
    assert decision.disposition is RouteDisposition.BLOCKED
    assert "no approved capability" in decision.reason


def test_read_only_mode_rejects_mutating_capability():
    registry = default_registry().with_states({"github.write": True})
    decision = AutonomyRouter(registry).choose(["github.write"], allow_mutation=False)
    assert decision.disposition is RouteDisposition.BLOCKED


def test_registry_rejects_unknown_runtime_overlay():
    try:
        CapabilityRegistry().with_states({"not.registered": True})
    except ValueError as exc:
        assert "unknown capabilities" in str(exc)
    else:
        raise AssertionError("unknown runtime capability must fail closed")


def test_snapshot_contains_safety_metadata():
    snap = default_registry().snapshot()
    assert snap["oracle.bootstrap"]["owner_only"] is True
    assert snap["oracle.repair"]["bounded"] is True
    assert snap["observer.execute"]["mutating"] is True
