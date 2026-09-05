from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHARED_FENCE = "oracle-instance-workload-mutation"

POWER_GROUP = (
    "${{ (github.event_name == 'workflow_dispatch' && inputs.action == 'status') && "
    "format('oracle-instance-status-{0}', github.run_id) || (github.event_name == "
    "'issue_comment' && github.actor == github.repository_owner && "
    "github.event.comment.body == '/oracle-instance status') && "
    "format('oracle-instance-status-{0}', github.run_id) || "
    "((github.event_name == 'workflow_dispatch' && "
    "contains(fromJSON('[\"start\",\"stop\"]'), inputs.action)) || "
    "(github.event_name == 'issue_comment' && github.actor == github.repository_owner && "
    "contains(fromJSON('[\"/oracle-instance start\",\"/oracle-instance stop\"]'), "
    "github.event.comment.body))) && 'oracle-instance-workload-mutation' || "
    "format('oracle-instance-power-noop-{0}', github.run_id) }}"
)

MASS_LAUNCH_GROUP = (
    "${{ github.event_name == 'pull_request_target' && "
    "github.event.pull_request.head.repo.full_name == github.repository && "
    "github.event.pull_request.user.login == github.repository_owner && "
    "github.event.pull_request.base.ref == 'main' && "
    "github.event.pull_request.changed_files == 1 && "
    "'oracle-instance-workload-mutation' || "
    "format('oracle-dds3-pilot10k-launch-noop-{0}', github.run_id) }}"
)

MASS_OPERATOR_GROUP = (
    "${{ github.event_name == 'issue_comment' && "
    "github.event.comment.user.login == github.repository_owner && "
    "github.event.comment.body == '/dds3-pilot10k status' && "
    "format('oracle-dds3-pilot10k-status-{0}', github.run_id) || "
    "(github.event_name == 'issue_comment' && "
    "github.event.comment.user.login == github.repository_owner && "
    "contains(fromJSON('[\"/dds3-pilot10k start\",\"/dds3-main30k deploy\","
    "\"/dds3-main30k stage\",\"/dds3-main30k start\","
    "\"/dds3-main30k reconcile\"]'), github.event.comment.body)) && "
    "'oracle-instance-workload-mutation' || "
    "format('oracle-dds3-pilot10k-operator-noop-{0}', github.run_id) }}"
)

ORACLE_V2_GROUP = (
    "${{ github.event_name == 'issue_comment' &&\n"
    "    github.event.comment.user.login == github.repository_owner &&\n"
    "    github.event.comment.body == '/oracle-v2 diagnose-ben' &&\n"
    "    format('oracle-operator-v2-diagnose-{0}', github.run_id) ||\n"
    "    github.event_name == 'issue_comment' &&\n"
    "    github.event.comment.user.login == github.repository_owner &&\n"
    "    contains(fromJSON('[\"/oracle-v2 rollout-worker\","
    "\"/oracle-v2 rollout-dds3-runtime\",\"/oracle-v2 canary-worlds\","
    "\"/oracle-v2 rollout-ben\",\"/oracle-v2 canary-ben\","
    "\"/oracle-v2 canary-ben-dds3\","
    "\"/oracle-v2 benchmark-ben-100-500\"]'), github.event.comment.body) &&\n"
    "    'oracle-instance-workload-mutation' ||\n"
    "    format('oracle-operator-v2-noop-{0}', github.run_id) }}"
)

EXPECTED_PRODUCERS = {
    "oracle-diana11-002-job.yml": (
        {"pull_request", "push"},
        "${{ github.event_name == 'pull_request' && "
        "format('oracle-diana11-002-pr-{0}', github.event.pull_request.number) || "
        "format('oracle-diana11-002-request-{0}', github.sha) }}",
    ),
    "oracle-diana11-003-one-shadow-execution.yml": (
        {"pull_request", "push"},
        "${{ github.event_name == 'pull_request' && "
        "format('oracle-diana11-003-pr-{0}', github.event.pull_request.number) || "
        "format('oracle-diana11-003-request-{0}', github.sha) }}",
    ),
}

