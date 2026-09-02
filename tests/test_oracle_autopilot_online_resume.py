from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0312_autopilot_deployment_resume.sql"
ROLLBACK = ROOT / "database/rollbacks/0312_autopilot_deployment_resume.sql"
SQL_TEST = ROOT / "database/tests/312_autopilot_deployment_resume.sql"
WORKFLOW = ROOT / ".github/workflows/oracle-autopilot-online-resume.yml"

DEPLOYED_REVISION = "9064044d4c5b85803c6778060dff4843111ab888"
RESUME_KEY = "online-stale-running-deployment-resume-20260902-v1"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_resume_migration_is_exact_one_shot_and_fail_closed():
    migration = read(MIGRATION)
    assert "CREATE TABLE autopilot.online_resume_receipt" in migration
    assert "autopilot.resume_online_pilot_after_deployment" in migration
    assert "CREATE OR REPLACE FUNCTION autopilot.online_resume_status()" in migration
    assert DEPLOYED_REVISION in migration
    assert RESUME_KEY in migration
    assert "staging_run_id = 33586404964" in migration
    assert "activation_run_id = 33586501046" in migration
    assert "AUTOPILOT_DEPLOYMENT_RESUME_REQUEST_NOT_APPROVED" in migration
    assert "AUTOPILOT_DEPLOYMENT_RESUME_QUEUE_NOT_IDLE" in migration
    assert "AUTOPILOT_DEPLOYMENT_RESUME_CANARY_INVALID" in migration
    assert "state_row.created_count <> state_row.pass_count + 1" in migration
    assert "state_row.finding_count <> 1" in migration
    assert "deployment_canary.acceptance_contract_json->>'deployed_revision'" in migration
    assert "deployment_canary.acceptance_contract_json->'activation_run_id'" in migration
    assert "retained_count <> 1" in migration
    assert "step_count <> 1" in migration
    assert "event_count <> 3" in migration
    assert "SET circuit_open = false" in migration
    assert "circuit_reason_code = NULL" in migration
    assert "last_task_id = NULL" in migration
    assert "last_task_key = NULL" in migration
    assert "last_created_at = NULL" in migration
    assert "'resume_scope', 'SHADOW_ONLY'" in migration
    assert "'production_mutation', false" in migration
    assert "'oracle_instance_stop', false" in migration
    assert "GRANT EXECUTE ON FUNCTION autopilot.online_resume_status()" in migration
    assert "GRANT EXECUTE ON FUNCTION autopilot.resume_online_pilot_after_deployment" not in migration
    assert "FROM autopilot_runtime;" in migration


def test_resume_sql_regression_proves_first_pass_and_reopen_boundary():
    sql_test = read(SQL_TEST)
    assert "AUTOPILOT_DEPLOYMENT_RESUME_TRANSITION_INVALID" in sql_test
    assert "AUTOPILOT_DEPLOYMENT_RESUME_FIRST_TICK_INVALID" in sql_test
    assert "AUTOPILOT_DEPLOYMENT_RESUME_PASS_TICK_INVALID" in sql_test
    assert "AUTOPILOT_DEPLOYMENT_RESUME_REPLAY_RECLOSED_CIRCUIT" in sql_test
    assert "first_online_tick.action <> 'CREATED'" in sql_test
    assert "second_online_tick.action <> 'INTERVAL_HOLD'" in sql_test
    assert "resume_status.nonzero_cost_task_count <> 0" in sql_test
    assert "resume_status.unsafe_terminal_task_count <> 0" in sql_test
    assert "ROLLBACK;" in sql_test


def test_resume_workflow_archives_marker_without_lifecycle_actions():
    workflow = read(WORKFLOW)
    assert "ops/oracle-autopilot-online-resume-requests/*.json" in workflow
    assert "group: oracle-instance-workload-mutation" in workflow
    assert "request['action'] == 'resume_online_pilot'" in workflow
    assert "request['activation_scope'] == 'SHADOW_ONLY'" in workflow
    assert "request['expected_circuit_code'] == 'ONLINE_STALE_RUNNING'" in workflow
    assert "request['no_instance_stop'] is True" in workflow
    assert "request['no_worker_restart'] is True" in workflow
    assert "request['no_observer_restart'] is True" in workflow
    assert "request['production_mutation'] is False" in workflow
    assert DEPLOYED_REVISION in workflow
    assert "br-still-tooth-b1ilkfcj" in workflow
    assert "br-wispy-lab-b1rq54of" in workflow
    assert "autopilot.online_resume_status()" in workflow
    assert "/proc/{os.environ['OBSERVER_PID']}/environ" in workflow
    assert "autopilot_runtime_login" in workflow
    assert "os.environ['AUTOPILOT_DATABASE_URL']" not in workflow
    assert '. "$env_file"' not in workflow
    assert 'mv -- "$marker" "$archive"' in workflow
    assert 'mv -- "$archive" "$marker"' in workflow
    assert "trap restore_marker EXIT" in workflow
    assert "AUTOPILOT_RESUME_FIRST_PASS_TIMEOUT" in workflow
    assert "row['active_task_count'] <= 1" in workflow
    assert "row['nonzero_cost_task_count'] == 0" in workflow
    assert "row['unsafe_terminal_task_count'] == 0" in workflow
    assert "AUTOPILOT_WORKER_RESTARTED=NO" in workflow
    assert "AUTOPILOT_OBSERVER_RESTARTED=NO" in workflow
    assert "ORACLE_INSTANCE_STOP_REQUESTED=NO" in workflow
    for forbidden in (
        "systemctl stop",
        "systemctl restart",
        "systemctl start",
        "systemctl disable",
        "systemctl enable --now",
        "oci compute instance action",
        "--action STOP",
        'rm -f "$marker"',
    ):
        assert forbidden not in workflow


def test_resume_rollback_reopens_circuit_before_removing_receipt():
    rollback = read(ROLLBACK)
    open_position = rollback.index("autopilot.open_online_pilot_circuit")
    drop_position = rollback.index("DROP TABLE IF EXISTS autopilot.online_resume_receipt")
    assert open_position < drop_position
    assert "ONLINE_RESUME_ROLLBACK" in rollback
    assert "0312_autopilot_deployment_resume" in rollback
