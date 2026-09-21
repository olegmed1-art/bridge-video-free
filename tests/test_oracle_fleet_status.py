import json
from pathlib import Path

import pytest

from ops.oracle_fleet_status import EvidenceError, parse_host_execution, parse_inventory


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-fleet-status.yml"


def instance(role: str, state: str = "RUNNING") -> dict:
    if role == "light":
        name, shape, ocpus = "bridge-school-autopilot-lite", "VM.Standard.A1.Flex", 2
    else:
        name, shape, ocpus = "bridge-school-dds3-frankfurt", "VM.Standard.E5.Flex", 6
    return {
        "id": f"ocid1.instance.oc1.eu-frankfurt-1.{role}",
        "compartment-id": "ocid1.tenancy.oc1..school",
        "display-name": name,
        "lifecycle-state": state,
        "shape": shape,
        "shape-config": {"ocpus": ocpus, "memory-in-gbs": 12},
    }


def host_execution(role: str = "heavy") -> dict:
    host = {
        "schema": "oracle-fleet-host-status/v1",
        "role": role,
        "hostname": "bridge-school-dds3-frankfurt",
        "observed_at_epoch": 1_789_000_000,
        "uptime_seconds": 3600,
        "cpu_logical": 12,
        "load": [0.1, 0.2, 0.3],
        "memory_total_kib": 12_000_000,
        "memory_available_kib": 8_000_000,
        "root_total_kib": 100_000_000,
        "root_available_kib": 50_000_000,
        "services": {
            "assistant_lab": "inactive",
            "autopilot": "inactive",
            "dds3": "active",
            "universal_video": "inactive",
            "universal_video_container": "inactive",
        },
        "processes": {
            "autonomous_video": 0,
            "book_material": 0,
            "ffmpeg": 0,
            "tesseract": 0,
        },
    }
    return {
        "data": {
            "lifecycle-state": "SUCCEEDED",
            "content": {"exit-code": 0, "text": json.dumps(host)},
        }
    }


def test_inventory_selects_exact_two_known_servers():
    result = parse_inventory({"data": [instance("light"), instance("heavy")]}, "all")
    assert [item["role"] for item in result] == ["light", "heavy"]
    assert [item["ocpus"] for item in result] == [2, 6]
    assert all(item["memory_gb"] == 12 for item in result)


def test_inventory_fails_closed_on_duplicate_or_wrong_shape():
    with pytest.raises(EvidenceError):
        parse_inventory({"data": [instance("light"), instance("light")]}, "light")
    wrong = instance("heavy")
    wrong["shape"] = "VM.Standard.A1.Flex"
    with pytest.raises(EvidenceError):
        parse_inventory({"data": [wrong]}, "heavy")


def test_host_execution_is_sanitized_and_role_bound():
    result = parse_host_execution(host_execution(), "heavy")
    assert result["hostname"] == "bridge-school-dds3-frankfurt"
    assert result["services"]["dds3"] == "active"
    assert "content" not in result
    with pytest.raises(EvidenceError):
        parse_host_execution(host_execution(), "light")


def test_host_execution_rejects_unbounded_or_inconsistent_values():
    payload = host_execution()
    host = json.loads(payload["data"]["content"]["text"])
    host["memory_available_kib"] = host["memory_total_kib"] + 1
    payload["data"]["content"]["text"] = json.dumps(host)
    with pytest.raises(EvidenceError):
        parse_host_execution(payload, "heavy")


def test_workflow_is_owner_only_read_only_and_covers_both_servers():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "github.actor == github.repository_owner" in text
    for command in (
        "/oracle-status",
        "/oracle-status all",
        "/oracle-status light",
        "/oracle-status heavy",
    ):
        assert command in text
    assert "bridge-school-autopilot-lite" not in text  # identity lives in the validator
    assert "bridge-school-dds3-frankfurt" not in text
    assert "oci compute instance list" in text
    assert "oci instance-agent command create" in text
    assert "oci compute instance action" not in text
    assert "systemctl restart" not in text
    assert "systemctl start" not in text
    assert "systemctl stop" not in text
    header = text.split("\njobs:", 1)[0]
    assert "oracle-fleet-status-pr-{0}" in header
    assert "|| 'oracle-instance-workload-mutation' }}" in header
    assert "No server lifecycle, service, queue, file, database, or workload state was changed." in text