EXPECTED_RESEARCH_PRODUCERS = {
    "research-job-dds3-canary.yml": (
        {"pull_request", "issue_comment"},
        "${{ github.event_name == 'pull_request' && "
        "format('research-job-dds3-pr-{0}', github.event.pull_request.number) || "
        "(github.event_name == 'issue_comment' && "
        "github.event.comment.user.login == 'olegmed1-art' && "
        "github.event.comment.body == '/research-job canary-dds3-idempotency') && "
        "'oracle-instance-workload-mutation' || "
        "format('research-job-dds3-noop-{0}', github.run_id) }}",
    ),
    "research-job-neon-operator.yml": (
        {"pull_request", "issue_comment"},
        "${{ github.event_name == 'pull_request' && "
        "format('research-job-neon-pr-{0}', github.event.pull_request.number) || "
        "(github.event_name == 'issue_comment' && "
        "github.event.comment.user.login == 'olegmed1-art' && "
        "github.event.comment.body == '/research-job migrate-neon-and-canary') && "
        "'oracle-instance-workload-mutation' || "
        "format('research-job-neon-noop-{0}', github.run_id) }}",
    ),
}


def _workflow_text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _workflow_contract(name: str) -> tuple[set[str], dict[str, object]]:
    document = yaml.safe_load(_workflow_text(name))
    # YAML 1.1 parsers treat the unquoted GitHub Actions key `on` as boolean.
    triggers = document.get("on", document.get(True))
    assert isinstance(triggers, dict), name
    concurrency = document.get("concurrency")
    assert isinstance(concurrency, dict), name
    return set(triggers), concurrency


def _assert_workflow_mapping(name: str, events: set[str], group: str) -> None:
    actual_events, concurrency = _workflow_contract(name)
    assert actual_events == events, name
    assert concurrency == {"group": group, "cancel-in-progress": False}, name


def _workflows_containing(marker: str) -> set[str]:
    return {
        path.name
        for path in WORKFLOWS.glob("*.yml")
        if marker in path.read_text(encoding="utf-8")
    }


def test_all_submit_bridge_oracle_producers_have_exact_event_to_fence_mapping() -> None:
    assert _workflows_containing(" submit-bridge") == set(EXPECTED_PRODUCERS)
    for name, (events, group) in EXPECTED_PRODUCERS.items():
        _assert_workflow_mapping(name, events, group)


def test_durable_diana_requests_use_request_preserving_groups_and_host_fence() -> None:
    for name, (_, group) in EXPECTED_PRODUCERS.items():
        assert "github.sha" in group, name
        assert SHARED_FENCE not in group, name
    for operator in (
        "ops/universal_video_diana11_operator.sh",
        "ops/universal_video_diana11_002_operator.sh",
        "ops/universal_video_diana11_003_operator.sh",
    ):
        text = (ROOT / operator).read_text(encoding="utf-8")
        assert "/run/lock/oracle-workload-mutation.lock" in text


def test_legacy_diana_submit_holds_host_fence_before_state_check_and_enqueue() -> None:
    text = (ROOT / "ops/universal_video_diana11_operator.sh").read_text(encoding="utf-8")
    submit = text[text.index("submit_for(){") : text.index("publish_bridge(){")]
    acquire = submit.index("exec 9>/run/lock/oracle-workload-mutation.lock")
    lock = submit.index("flock -x 9", acquire)
    state = submit.index('current="$(state_for "$job_id"', lock)
    enqueue = submit.index('ln "$tmp" "$SPOOL/inbox/$job_file"', state)
    assert acquire < lock < state < enqueue


