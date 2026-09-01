from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-autopilot-production-canary.yml"
UNIT = ROOT / "deploy/oracle-autopilot/school-autopilot-production-canary.service"
INSTALLER = ROOT / "ops/oracle_autopilot_production_canary_install.sh"


def test_workflow_is_request_gated_and_exactly_targets_production() -> None:
    workflow = WORKFLOW.read_text()

    assert "branches: [codex/oracle-autopilot-lite-shadow]" in workflow
    assert "ops/oracle-autopilot-production-canary-requests/*.json" in workflow
    assert "environment: database-production" in workflow
    assert "EXPECTED_PROJECT_ID: misty-poetry-18012774" in workflow
    assert "EXPECTED_BRANCH_ID: br-wispy-lab-b1rq54of" in workflow
    assert "EXPECTED_MIGRATION_KEY: 0300_autopilot_oracle_shadow" in workflow
    assert "run_zero_cost_production_canary" in workflow
    assert "AUTOPILOT_SMOKE_V1" in workflow
    assert "cost_cap_microusd = 0" in workflow
    assert "git archive \"$SOURCE_REVISION\"" in workflow
    assert "autopilot_phase3b" in workflow
    assert 'ssh-keygen -e -m PKCS8 -f "$rsa_openssh"' in workflow
    assert 'openssl pkey -pubin -in "$rsa_public" -noout' in workflow
    assert "task_production_mutation" in workflow
    assert "request[\"task_production_mutation\"] is False" in workflow
    assert "-v task_key=\"$TASK_KEY\" <<'PSQL'" in workflow
    assert "-v task_id=\"$task_id\" <<'PSQL'" in workflow
    assert "create_shadow_task(:'task_key'" not in workflow


def test_failure_path_applies_both_kill_switches() -> None:
    workflow = WORKFLOW.read_text()

    assert "failure() || cancelled()" in workflow
    assert (
        "systemctl disable --now school-autopilot-production-canary.service"
        in workflow
    )
    assert "ALTER ROLE autopilot_prod_canary_login NOLOGIN" in workflow
    assert (
        "ALTER ROLE autopilot_prod_canary_login WITH LOGIN PASSWORD %L CONNECTION LIMIT 2"
        in workflow
    )
    assert "CONNECTION LIMIT 3" not in workflow
    assert "WITH LOGIN PASSWORD %L INHERIT NOSUPERUSER" not in workflow
    assert "ORACLE_INSTANCE_STOP_REQUESTED=NO" in workflow

    forbidden = (
        "oci compute instance action",
        "--action STOP",
        "systemctl disable --now school-autopilot-shadow.service",
        "database/scripts/migrate.sh",
    )
    for text in forbidden:
        assert text not in workflow


def test_unit_is_isolated_and_runs_only_the_bounded_worker() -> None:
    unit = UNIT.read_text()

    assert "User=school-autopilot-canary" in unit
    assert "AUTOPILOT_RUNTIME_MODE=SHADOW" in unit
    assert "python -m oracle_autopilot.worker" in unit
    assert "worker_v17" not in unit
    assert "/school-autopilot-production-canary/" in unit
    assert "ProtectProc=invisible" in unit
    assert "CapabilityBoundingSet=" in unit


def test_installer_pins_target_identity_and_separate_service() -> None:
    installer = INSTALLER.read_text()

    assert "EXPECTED_PROJECT_ID=misty-poetry-18012774" in installer
    assert "EXPECTED_BRANCH_ID=br-wispy-lab-b1rq54of" in installer
    assert "EXPECTED_DB_USER=autopilot_prod_canary_login" in installer
    assert "AUTOPILOT_ACTIVATION_SCOPE=PRODUCTION_CANARY_ZERO_COST" in installer
    assert "school-autopilot-production-canary.service" in installer
    assert "school-autopilot-shadow.service" not in installer
    assert "bounded policy module is missing" in installer
    assert '"$RELEASE_DIR/autopilot_phase3b"' in installer
    assert "has_table_privilege(current_user, 'autopilot.task', 'SELECT')" in installer
    assert "not task_select and not task_insert and not can_create" in installer
    assert "non_autopilot_write_access == 0" in installer
    assert "unexpected_non_autopilot_select_access == 0" in installer
    assert "allowed_postgres_telemetry_views == 2" in installer
    assert "pg_stat_statements_info" in installer
