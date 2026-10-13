"""Offline fixture/plan integrity; no PostgreSQL import, connection or execution."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from universal_video import batch_plan
from universal_video.terminal_evidence_v2 import source_identity_from_claim, validate_terminal_output


ROOT = Path(__file__).resolve().parents[1]


def fixture():
    return json.loads((ROOT / "tests/fixtures/universal_video_terminal_sql_v2.json").read_text(encoding="utf-8"))


def test_terminal_fixture_matches_existing_python_contract_without_live_readback():
    data = fixture()
    assert data["synthetic_only"] is True
    assert data["sql_executed"] is False
    assert data["postgres_test_status"] == "NOT_RUN"
    validate_terminal_output(data["claim"], data["output"])
    assert data["expected_sql_result"] == dict(job_status="REVIEW_READY", batch_status="CANARY_REVIEW",
                                               released_jobs=0, pending_canary_jobs=1)


def test_fixture_contains_real_sql_required_artifact_revision_size_and_identity():
    data = fixture()
    claim, output = data["claim"], data["output"]
    manifest, receipt = output["artifact_manifest"], output["terminal_receipt"]
    assert manifest["source_identity"] == source_identity_from_claim(claim)
    assert manifest["schema_version"] == "universal-video-artifact-manifest/v1"
    assert receipt["schema_version"] == "universal-video-terminal-receipt/v1"
    for value in (manifest, receipt):
        assert value["job_id"] == claim["stable_job_key"]
        assert value["source_file_id"] == claim["source_file_id"]
        assert value["canonical_promotion_allowed"] is False
        assert value["database_persistence_allowed"] is False
        assert value["publication_state"] == "NOT_PUBLISHED"
    assert manifest["result_mode"] == "SHADOW_REVIEW_ONLY"
    assert receipt["status"] == "PASS"
    for key in ("source_identity_verified", "drive_readback_verified", "result_readback_verified", "checksum_verified"):
        assert receipt[key] is True  # Synthetic inputs; not a live attestation.
    assert type(receipt["artifact_count"]) is int and receipt["artifact_count"] == 2
    assert len(manifest["artifacts"]) == 2
    for index, (kind, mime) in enumerate((("master_pdf", "application/pdf"), ("ai_done", "application/json"))):
        artifact = manifest["artifacts"][index]
        assert artifact["kind"] == kind
        assert artifact["mime_type"] == mime
        assert artifact["drive_id"] == output[kind + "_drive_id"]
        assert artifact["locator"] == "gdrive:file:" + artifact["drive_id"]
        assert artifact["parent_id"] == claim["output_folder_id"]
        assert type(artifact["size_bytes"]) is int and artifact["size_bytes"] > 0
        assert re.fullmatch(r"[0-9]+", artifact["version"])
        assert datetime.fromisoformat(artifact["modified_time"].replace("Z", "+00:00")).tzinfo is not None
        binary = data["synthetic_artifact_bytes"][kind]["text"].encode("utf-8")
        assert len(binary) == artifact["size_bytes"]
        assert hashlib.sha256(binary).hexdigest() == artifact["sha256"] == output[kind + "_sha256"]


def test_fixture_canonical_hashes_are_reproducible():
    data = fixture()
    output = data["output"]
    receipt = output["terminal_receipt"]
    assert batch_plan._digest(data["queue_setup"]["files"]) == data["queue_setup"]["inventory_sha256"]
    assert batch_plan._digest(output["artifact_manifest"]) == output["artifact_manifest_sha256"]
    assert receipt["artifact_manifest_sha256"] == output["artifact_manifest_sha256"]
    assert batch_plan._digest({k: v for k, v in receipt.items() if k != "evidence_sha256"}) == (
        receipt["evidence_sha256"]) == output["terminal_evidence_sha256"]


def test_sql_plan_references_parameters_and_evidence_status_are_consistent():
    plan = json.loads((ROOT / "database/tests/plans/fixed_subset_admission_v1.json").read_text(encoding="utf-8"))
    assert plan["status"] == "DESIGN_ONLY"
    assert plan["sql_executed"] is False
    assert plan["postgres_test_status"] == "NOT_RUN"
    assert plan["production_authorization"] is False
    assert plan["execution_allowed"] is False
    assert plan["release_allowed"] is False
    assert plan["no_live_io"] is True
    cases = plan["cases"]
    assert len({case["id"] for case in cases}) == len(cases) == plan["counts"]["top_level_scenarios"]
    assert {case["group"] for case in cases} >= {
        "concurrency", "fault_atomicity", "legacy_race", "historical_cutover",
        "recovery", "source_freshness", "observation", "worker_boundary", "terminal_boundary",
    }
    terminal_variants = [v for case in cases for v in case.get("malformed_variants", [])]
    assert len({v["id"] for v in terminal_variants}) == len(terminal_variants) == (
        plan["counts"]["terminal_malformed_variants"])

    def inspect(value):
        if isinstance(value, list):
            for item in value:
                inspect(item)
        elif isinstance(value, dict):
            if "sql_executed" in value:
                assert value["sql_executed"] is False
            if "postgres_test_status" in value:
                assert value["postgres_test_status"] == "NOT_RUN"
            if "steps" in value:
                assert value["status"] == "NOT_RUN"
                assert value["steps"] and value["expected"]
            if "session" in value:
                assert value["session"] in plan["sessions"]
            if "sql" in value:
                assert isinstance(value["sql"], str) and value["sql"].strip()
                positions = {int(n) for n in re.findall(r"\$(\d+)", value["sql"])}
                assert positions == set(range(1, len(value.get("parameters", [])) + 1))
            if isinstance(value.get("assertions"), list):
                for reference in value["assertions"]:
                    if isinstance(reference, str):
                        assert reference in plan["assertions"]
            for item in value.values():
                inspect(item)

    inspect(plan)
