from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = (ROOT / "ops/universal_video_oci_admin_entrypoint.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "ops/install_universal_video_ocarun_admin.sh").read_text(encoding="utf-8")
OPERATOR_INSTALL = (ROOT / "ops/install_universal_video_operator.sh").read_text(encoding="utf-8")
CLOUD = (ROOT / "ops/cloud_shell_install_bounded_oci_admin.sh").read_text(encoding="utf-8")
VIDEO_CLOUD = (ROOT / "ops/cloud_shell_install_universal_video_bounded_admin.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/oracle-universal-video-admin.yml").read_text(encoding="utf-8")


def test_entrypoint_is_fixed_and_no_asr_productionization_only():
    assert "usage: universal-video-oci-admin audit|productionize|evidence-export" in ENTRY
    assert "audit) audit ;;" in ENTRY
    assert "productionize) productionize ;;" in ENTRY
    assert "evidence-export) evidence_export ;;" in ENTRY
    assert "readonly UV_RUNTIME_COMMIT='7e46f0327d6094400e0d35ec6af20408cc97683e'" in ENTRY
    assert "readonly ACTIVATION_BLOB='bbf4dc5779726fca415f641b90d017a802daaabf'" in ENTRY
    assert "readonly PRODUCTIONIZE_BLOB='9a76e06ed1cb7ecc92102e5c16cf215c18f9159d'" in ENTRY
    assert "readonly DRIVE_PROBE_FILE_ID='1RKrDWP6IOfVyuDWRMIsiUT62vpmVW9VS'" in ENTRY
    assert "readonly DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'" in ENTRY
    assert "UNIVERSAL_VIDEO_RUN_SMOKE=0" in ENTRY
    assert "UNIVERSAL_VIDEO_PREWARM_MODEL=0" in ENTRY
    assert "UNIVERSAL_VIDEO_DRIVE_SOURCE_NO_ASR_PASS" in ENTRY
    assert "asr_started=0" in ENTRY
    assert "faster_whisper" not in ENTRY
    assert "run_job" not in ENTRY
    assert "UNIVERSAL_VIDEO_OCI_ADMIN_ERROR stage=${CURRENT_STAGE} rc=${rc}" in ENTRY
    assert "CURRENT_STAGE='productionize_activation'" in ENTRY
    assert "CURRENT_STAGE='productionize_script'" in ENTRY
    assert "CURRENT_STAGE='productionize_marker_validation'" in ENTRY
    assert "CURRENT_STAGE='productionize_dds3_after'" in ENTRY
    assert "eval " not in ENTRY
    assert "bash -c" not in ENTRY
    assert "sh -c" not in ENTRY


def test_sudoers_surface_is_exact_and_not_broad():
    audit_line = "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin audit"
    productionize_line = "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin productionize"
    evidence_export_line = "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin evidence-export"
    spool_repair_line = "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-spool-repair"
    assert audit_line in INSTALL
    assert productionize_line in INSTALL
    assert evidence_export_line in INSTALL
    assert spool_repair_line in INSTALL
    sudo_lines = [line.strip() for line in INSTALL.splitlines() if line.strip().startswith("ocarun ALL=")]
    assert sudo_lines == [audit_line, productionize_line, evidence_export_line, spool_repair_line]
    assert "grep -Ev '^[[:space:]]*(#|$)'" in INSTALL
    assert "NOPASSWD:[[:space:]]*ALL" in INSTALL
    assert "visudo -cf" in INSTALL
    assert "install -o root -g root -m 0755" in INSTALL
    assert "install -o root -g root -m 0440" in INSTALL
    assert "sudo -u ocarun sudo -n \"$TARGET\" audit" in INSTALL
    assert "bash -c" not in INSTALL
    assert "eval " not in INSTALL
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-admin-ocarun'" in INSTALL
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-operator-ocarun'" in OPERATOR_INSTALL
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-ocarun'" not in INSTALL
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-ocarun'" not in OPERATOR_INSTALL


def test_evidence_export_admin_is_fixed_input_idle_and_sanitized():
    assert "readonly EVIDENCE_REQUEST_PATH=\"$EVIDENCE_REQUEST_DIR/evidence-export-request.json\"" in ENTRY
    assert "readonly EVIDENCE_STATUS_PATH=\"$EVIDENCE_STATUS_DIR/universal-video-status.json\"" in ENTRY
    assert "readonly MAX_EVIDENCE_REQUEST_BYTES=16384" in ENTRY
    assert "readonly MAX_EVIDENCE_RECEIPT_BYTES=32768" in ENTRY
    assert "readonly EVIDENCE_EXPORTER_PIN='/etc/bridge-school/universal-video-admin-source-commit'" in ENTRY
    assert "-m universal_video.status_attestation" in ENTRY
    assert "evidence_export_running_job_guard" in ENTRY
    assert "evidence_export_second_running_job_guard" in ENTRY
    assert "universal-video has a running job" in ENTRY
    assert "universal-video accepted a job during evidence export" in ENTRY
    assert "_validate_request(_read_regular_json" in ENTRY
    assert "publication_state')=='NOT_PUBLISHED'" in ENTRY
    assert "school_canon_changed') is False" in ENTRY
    assert "UNIVERSAL_VIDEO_RUN_SMOKE=1" not in ENTRY
    assert "universal-video-resident-status-v1" not in ENTRY


def test_installer_binds_admin_to_the_exact_deployed_source_commit():
    assert "readonly SOURCE_PIN='/etc/bridge-school/universal-video-admin-source-commit'" in INSTALL
    assert 'install -o root -g universal-video -m 0440 "$tmp/source-commit" "$SOURCE_PIN"' in INSTALL
    assert "unexpected source pin ownership/mode" in INSTALL
    assert "unexpected source pin content" in INSTALL


def test_cloud_shell_bootstrap_is_single_fixed_host_path():
    assert "readonly ORACLE_HOST='158.180.47.161'" in CLOUD
    assert "readonly ORACLE_USER='ubuntu'" in CLOUD
    assert 'readonly SSH_KEY_PATH="$HOME/.ssh/bridge_school_dds3_oracle"' in CLOUD
    assert "readonly BOOTSTRAP_COMMIT='47d37493679063b5f1726aa33778a80124e2706e'" in CLOUD
    assert "SOURCE_COMMIT is not current main" not in CLOUD
    assert "git ls-remote" not in CLOUD
    for fingerprint in (
        "SHA256:NXmGcng3fzof9b6Hs5Xgh4yYnzxGyVwa/EcfOxu0WPk",
        "SHA256:UGJo5yPdnk/wf8DVrzvXt2xJkE9GJ8+3IIcQ2vA+mkc",
        "SHA256:eRCJ8c4V7HCBlIoNVSlpPSWZE5xPUMjBD6f0PvHDj64",
    ):
        assert fingerprint in CLOUD
    assert "cmp -s \"$actual\" \"$expected\"" in CLOUD
    assert "ops/install_assistant_lab_ocarun_admin.sh" in CLOUD
    assert "ops/install_universal_video_ocarun_admin.sh" in CLOUD
    assert "ORACLE_BOUNDED_ADMIN_PIN_PASS" in CLOUD
    assert "ORACLE_BOUNDED_OCARUN_ADMIN_BOOTSTRAP_PASS" in CLOUD
    assert "SOURCE_COMMIT='$BOOTSTRAP_COMMIT'" in CLOUD
    assert 'case "$1"' not in CLOUD
    assert '[[ $#' not in CLOUD
    assert "eval " not in CLOUD


def test_workflow_accepts_only_two_operations_and_one_instance():
    assert "x['operation'] in {'audit','productionize'}" in WORKFLOW
    assert "assert set(x)=={'request_id','operation','instance_id'}" in WORKFLOW
    assert "ocid1.instance.oc1.eu-frankfurt-1.antheljtruoejaica7hj5oubnh2cctnjr7ti7llcgo6ho6wdvgvui6td7saq" in WORKFLOW
    assert "universal-video-oci-admin audit 2>&1" in WORKFLOW
    assert "universal-video-oci-admin productionize 2>&1" in WORKFLOW
    assert "UNIVERSAL_VIDEO_OCI_ADMIN_EXECUTION_FAIL" in WORKFLOW
    assert "UNIVERSAL_VIDEO_[A-Z0-9_]+(PASS|FAIL|ERROR)" in WORKFLOW
    assert "--timeout-in-seconds 3600" in WORKFLOW
    assert "UNIVERSAL_VIDEO_OCI_ADMIN_REMOTE_PASS" in WORKFLOW
    assert "UNIVERSAL_VIDEO_OCI_ADMIN_EXTERNAL_DDS3_PASS" in WORKFLOW


def test_workflow_reuses_the_proven_bounded_oci_config_contract():
    assert "OCI_CLI_CONFIG: ${{ secrets.OCI_CLI_CONFIG }}" in WORKFLOW
    assert "OCI_KEY: ${{ secrets.OCI_CLI_KEY_CONTENT }}" in WORKFLOW
    assert "OCI_CLI_USER" not in WORKFLOW
    assert "OCI_CLI_TENANCY" not in WORKFLOW
    assert "OCI_CLI_FINGERPRINT" not in WORKFLOW
    assert "OCI_CLI_REGION" not in WORKFLOW
    assert "region=eu-frankfurt-1" in WORKFLOW
    assert "oci-cli==3.90.3" in WORKFLOW
    assert "openssl pkey" in WORKFLOW
    assert "oci_user=\"" not in WORKFLOW
    assert "\\\\2/p" not in WORKFLOW


def test_workflow_does_not_publish_raw_remote_output_or_oauth_values():
    assert 'uv-admin-raw.txt" > "$RUNNER_TEMP/uv-admin-safe.txt"' in WORKFLOW
    assert 'cat "$RUNNER_TEMP/uv-admin-safe.txt"' in WORKFLOW
    assert 'cat "$RUNNER_TEMP/uv-admin-raw.txt"' not in WORKFLOW
    assert "No OAuth value/raw command output was published" in WORKFLOW
    assert "client_secret" not in WORKFLOW
    assert "refresh_token" not in WORKFLOW
    assert "GOOGLE_DRIVE_OAUTH_JSON" not in WORKFLOW


def test_video_only_cloud_bootstrap_does_not_depend_on_assistant_lab_audit():
    assert "ops/install_universal_video_ocarun_admin.sh" in VIDEO_CLOUD
    assert "install_assistant_lab_ocarun_admin.sh" not in VIDEO_CLOUD
    assert "assistant-lab-oci-admin" not in VIDEO_CLOUD
    assert "ORACLE_UNIVERSAL_VIDEO_BOUNDED_ADMIN_BOOTSTRAP_PASS" in VIDEO_CLOUD
    assert "SOURCE_COMMIT='$BOOTSTRAP_COMMIT'" in VIDEO_CLOUD
    assert "cmp -s \"$actual\" \"$expected\"" in VIDEO_CLOUD