def test_remaining_host_mutators_acquire_atomic_host_fence() -> None:
    tls = _workflow_text("oracle-dds3-tls-renewal.yml")
    admin_workflow = _workflow_text("oracle-universal-video-admin.yml")
    admin_entry = (ROOT / "ops/universal_video_oci_admin_entrypoint.sh").read_text(encoding="utf-8")
    maintenance = (ROOT / "deploy/oracle-universal-video/universal-video-maintenance.service").read_text(encoding="utf-8")
    for text in (tls, admin_entry, maintenance):
        assert "/run/lock/oracle-workload-mutation.lock" in text
    assert "flock -x 9" in tls
    assert "ExecStart=/usr/bin/flock -x /run/lock/oracle-workload-mutation.lock" in tls
    assert "flock -x 9" in admin_entry
    assert "/usr/bin/flock -x" in maintenance
    assert "github.sha" in admin_workflow
    assert "oracle-instance-workload-mutation" not in admin_workflow.split("concurrency:", 1)[1].split("jobs:", 1)[0]

    for name in (
        "oracle-assistant-lab-control-rollout.yml",
        "oracle-assistant-lab-worker-rollout.yml",
        "oracle-ben-runtime-rollout.yml",
    ):
        rollout = _workflow_text(name)
        concurrency = rollout.split("concurrency:", 1)[1].split("jobs:", 1)[0]
        assert "github.sha" in concurrency, name
        assert "cancel-in-progress: false" in concurrency, name
        assert "/run/lock/oracle-workload-mutation.lock" in rollout, name


def test_stop_consumer_has_exact_non_cancelling_event_to_fence_mapping() -> None:
    _assert_workflow_mapping(
        "oracle-instance-power.yml",
        {"workflow_dispatch", "issue_comment"},
        POWER_GROUP,
    )


def test_research_job_canaries_have_exact_event_to_fence_mapping() -> None:
    assert _workflows_containing("research_runtime import enqueue") == set(
        EXPECTED_RESEARCH_PRODUCERS
    )
    for name, (events, group) in EXPECTED_RESEARCH_PRODUCERS.items():
        _assert_workflow_mapping(name, events, group)


def test_dds3_mass_launchers_have_exact_event_to_fence_mapping() -> None:
    expected = {
        "oracle-dds3-pilot10k-launch.yml": ({"pull_request_target"}, MASS_LAUNCH_GROUP),
        "oracle-dds3-pilot10k-operator.yml": ({"issue_comment"}, MASS_OPERATOR_GROUP),
    }
    assert _workflows_containing("systemctl start --no-block dds3-mass@") == set(expected)
    for name, (events, group) in expected.items():
        _assert_workflow_mapping(name, events, group)
        assert SHARED_FENCE in group


def test_guard_install_serializes_with_stop_and_gates_after_uploads() -> None:
    workflow = _workflow_text("oracle-idle-guard-exact-install.yml")
    _, concurrency = _workflow_contract("oracle-idle-guard-exact-install.yml")
    assert concurrency == {"group": SHARED_FENCE, "cancel-in-progress": False}
    third_upload = workflow.index("ops/install_oracle_idle_state_ocarun.sh")
    late_head = workflow.index("INSTALL_FINAL_HEAD_MOVED", third_upload)
    late_auth = workflow.index("INSTALL_FINAL_AUTHORIZATION_MISSING", late_head)
    remote_install = workflow.index("sudo -n env UPLOADED_INSTALLER=", late_auth)
    assert third_upload < late_head < late_auth < remote_install


