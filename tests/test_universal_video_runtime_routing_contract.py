import copy
import json
from pathlib import Path
import pytest
from ops.validate_universal_video_runtime_routing import RoutingContractError, load_and_validate, require_active
ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/bridge-video-3.1-free.yml"

def test_historical_routing_is_explicit_single_active_and_cutover_blocked(historical_routing) -> None:
    routing = load_and_validate()
    assert routing["active_production_route"] == "github_actions_legacy"
    assert routing["policy_target_route"] == "oracle_container"
    assert routing["cutover"]["state"] == "BLOCKED_ON_FEATURE_PARITY"
    assert routing["cutover"]["required_evidence"] == ["resident_container_promotion_pass","r26_feature_parity_pass","one_bounded_end_to_end_pass","rollback_proof_pass"]

def test_legacy_worker_checks_route_before_job_or_secret_access() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    guard = "python ops/validate_universal_video_runtime_routing.py --require-active github_actions_legacy"
    assert workflow.index(guard) < workflow.index("INPUT_JOB_ID:")
    assert workflow.index(guard) < workflow.index("GOOGLE_DRIVE_OAUTH_JSON")
    assert workflow.index(guard) < workflow.index("Install free open-source runtime")
    assert workflow.index(guard) < workflow.index("Process one opaque Drive job")

def test_second_production_default_fails_closed(historical_routing, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tampered = copy.deepcopy(load_and_validate()); tampered["routes"]["oracle_container"]["production_default"] = True
    routing_file = tmp_path / "routing.json"; routing_file.write_text(json.dumps(tampered), encoding="utf-8")
    monkeypatch.setattr("ops.validate_universal_video_runtime_routing.ROUTING_FILE", routing_file)
    with pytest.raises(RoutingContractError, match="UV_RUNTIME_ROUTING_NOT_SINGLE_ACTIVE"): load_and_validate()

def test_historical_oracle_cutover_retires_legacy_worker(historical_routing) -> None:
    routing = copy.deepcopy(load_and_validate()); routing["active_production_route"] = "oracle_container"; routing["routes"]["github_actions_legacy"]["production_default"] = False; routing["routes"]["oracle_container"]["production_default"] = True
    with pytest.raises(RoutingContractError, match="UV_RUNTIME_ROUTE_RETIRED"): require_active(routing, "github_actions_legacy")

@pytest.fixture
def historical_routing(monkeypatch, tmp_path):
    """Exercise pre-retirement gates using inert archived workflow evidence."""
    routing = json.loads((ROOT / "ops/universal-video-runtime-routing.json").read_text())
    assert routing["routes"]["oracle_container"]["entrypoint"] == ".github/workflows/oracle-universal-video-job.yml"
    assert not (ROOT / routing["routes"]["oracle_container"]["entrypoint"]).exists()
    routing["routes"]["oracle_container"]["entrypoint"] = "tests/fixtures/retired_oracle/oracle-universal-video-job.yml"
    path = tmp_path / "historical-routing.json"
    path.write_text(json.dumps(routing), encoding="utf-8")
    monkeypatch.setattr("ops.validate_universal_video_runtime_routing.ROUTING_FILE", path)


def test_current_retired_oracle_entrypoint_fails_closed():
    with pytest.raises(RoutingContractError, match="UV_RUNTIME_ROUTING_ENTRYPOINT_MISSING"):
        load_and_validate()
