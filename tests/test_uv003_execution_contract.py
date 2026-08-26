from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "ops/universal_video_diana11_003_operator.sh"
INSTALLER = ROOT / "ops/install_universal_video_diana11_003_operator.sh"
WORKFLOW = ROOT / ".github/workflows/oracle-diana11-003-one-shadow-execution.yml"

JOB_ID = "diana11-shadow-20260826-001"
JOB_HASH = "a43e11beb0765aa91551d4c4a69767f02c4dcb3b5e485cd5bb0f2996e734d73d"
RUNTIME_COMMIT = "6a4e8248eedd00f849fcefd1bf41a51b26f5e7c6"
PROCESSING_FINGERPRINT = "371661d2a1858e576e2f618ddf504da724edc30089a9af88f9dd3a140ca30951"
SOURCE_ID = "1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C"
DESTINATION_ID = "1I8cSuA-p0MpaZIbA33slks19KyvfJDMK"


def _job() -> dict:
    return {
        "job_id": JOB_ID,
        "profile": "bridge_lesson",
        "source": {"kind": "google_drive", "file_id": SOURCE_ID, "name": "Диана 11"},
        "project": "Школа спортивного бриджа",
        "metadata": {
            "purpose": "UV-DIANA11-DURABLE-003 fresh provenance shadow",
            "human_requested": True,
        },
        "options": {
            "chunk_seconds": 600,
            "max_source_bytes": 2_147_483_648,
            "max_duration_seconds": 43_200.0,
        },
    }


