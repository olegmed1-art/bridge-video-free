from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/oracle-universal-video-job.yml").read_text(
    encoding="utf-8"
)
OPERATOR_INSTALL = (ROOT / "ops/install_universal_video_operator.sh").read_text(
    encoding="utf-8"
)
ADMIN_INSTALL = (ROOT / "ops/install_universal_video_ocarun_admin.sh").read_text(
    encoding="utf-8"
)


def test_job_uses_pinned_bounded_ssh_transport():
    assert "ORACLE_HOST: 158.180.47.161" in WORKFLOW
    assert "ORACLE_USER: ubuntu" in WORKFLOW
    assert (
        "EXPECTED_FINGERPRINT: "
        "SHA256:NXmGcng3fzof9b6Hs5Xgh4yYnzxGyVwa/EcfOxu0WPk"
    ) in WORKFLOW
    assert 'ops/oracle_known_hosts_from_scan.sh "$ORACLE_HOST" "$EXPECTED_FINGERPRINT" "$known"' in WORKFLOW
    assert "StrictHostKeyChecking=yes" in WORKFLOW
    assert "StrictHostKeyChecking=no" not in WORKFLOW
    assert "BatchMode=yes" in WORKFLOW
    assert "IdentitiesOnly=yes" in WORKFLOW
    assert "timeout 180 ssh" in WORKFLOW
    assert "ServerAliveInterval=15" in WORKFLOW
    assert "ServerAliveCountMax=2" in WORKFLOW


def test_job_invokes_only_fixed_resident_admin_surfaces():
    assert "oci instance-agent command" not in WORKFLOW
    assert "--execution-user" not in WORKFLOW
    assert "repair_cmd='sudo -n -u ocarun sudo -n /usr/local/sbin/universal-video-spool-repair'" in WORKFLOW
    assert 'submit_cmd="sudo -n -u ocarun sudo -n /usr/local/sbin/universal-video submit-drive-base64 \'$payload\'"' in WORKFLOW
    assert 'status_cmd="sudo -n -u ocarun sudo -n /usr/local/sbin/universal-video status \'$JOB_ID\'"' in WORKFLOW
    assert 'run_remote "$repair_cmd"' in WORKFLOW
    assert 'run_remote "$submit_cmd"' in WORKFLOW
    assert 'run_remote "$status_cmd"' in WORKFLOW
    operator = (ROOT / "ops/universal_video_operator.sh").read_text(encoding="utf-8")
    assert "systemctl is-active --quiet universal-video-container.service" in operator
    assert "legacy universal-video.service still active" in operator


def test_bootstrap_smoke_does_not_require_container_before_promotion():
    bootstrap = (ROOT / "ops/oracle_universal_video_run_command.sh").read_text(encoding="utf-8")
    assert "/usr/local/sbin/universal-video status .." in bootstrap
    assert "UNIVERSAL_VIDEO_OPERATOR_REJECTION_SMOKE_PASS" in bootstrap
    assert "/usr/local/sbin/universal-video status install-smoke" not in bootstrap


def test_job_payload_binds_request_and_requested_runtime_for_resident_attestation():
    assert "metadata['request_commit']=os.environ['GITHUB_SHA'].lower()" in WORKFLOW
    assert "metadata['requested_runtime_commit']=requested" in WORKFLOW
    assert "requested_runtime_commit is required for resident attestation" in WORKFLOW


def test_existing_job_resumes_by_exact_id_instead_of_duplicate_submission():
    assert "UV_ERROR=job id already exists; use status or a new id" in WORKFLOW
    assert "PRE_SUBMIT_ERROR_CODE=UV_EXISTING_JOB_RESUMED" in WORKFLOW
    assert "PRE_SUBMIT_ERROR_CODE=UV_RESUME_STATUS_COMMAND_FAILED" in WORKFLOW
    assert "entry='RESUMED'" in WORKFLOW
    assert 'initial="$(run_remote "$status_cmd")"' in WORKFLOW
    assert "SOURCE_READY_ON_ORACLE|RUNNING|PROCESSING" in WORKFLOW


def test_operator_and_admin_sudoers_ownership_cannot_collide():
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-operator-ocarun'" in OPERATOR_INSTALL
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-admin-ocarun'" in ADMIN_INSTALL
    assert "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video submit-drive-base64 *" in OPERATOR_INSTALL
    assert "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video status *" in OPERATOR_INSTALL
    assert "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-spool-repair" in ADMIN_INSTALL
    assert "/etc/sudoers.d/universal-video-ocarun'" not in OPERATOR_INSTALL
    assert "/etc/sudoers.d/universal-video-ocarun'" not in ADMIN_INSTALL


def test_job_keeps_remote_output_fail_closed_and_bounded():
    assert '"$ORACLE_USER@$ORACLE_HOST" "$command" 2>/dev/null' in WORKFLOW
    assert "initial_safe=" in WORKFLOW
    assert "safe=" in WORKFLOW
    assert "grep -E '^UV_(STATE|RESULT_STATUS|RESULT_DIR|CONFORMANCE_STATE|" in WORKFLOW
    assert "|ERROR_TYPE|ERROR_CODE)=" in WORKFLOW
    assert "|ERROR_TYPE|ERROR)=" not in WORKFLOW
    assert "PRE_SUBMIT_ERROR_CODE=UV_SPOOL_REPAIR_COMMAND_FAILED" in WORKFLOW
    assert "PRE_SUBMIT_ERROR_CODE=UV_SUBMIT_SERVICE_INACTIVE" not in WORKFLOW
    assert "UV_ERROR=universal-video-container.service inactive" in WORKFLOW
    assert "code='UV_SUBMIT_SERVICE_INACTIVE'" in WORKFLOW
    assert "PRE_SUBMIT_ERROR_CODE=UV_SUBMIT_STATE_INVALID" in WORKFLOW
    assert "PRE_SUBMIT_ERROR_CODE=UV_STATUS_COMMAND_FAILED" in WORKFLOW
    assert "PRE_SUBMIT_ERROR_CODE=UV_STATUS_STATE_INVALID" in WORKFLOW
    assert "UV_OPERATOR_ERROR_CODE=$code" in WORKFLOW
