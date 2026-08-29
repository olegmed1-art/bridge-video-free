import os
from pathlib import Path
import pwd
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-evidence-export.yml"
ADMIN = ROOT / "ops/universal_video_oci_admin_entrypoint.sh"
INSTALL = ROOT / "ops/install_universal_video_ocarun_admin.sh"


def test_export_workflow_has_a_fixed_read_only_remote_surface():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ops/oracle-universal-video-evidence-export-requests/*.json" in text
    assert "expected exactly one evidence export request" in text
    assert "assert set(wrapper)=={'request_id','issue','export'}" in text
    assert "assert wrapper['issue']==819" in text
    assert "sudo -n /usr/local/sbin/universal-video-oci-admin evidence-export" in text
    assert "publication_state']=='NOT_PUBLISHED'" in text
    assert "school_canon_changed'] is False" in text


def test_oci_run_command_crosses_only_the_exact_root_owned_boundary():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    admin = ADMIN.read_text(encoding="utf-8")
    installer = INSTALL.read_text(encoding="utf-8")
    exact = "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin evidence-export"
    assert exact in installer
    assert "evidence-export) evidence_export ;;" in admin
    assert "EVIDENCE_EXPORTER_PIN='/etc/bridge-school/universal-video-admin-source-commit'" in admin
    assert "-m universal_video.status_attestation" in admin
    assert "universal_video_resident_evidence_export.py" in admin
    assert "systemctl is-active --quiet universal-video.service" in admin
    assert "universal-video-resident-status-v1" not in admin
    assert "source pin mismatch" in admin
    for root_only in (
        "install -d -m 0750 -o root",
        "chown root:universal-video",
        "runuser -u universal-video",
    ):
        assert root_only not in workflow


def test_original_inline_root_precondition_fails_for_unprivileged_executor():
    with tempfile.TemporaryDirectory(prefix="uv-root-precondition-", dir="/tmp") as directory:
        parent = Path(directory)
        parent.chmod(0o777)
        install = [
            "install", "-d", "-m", "0750", "-o", "root", "-g", "root",
            str(parent / "request-dir"),
        ]
        command = install
        if os.geteuid() == 0:
            command = ["runuser", "-u", pwd.getpwnam("nobody").pw_name, "--", *install]

        result = subprocess.run(command, check=False, capture_output=True, text=True)
        assert result.returncode != 0


def test_raw_remote_output_is_never_published_or_logged():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'uv-export-raw.txt' in text
    assert 'uv-export-safe.json' in text
    assert 'cat "$RUNNER_TEMP/uv-export-raw.txt"' not in text
    assert 'cat "$RUNNER_TEMP/uv-export-safe.json"' in text
    assert "No transcript text, raw command output, media" in text
    assert "transcript.jsonl" in text
    assert "transcript.txt" in text
    assert "bridge_positions_profiled_shadow.jsonl" in text
    assert "bridge_positions_profiled_shadow_runtime.json" in text
    assert "UV_EVIDENCE_EXPORT_FAIL stage=bounded_admin rc=%s" in text
    assert "assert len(failures)==1 and not found" in text
    assert "REMOTE_{stage.upper()}_RC_{rc}" in text
    assert "uv-export-failed" in text


def test_workflow_cannot_start_compute_or_promote_results():
    text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "universal-video submit",
        "spool/inbox",
        "systemctl start universal-video",
        "workflow_dispatch",
        "canonical_promotion_allowed'] is True",
        "school_canon_changed'] is True",
    )
    for token in forbidden:
        assert token not in text
    assert "External DDS3 non-regression" in text
    assert "fallback_used') is False" in text
