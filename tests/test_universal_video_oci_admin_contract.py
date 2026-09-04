import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = (ROOT / "ops/universal_video_oci_admin_entrypoint.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "ops/install_universal_video_ocarun_admin.sh").read_text(encoding="utf-8")
OPERATOR_INSTALL = (ROOT / "ops/install_universal_video_operator.sh").read_text(encoding="utf-8")
CLOUD = (ROOT / "ops/cloud_shell_install_bounded_oci_admin.sh").read_text(encoding="utf-8")
VIDEO_CLOUD = (ROOT / "ops/cloud_shell_install_universal_video_bounded_admin.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/oracle-universal-video-admin.yml").read_text(encoding="utf-8")
PRODUCTIONIZE = (ROOT / "ops/oracle_universal_video_productionize.sh").read_text(encoding="utf-8")
MAINTENANCE_UNIT = (ROOT / "deploy/oracle-universal-video/universal-video-maintenance.service").read_text(encoding="utf-8")


def test_entrypoint_is_fixed_and_no_asr_productionization_only():
    assert "usage: universal-video-oci-admin audit|productionize" in ENTRY
    assert "audit) audit ;;" in ENTRY
    assert "productionize) productionize ;;" in ENTRY
    assert "readonly UV_RUNTIME_COMMIT='07ce0495959e0f798b4a6e5ca5b31423cccfa849'" in ENTRY
    assert "readonly ACTIVATION_BLOB='0343e1a3c8e5a87c4c1931ad738e1af855266802'" in ENTRY
    assert "readonly PRODUCTIONIZE_BLOB='69b7243da69076e94891148467e04d10bbc7b058'" in ENTRY
    assert "readonly DRIVE_PROBE_FILE_ID='1RKrDWP6IOfVyuDWRMIsiUT62vpmVW9VS'" in ENTRY
    assert "readonly DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'" in ENTRY
    assert "UNIVERSAL_VIDEO_RUN_SMOKE=0" in ENTRY
    assert "UNIVERSAL_VIDEO_PREWARM_MODEL=0" in ENTRY
    assert "UNIVERSAL_VIDEO_SKIP_ADMIN_INSTALL=1" in ENTRY
    assert "universal_video_admin=preserved_revision_bound" in ENTRY
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


def test_admin_runtime_pin_is_one_self_consistent_published_tree():
    def pinned(name, text):
        match = re.search(rf"readonly {name}='([^']+)'", text)
        assert match, name
        return match.group(1)

    runtime = pinned("UV_RUNTIME_COMMIT", ENTRY)
    installer_runtime = re.search(
        r"readonly UV_RUNTIME_COMMIT='([0-9a-f]{40})'", INSTALL
    )
    assert installer_runtime
    assert runtime == installer_runtime.group(1)
    assert (
        runtime,
        pinned("ACTIVATION_BLOB", ENTRY),
        pinned("PRODUCTIONIZE_BLOB", ENTRY),
    ) == (
        "07ce0495959e0f798b4a6e5ca5b31423cccfa849",
        "0343e1a3c8e5a87c4c1931ad738e1af855266802",
        "69b7243da69076e94891148467e04d10bbc7b058",
    )
    assert "ORACLE_WORKLOAD_FENCE_HELD" in PRODUCTIONIZE
    assert "flock -n -x 9" in PRODUCTIONIZE
    assert "flock -x /run/lock/oracle-workload-mutation.lock" in MAINTENANCE_UNIT


def test_sudoers_surface_is_exact_and_not_broad():
    audit_line = "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin audit"
    productionize_line = "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-oci-admin productionize"
    spool_repair_line = "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-spool-repair"
    evidence_export_line = 'ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-evidence-export ""'
    assert audit_line in INSTALL
    assert productionize_line in INSTALL
    assert spool_repair_line in INSTALL
    assert evidence_export_line in INSTALL
    sudo_lines = [line.strip() for line in INSTALL.splitlines() if line.strip().startswith("ocarun ALL=")]
    assert sudo_lines == [audit_line, productionize_line, spool_repair_line, evidence_export_line]
    assert "grep -Ev '^[[:space:]]*(#|$)'" in INSTALL
    assert "NOPASSWD:[[:space:]]*ALL" in INSTALL
    assert "visudo -cf" in INSTALL
    assert "install -o root -g root -m 0755" in INSTALL
    assert "install -o root -g root -m 0440" in INSTALL
    assert "sudo -u ocarun sudo -n \"$TARGET\" audit" in INSTALL
    assert "sudo -u ocarun sudo -n \"$EXPORT_TARGET\" unexpected" in INSTALL
    assert "bash -c" not in INSTALL
    assert "eval " not in INSTALL
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-admin-ocarun'" in INSTALL
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-operator-ocarun'" in OPERATOR_INSTALL
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-ocarun'" not in INSTALL
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-ocarun'" not in OPERATOR_INSTALL


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


def test_productionize_hands_fence_to_exact_maintenance_unit():
    register = PRODUCTIONIZE.index('systemctl start --no-block "$MAINT_SERVICE"')
    handoff = PRODUCTIONIZE.index("flock -u 9")
    start = PRODUCTIONIZE.index('systemctl start "$MAINT_SERVICE"', handoff)
    reacquire = PRODUCTIONIZE.index("flock -x 9", start)
    result_check = PRODUCTIONIZE.index('systemctl show "$MAINT_SERVICE" -p Result', start)
    activating = PRODUCTIONIZE.index('== activating', register)
    assert register < activating < handoff < start
    assert start < reacquire
    assert reacquire < result_check
    assert "exec 9>&-" not in PRODUCTIONIZE
    assert "if (( owns_fence == 0 ))" not in PRODUCTIONIZE
    assert "flock -n -x 9" in PRODUCTIONIZE
    assert "universal_video.maintenance --base-dir \"$BASE_DIR\" --apply" not in PRODUCTIONIZE
    assert "EnvironmentFile=/opt/bridge-school/universal-video/universal-video.env" in MAINTENANCE_UNIT
    assert "CPUQuota=50%" in MAINTENANCE_UNIT
    assert "MemoryMax=256M" in MAINTENANCE_UNIT
    assert "NoNewPrivileges=true" in MAINTENANCE_UNIT
    assert "ProtectSystem=strict" in MAINTENANCE_UNIT
    assert "ExecStart=/usr/bin/flock -x /run/lock/oracle-workload-mutation.lock" in MAINTENANCE_UNIT


def test_video_only_cloud_bootstrap_does_not_depend_on_assistant_lab_audit():
    assert "ops/install_universal_video_ocarun_admin.sh" in VIDEO_CLOUD
    assert "install_assistant_lab_ocarun_admin.sh" not in VIDEO_CLOUD
    assert "assistant-lab-oci-admin" not in VIDEO_CLOUD
    assert "ORACLE_UNIVERSAL_VIDEO_BOUNDED_ADMIN_BOOTSTRAP_PASS" in VIDEO_CLOUD
    assert "SOURCE_COMMIT='$BOOTSTRAP_COMMIT'" in VIDEO_CLOUD
    assert "cmp -s \"$actual\" \"$expected\"" in VIDEO_CLOUD