def test_guard_install_executes_only_verified_root_owned_installer_copy() -> None:
    workflow = _workflow_text("oracle-idle-guard-exact-install.yml")
    copy = workflow.index("install -o root -g root -m 0700")
    regular = workflow.index("test -f", copy)
    mode = workflow.index("root:root:700", regular)
    digest = workflow.index("INSTALLER_SHA256", mode)
    syntax = workflow.index("bash -n", digest)
    execute = workflow.index("; \\\"\\$trusted\\\"'", syntax)
    assert copy < regular < mode < digest < syntax < execute
    assert "sudo -n env SOURCE_FILE=" not in workflow
    assert "ADMIN_SHA256=" in workflow
    assert "INSTALLED_ADMIN_SHA256=" in workflow
    installer = (ROOT / "ops" / "install_oracle_idle_state_ocarun.sh").read_text()
    assert 'atomic_copy_executable_verified "$trusted_admin" "$ADMIN_SHA256" "$ADMIN_TARGET"' in installer
    assert "set -Eeuo pipefail; sudo -n env UPLOADED_INSTALLER=" in workflow
    assert 'restore_previous_admin' in installer
    assert "PRE_GUARD_SHA256=" in workflow
    assert "PRE_ADMIN_SHA256=" in workflow
    assert "POST_AUTOPILOT_ACTIVE=" in workflow
    assert "POST_AUTOPILOT_ENABLED=" in workflow


def test_install_proof_captures_are_exclusive_root_only_regular_files() -> None:
    installer = (ROOT / "ops" / "install_oracle_idle_state_ocarun.sh").read_text(encoding="utf-8")
    assert 'mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-install-proof.' in installer
    assert 'mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-authorizer.stderr.' in installer
    assert '[[ -f "$capture" && ! -L "$capture" ]]' in installer
    assert "root:root:600" in installer
    assert "'/tmp/oracle-idle-state-install-proof.txt'" not in installer


def test_oracle_v2_operator_mutations_share_stop_fence() -> None:
    _assert_workflow_mapping(
        "oracle-operator-v2.yml",
        {"pull_request", "issue_comment"},
        ORACLE_V2_GROUP,
    )
    assert SHARED_FENCE in ORACLE_V2_GROUP
    assert "oracle-operator-v2-diagnose-{0}" in ORACLE_V2_GROUP
    mutation_commands = ORACLE_V2_GROUP.split("contains(fromJSON(", 1)[1]
    assert "/oracle-v2 diagnose-ben" not in mutation_commands


def test_installer_executes_verified_root_owned_authorizer_copy() -> None:
    workflow = (ROOT / "ops" / "install_oracle_idle_state_ocarun.sh").read_text(encoding="utf-8")
    copy = workflow.index("trusted_authorizer=")
    verify = workflow.index("trusted authorizer digest mismatch", copy)
    mode = workflow.index("trusted authorizer ownership/mode invalid", verify)
    execute = workflow.index('python3 "$trusted_authorizer" --proof', mode)
    assert copy < verify < mode < execute
    assert 'python3 "$AUTHORIZER_FILE" --proof' not in workflow


def test_instance_power_preserves_confirmed_busy_state() -> None:
    workflow = _workflow_text("oracle-instance-power.yml")
    busy = workflow.index("state_busy_forbids_stop")
    no = workflow.index("stop_authorized=NO", busy)
    mapped = workflow.index("idle_state=BUSY", no)
    assert busy < no < mapped


def test_maintenance_handoff_remains_classifier_visible_until_reacquire() -> None:
    classifier = (ROOT / "ops/oracle_idle_state.sh").read_text(encoding="utf-8")
    productionize = (ROOT / "ops/oracle_universal_video_productionize.sh").read_text(encoding="utf-8")
    marker = "/run/bridge-school/universal-video-maintenance-handoff"
    assert marker in classifier
    assert "maintenance_fence_handoff_in_progress" in classifier
    assert marker in productionize
    assert 'flock -n -x 9' in productionize
    assert 'if [[ "${ORACLE_WORKLOAD_FENCE_HELD:-0}" != 1 ]]' in productionize
    create = productionize.index('install -m 0600 -o root -g root /dev/null "$MAINT_HANDOFF_FILE"')
    release = productionize.index("flock -u 9", create)
    reacquire = productionize.index("flock -x 9", release)
    remove = productionize.index('rm -f "$MAINT_HANDOFF_FILE"', reacquire)
    assert create < release < reacquire < remove
