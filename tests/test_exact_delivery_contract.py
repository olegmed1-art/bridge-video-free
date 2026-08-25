from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_exact_operator_separates_compute_delivery_and_methodology_states():
    operator = (ROOT / "ops/universal_video_diana11_operator.sh").read_text(encoding="utf-8")
    assert "UV_STATE=TECHNICAL_CONFORMANT" in operator
    assert "UV_STATE=REVIEW" in operator
    assert "UV_STATE=NONCONFORMANT" in operator
    assert "UV_STATE=CONFLICT" in operator
    assert "UV_PUBLICATION_STATE=PUBLISHED_VERIFIED" in operator
    assert "UV_BRIDGE_PRODUCTION_READY=NO" in operator
    assert "UV_PEDAGOGICAL_STATUS=NOT_EVALUATED" in operator
    assert "POST_HOC_OBSERVATION" in operator
    assert "c80f34c4018c0861c5ba85d9ab0efac63e84027eca755783d15206d416f2d7f6" in operator
    subprocess.run(["bash", "-n", str(ROOT / "ops/universal_video_diana11_operator.sh")], check=True)


def test_exact_sudo_surface_has_no_arbitrary_publish_arguments():
    installer = (ROOT / "ops/install_universal_video_diana11_operator.sh").read_text(encoding="utf-8")
    assert "/usr/local/sbin/universal-video-diana11 conform-bridge" in installer
    assert "/usr/local/sbin/universal-video-diana11 publish-bridge" in installer
    sudoers = installer.split("cat > \"$tmp\" <<'EOF'", 1)[1].split("EOF", 1)[0]
    assert "NOPASSWD:ALL" not in sudoers
    assert "NOPASSWD: ALL" not in sudoers
    assert "DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'" in installer
    assert '${EXPECTED_RUNTIME_COMMIT:?EXPECTED_RUNTIME_COMMIT is required}' in installer
    assert "runtime checkout is dirty" in installer
    assert "operator source does not match runtime commit" in installer
    subprocess.run(["bash", "-n", str(ROOT / "ops/install_universal_video_diana11_operator.sh")], check=True)


def test_enqueue_never_root_opens_worker_writable_predictable_temp_path():
    operator = (ROOT / "ops/universal_video_diana11_operator.sh").read_text(encoding="utf-8")
    submit = operator.split("submit_for(){", 1)[1].split("publish_bridge(){", 1)[0]
    assert "readonly ROOT_STAGING='/opt/bridge-school/.universal-video-diana11-staging'" in operator
    assert "readonly PUBLISHED_DIR='/opt/bridge-school/.universal-video-diana11-published'" in operator
    assert 'mktemp -p "$ROOT_STAGING"' in submit
    assert "root:root:700" in operator
    assert "share a filesystem" in submit
    assert 'tmp="$SPOOL/inbox/' not in submit
    assert '$SPOOL/published' not in operator
    assert 'verify_root_control_dir "$PUBLISHED_DIR"' in operator
    assert 'install -d -o root -g universal-video' not in operator
    assert 'oracle_universal_video_spool_guard.sh' in operator
    assert 'universal_video_receipt_reader.py' in operator
    assert 'inspect-done' in operator
    assert 'inspect-failed' in operator
    worker_receipt_reads = operator.split("state_for(){", 1)[1].split("submit_for(){", 1)[0]
    assert 'json.load(open(sys.argv[1]' not in worker_receipt_reads
    assert 'cmp -s "$tmp" "$SPOOL/inbox/$job_file"' not in operator


def test_delivery_workflow_is_one_exact_experiment():
    workflow = (ROOT / ".github/workflows/oracle-diana11-delivery.yml").read_text(encoding="utf-8")
    assert "x['issue']==547" in workflow
    assert "{'inspect-status-bridge','conform-publish-bridge'}" in workflow
    assert "x['job_id']=='diana11-bridge-20260825-01'" in workflow
    assert "sudo -n /usr/local/sbin/universal-video-diana11 publish-bridge" in workflow
    assert "sudo -n /usr/local/sbin/universal-video-diana11 status-bridge" in workflow
    assert "Exact status was read without submit, ASR/media processing, or publication" in workflow
    assert "re.fullmatch(pattern,line)" in workflow
    assert "UV_ERROR_TYPE=(?:MULTIPLE_SPOOL_STATES|UNSAFE_SPOOL_RECEIPT|UNSAFE_FAILED_RECEIPT|DONE_RECEIPT_IDENTITY_MISMATCH|UNEXPECTED_DONE_STATUS|RESULT_CONFORMANCE_FAILED)" in workflow
    diagnostic = workflow.split("inspect-status-bridge)", 1)[1].split("conform-publish-bridge)", 1)[0]
    assert "submit" not in diagnostic
    assert "production promotion is BLOCKED" in workflow
    assert "REMOTE_VALIDATED" in workflow
    assert "No conformance or publication claim is established" in workflow


def test_bootstrap_is_bound_to_predeployed_clean_runtime_commit():
    workflow = (ROOT / ".github/workflows/oracle-diana11-operator-bootstrap.yml").read_text(encoding="utf-8")
    assert "expected_runtime_commit" in workflow
    assert "EXPECTED_RUNTIME_COMMIT='${{ needs.validate.outputs.expected_runtime_commit }}'" in workflow
    assert "assert re.fullmatch(r'[0-9a-f]{40}'" in workflow