def _fingerprint(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def test_frozen_job_and_processing_hashes_are_exact():
    assert _fingerprint({"contract": "universal-video-v1", **_job()}) == JOB_HASH
    assert (
        _fingerprint(
            {
                "contract": "universal-video-v1",
                "source_revision": RUNTIME_COMMIT,
                "whisper_model": "small",
            }
        )
        == PROCESSING_FINGERPRINT
    )


def test_operator_is_valid_bash_and_exactly_one_job():
    subprocess.run(["bash", "-n", str(OPERATOR)], check=True)
    source = OPERATOR.read_text(encoding="utf-8")
    assert f"readonly BRIDGE_JOB_ID='{JOB_ID}'" in source
    assert f"readonly BRIDGE_JOB_HASH='{JOB_HASH}'" in source
    assert f"readonly DRIVE_FILE_ID='{SOURCE_ID}'" in source
    assert "readonly SOURCE_SIZE_BYTES='740292560'" in source
    assert f"readonly DRIVE_RESULTS_FOLDER_ID='{DESTINATION_ID}'" in source
    assert f"readonly EXPECTED_RUNTIME_COMMIT='{RUNTIME_COMMIT}'" in source
    assert "readonly EXPECTED_WHISPER_MODEL='small'" in source
    assert f"readonly EXPECTED_PROCESSING_FINGERPRINT='{PROCESSING_FINGERPRINT}'" in source
    assert "'max_duration_seconds':43200.0" in source
    assert "UV-DIANA11-DURABLE-003 fresh provenance shadow" in source
    assert source.count("submit_for \"$BRIDGE_JOB_ID\"") == 1
    assert "UV_SUBMIT_COUNT=1" in source
    assert "UV_AUTOMATIC_RETRIES=0" in source
    for forbidden in ("ffmpeg ", "faster_whisper", "WhisperModel(", "spool/inbox/" + JOB_ID):
        assert forbidden not in source


def test_enqueue_uses_root_staging_and_atomic_collision_guard():
    source = OPERATOR.read_text(encoding="utf-8")
    submit = source.split("submit_for(){", 1)[1].split("publish_bridge(){", 1)[0]
    assert "readonly ROOT_STAGING='/opt/bridge-school/.universal-video-diana11-003-staging'" in source
    assert "readonly PUBLISHED_DIR='/opt/bridge-school/.universal-video-diana11-003-published'" in source
    assert 'mktemp -p "$ROOT_STAGING"' in submit
    assert 'ln "$tmp" "$SPOOL/inbox/$job_file"' in submit
    assert "ENQUEUE_COLLISION" in submit
    assert "FRESH_ID_PUBLICATION_CONFLICT" in submit
    assert "ORPHAN_RESULT_DIRECTORY" in source
    assert 'tmp="$SPOOL/inbox/' not in submit
    assert 'cmp -s "$tmp"' not in submit


def test_status_requires_generation_conformance_and_frozen_processing_identity():
    source = OPERATOR.read_text(encoding="utf-8")
    state = source.split("state_for(){", 1)[1].split("submit_for(){", 1)[0]
    assert "--evidence-phase GENERATION_FINALIZATION" in source
    assert "UV_STATE=TECHNICAL_CONFORMANT" in state
    assert "UV_TRANSCRIPT_QC=PASS" in state
    assert "processing_revision" in state and "EXPECTED_RUNTIME_COMMIT" in state
    assert "processing_model" in state and "EXPECTED_WHISPER_MODEL" in state
    assert "UV_PROCESSING_FINGERPRINT=" in state
    assert "UV_BRIDGE_PRODUCTION_READY=NO" in state
    assert "UV_PEDAGOGICAL_STATUS=NOT_EVALUATED" in state


def test_readback_command_is_independent_and_read_only():
    source = OPERATOR.read_text(encoding="utf-8")
    readback = source.split("readback_bridge(){", 1)[1].split("need_root", 1)[0]
    assert "requests.get" in readback
    assert "UV_READBACK_STATE=REMOTE_VERIFIED" in readback
    assert "UV_READBACK_BROAD_ACL=NO" in readback
    assert "UNIVERSAL_VIDEO_DIANA11_003_READBACK_PASS" in readback
    for forbidden in ("requests.post", "requests.put", "requests.patch", "requests.delete", "publish_result("):
        assert forbidden not in readback


def test_installer_grants_only_five_fixed_commands():
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
    source = INSTALLER.read_text(encoding="utf-8")
    sudoers = source.split("cat > \"$tmp\" <<'EOF'", 1)[1].split("EOF", 1)[0]
    expected = {
        "submit-bridge",
        "status-bridge",
        "conform-bridge",
        "publish-bridge",
        "readback-bridge",
    }
    assert sudoers.count("NOPASSWD:") == len(expected)
    for command in expected:
        assert f"/usr/local/sbin/universal-video-diana11-003 {command}" in sudoers
    assert "NOPASSWD:ALL" not in sudoers
    assert "NOPASSWD: ALL" not in sudoers
    assert "EXPECTED_SOURCE_SHA256" in source
    assert "runtime checkout is dirty" not in source or "RUNTIME_CONTRACT" in source
    assert "sudo -u ocarun sudo -n \"$TARGET\" unexpected" in source


def test_workflow_has_one_request_no_dispatch_and_no_submit_retry():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch" not in source
    assert "ops/oracle-diana11-003-execution-requests/*.json" in source
    assert "x['request_id']=='uv003-execute-one-shadow-20260826-01'" in source
    assert "x['issue']==612" in source
    assert "x['submit_limit']==1" in source
    assert "x['automatic_retries']==0" in source
    assert source.count("universal-video-diana11-003 submit-bridge") == 1
    assert "for _ in $(seq 1 300)" in source
    monitor = source.split("Submit once and monitor technical terminal state", 1)[1].split(
        "Publish exact compact bundle once", 1
    )[0]
    loop = monitor.split("for _ in $(seq 1 300)", 1)[1]
    assert "submit-bridge" not in loop
    assert "status-bridge" in loop
    assert "sleep 60" in loop


def test_publication_and_readback_are_strictly_gated():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "if: ${{ steps.execution.outputs.validated == 'true' }}" in source
    assert "if: ${{ steps.publication.outputs.validated == 'true' }}" in source
    assert "UV_RAW_MEDIA_PUBLISHED=NO" in source
    assert "UV_PUBLICATION_STATE=PUBLISHED_VERIFIED" in source
    assert "UV_READBACK_STATE=REMOTE_VERIFIED" in source
    assert '[[ -n "$pub_hash" && "$pub_hash" == "$read_hash" ]]' in source
    assert "PRODUCTION_PROMOTION=BLOCKED" in source
    assert "GITHUB_RAW_REMOTE_OUTPUT_RECORDED=NO" in source
    assert "actions/upload-artifact" not in source
