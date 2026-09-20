import json
from pathlib import Path


def test_r263_activation_receipt_has_one_current_production_target():
    activation = json.loads(
        Path("ops/r263-production-activation.json").read_text(encoding="utf-8")
    )
    promotion = json.loads(
        Path("ops/r263-production-promotion.json").read_text(encoding="utf-8")
    )
    canonical = json.loads(
        Path("ops/r263-canonical-promotion-gate.json").read_text(encoding="utf-8")
    )

    assert activation["revision"] == promotion["revision"] == "3.1-free-r26.3"
    assert activation["activation_state"] == "ACTIVE_READBACK_CONFIRMED"
    assert activation["promotion_commit"] == canonical["production_promotion_commit"]

    branch = activation["neon"]
    assert branch["default"] is True
    assert branch["primary"] is True
    assert branch["protected"] is True

    current = activation["current_revision"]
    previous = activation["previous_revision"]
    assert current["version_no"] == 20
    assert current["status"] == "active"
    assert current["production_allowed"] is True
    assert current["production_promotion_state"] == "ACTIVE"
    assert current["recognizer_profile"] == "AUTONOMOUS_HASH_GEOMETRY_V1"
    assert current["human_verification_required"] is False
    assert current["active_compute_route"] == "github_actions_legacy"
    assert current["deck_complement"] is False
    assert current["hidden_hand_inference"] is False
    assert current["canonical_promotion_allowed"] is False
    assert current["school_canon_write_allowed"] is False
    assert current["world_write_allowed"] is False

    assert previous["version_no"] == 19
    assert previous["status"] == "observed"
    assert previous["production_allowed"] is False
    assert previous["production_promotion_state"] == "SUPERSEDED_ROLLBACK_AVAILABLE"
    assert previous["rollback_history_retained"] is True

    readback = activation["repository_readback"]
    assert readback["workflow_requested_revision"] == "3.1-free-r26.3"
    assert readback["canonical_gate"] == "BLOCKED_HOLDOUT"

    workflow = Path(".github/workflows/bridge-video-3.1-free.yml").read_text(
        encoding="utf-8"
    )
    assert 'BRIDGE_REQUESTED_ALGORITHM_REVISION: "3.1-free-r26.3"' in workflow
