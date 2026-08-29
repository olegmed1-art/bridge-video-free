from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_universal_video_evidence_export import _inputs
from universal_video.evidence_export import EvidenceExportError
from universal_video.runtime_shadow_evidence import RECEIPT_FILE, SHADOW_OUTPUT_FILE
from universal_video.status_attestation import STATUS_SCHEMA, build_resident_status


def _snapshot(request: Path, spool: Path):
    return build_resident_status(
        request_path=request,
        spool_root=spool,
        exporter_commit="f" * 40,
        now=1000.0,
    )


def test_v3_schema_and_snapshot_are_single_job_fail_closed(tmp_path: Path):
    request, _status, spool, _result, _final = _inputs(tmp_path)
    value = _snapshot(request, spool)
    schema = json.loads(
        (Path(__file__).parents[1] / "ops/universal-video-resident-status-v3.schema.json").read_text()
    )
    assert value["schema"] == STATUS_SCHEMA
    assert schema["properties"]["schema"]["const"] == STATUS_SCHEMA
    assert schema["additionalProperties"] is False
    assert value["active_jobs"] == []
    assert value["exporter_commit"] == "f" * 40
    assert len(value["job_attestations"]) == 1
    job = value["job_attestations"][0]
    assert job["requested_runtime_commit"] == job["observed_job_runtime_commit"] == "a" * 40
    assert job["runtime_shadow_attestation"]["state"] == "UNAVAILABLE"
    assert job["runtime_shadow_attestation"]["unavailable_reasons"] == ["SHADOW_RECEIPT_MISSING"]


@pytest.mark.parametrize("field", ["processing_revision", "runtime", "metadata"])
def test_snapshot_rejects_each_runtime_revision_mismatch(tmp_path: Path, field: str):
    request, _status, spool, result, _final = _inputs(tmp_path)
    manifest_path = result / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if field == "processing_revision":
        manifest[field] = "b" * 40
    elif field == "runtime":
        manifest[field]["source_revision"] = "b" * 40
    else:
        manifest[field]["requested_runtime_commit"] = "b" * 40
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="runtime revision binding mismatch"):
        _snapshot(request, spool)


def test_snapshot_rejects_active_job_partial_shadow_and_symlink(tmp_path: Path):
    request, _status, spool, result, _final = _inputs(tmp_path)
    running = spool / "running"
    running.mkdir()
    (running / "other.json").write_text("{}", encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="active job"):
        _snapshot(request, spool)
    (running / "other.json").unlink()

    (result / SHADOW_OUTPUT_FILE).write_text("{}\n", encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="partial unattested"):
        _snapshot(request, spool)
    (result / SHADOW_OUTPUT_FILE).unlink()

    target = result / "runtime.json"
    target.write_text("{}", encoding="utf-8")
    (result / RECEIPT_FILE).symlink_to(target)
    with pytest.raises(EvidenceExportError, match="unsafe fixed input"):
        _snapshot(request, spool)


def test_snapshot_rejects_duplicate_nonfinite_and_oversize_control(tmp_path: Path):
    request, _status, spool, _result, _final = _inputs(tmp_path)
    request.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="invalid fixed JSON"):
        _snapshot(request, spool)

    request.write_text('{"schema":NaN}', encoding="utf-8")
    with pytest.raises(EvidenceExportError, match="invalid fixed JSON"):
        _snapshot(request, spool)

    request.write_bytes(b"x" * (16 * 1024 + 1))
    with pytest.raises(EvidenceExportError, match="byte cap"):
        _snapshot(request, spool)
