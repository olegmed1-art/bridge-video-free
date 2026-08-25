import hashlib
import json
from pathlib import Path

from universal_video.contract import CONTRACT_VERSION, canonical_job_hash, validate_job


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "ops/experiments/uv-diana11-durable-002.preflight.json"


def _sha256_json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def test_diana11_durable_002_is_frozen_and_non_executable():
    spec = json.loads(PREFLIGHT.read_text(encoding="utf-8"))

    assert spec["experiment_id"] == "UV-DIANA11-DURABLE-002"
    assert spec["issue"] == 562
    assert spec["state"] == "PREPARED_NOT_AUTHORIZED_TO_RUN"
    assert spec["execution_authorized"] is False
    assert spec["production_promotion"] == "BLOCKED"
    assert spec["wip"] == 1
    assert spec["max_fresh_jobs"] == 1
    assert spec["automatic_retries"] == 0
    assert spec["incremental_cost_cap_eur"] == 3
    assert all(value is False for value in spec["preflight_only"].values())


def test_diana11_durable_002_hashes_match_canonical_contract():
    spec = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    runtime = spec["runtime"]
    raw_job = spec["job"]

    payload = {
        "job_id": raw_job["job_id"],
        "profile": raw_job["profile"],
        "source": {
            "kind": raw_job["source"]["kind"],
            "file_id": raw_job["source"]["file_id"],
            "name": raw_job["source"]["name"],
        },
        "project": raw_job["project"],
        "metadata": raw_job["metadata"],
        "options": raw_job["options"],
    }
    job = validate_job(payload)

    assert canonical_job_hash(job) == raw_job["expected_canonical_job_hash"]
    assert runtime["contract"] == CONTRACT_VERSION
    assert runtime["source_revision"] != "development"
    assert len(runtime["source_revision"]) == 40
    assert runtime["whisper_model"]

    identity = {
        "contract": runtime["contract"],
        "source_revision": runtime["source_revision"],
        "whisper_model": runtime["whisper_model"],
    }
    assert _sha256_json(identity) == runtime["expected_processing_fingerprint"]


def test_diana11_durable_002_provenance_and_destination_are_exact():
    spec = json.loads(PREFLIGHT.read_text(encoding="utf-8"))

    assert spec["job"]["source"]["file_id"] == "1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C"
    assert spec["job"]["source"]["size_bytes"] == 740292560
    assert spec["destination"]["folder_id"] == "1I8cSuA-p0MpaZIbA33slks19KyvfJDMK"
    assert spec["destination"]["raw_media_allowed"] is False
    assert set(spec["required_provenance"]) == {
        "processing_fingerprint",
        "processing_revision",
        "processing_whisper_model",
    }
    assert spec["evidence_phase"] == "GENERATION_FINALIZATION"
