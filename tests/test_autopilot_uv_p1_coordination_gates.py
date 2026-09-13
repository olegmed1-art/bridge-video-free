import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _load_registration_runtime(monkeypatch: pytest.MonkeyPatch):
    values = {
        "PROJECT_ID": "misty-poetry-18012774",
        "TEMP_BRANCH_ID": "br-still-tooth-b1ilkfcj",
        "PRODUCTION_BRANCH_ID": "br-wispy-lab-b1rq54of",
        "TEMP_ENDPOINT_ID": "ep-floral-field-b1pjs2of",
        "LIVE_RUNTIME_HEAD_SHA": "c1515c5af4a47c7468d7c4769e91082f7afd163c",
        "LIVE_CANARY_HEAD_SHA": "79aec3f732fdcd8ca9f5f8a4a6ba5a88f4bba8d4",
        "LIVE_IDLE_HEAD_SHA": "8ab8d74c2a0ffd281ae4ccea9e5c8e55eea2ab45",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    path = ROOT / "ops/autopilot/register_uv_p1_runtime.py"
    spec = importlib.util.spec_from_file_location("register_uv_p1_runtime_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registration_reconciles_all_live_heads_before_database_connection_and_each_mutation() -> None:
    runtime = _read("ops/autopilot/register_uv_p1_runtime.py")
    workflow = _read(".github/workflows/autopilot-uv-p1-register-v3.yml")

    assert runtime.index("_verify_initial_live_heads()") < runtime.index("connection = _connect_runtime()")
    for name in ("LIVE_RUNTIME_HEAD_SHA", "LIVE_CANARY_HEAD_SHA", "LIVE_IDLE_HEAD_SHA"):
        assert name in runtime
        assert name in workflow
    registration_loop = runtime.split("for label, task_key, pr_number, expected_head in APPROVED:", 1)[1]
    mutation = registration_loop.index("task_id, status, created = _register(connection, task_key)")
    assert registration_loop.rindex("_verify_current_live_head", 0, mutation) < mutation
    assert registration_loop.count("_register(connection, task_key)") == 1
    assert "urllib.request.urlopen(request, timeout=10)" in runtime
    assert "REGISTRATION_RETRY_SECONDS: Final[int] = 60" in runtime
    assert "observed_task_id, status = _observe(connection, task_key)" in runtime
    assert "FROM autopilot.task_status WHERE task_key=%s" in runtime
    assert "runtime:997 canary:1062 idle:1061" in workflow
    assert "1000" not in runtime
    assert workflow.index("Resolve live PR heads immediately before registration") < workflow.index(
        "Register and observe through runtime-only ingress"
    )
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "allow_temporary_registration == 'YES'" in workflow


def test_registration_rechecks_every_approved_head_immediately_before_aggregate_pass() -> None:
    runtime = _read("ops/autopilot/register_uv_p1_runtime.py")

    helper = runtime[
        runtime.index("def _verify_all_current_live_heads()") : runtime.index("def main()")
    ]
    assert "for label, _task_key, pr_number, expected_head in APPROVED:" in helper
    assert "_verify_current_live_head(label, pr_number, expected_head)" in helper
    assert "AUTOPILOT_UV_P1_FINAL_ALL_HEADS_VERIFIED=PASS" in helper

    final_recheck = runtime.index("_verify_all_current_live_heads()", runtime.index("def main()"))
    receipt = runtime.index('"gate": "AUTOPILOT_UV_P1_DURABLE_REGISTRATION"', final_recheck)
    output = runtime.index('print("UV_P1_REGISTRATION_RECEIPT="', receipt)
    assert final_recheck < receipt < output


def test_registration_live_resolver_fails_closed_on_head_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_registration_runtime(monkeypatch)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "state": "open",
                    "draft": True,
                    "head": {"sha": "bd6dbea5a935ed8f5410ce8eb328c36829667235"},
                }
            ).encode()

    monkeypatch.setattr(runtime.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="AUTOPILOT_UV_P1_RUNTIME_APPROVAL_STALE"):
        runtime._verify_current_live_head(
            "RUNTIME",
            997,
            "c1515c5af4a47c7468d7c4769e91082f7afd163c",
        )


@pytest.mark.parametrize("port", ["not-a-port", "65536"])
def test_registration_dsn_discovery_skips_invalid_ports(
    monkeypatch: pytest.MonkeyPatch,
    port: str,
) -> None:
    runtime = _load_registration_runtime(monkeypatch)

    assert (
        runtime._temporary_dsn(
            f"postgresql://runtime:secret@ep-example.neon.tech:{port}/neondb"
        )
        is None
    )


def test_registration_dsn_identity_distinguishes_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_registration_runtime(monkeypatch)

    stale = runtime._temporary_dsn(
        "postgresql://runtime:secret@ep-example.neon.tech:5433/neondb"
    )
    live = runtime._temporary_dsn(
        "postgresql://runtime:secret@ep-example.neon.tech:5432/neondb"
    )

    assert stale is not None and live is not None
    assert stale[0] != live[0]
    assert ":5433/neondb" in stale[0]
    assert ":5432/neondb" in live[0]


def test_registration_dsn_identity_distinguishes_connection_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_registration_runtime(monkeypatch)

    stale = runtime._temporary_dsn(
        "postgresql://runtime:secret@ep-example.neon.tech:5432/neondb?sslmode=disable"
    )
    live = runtime._temporary_dsn(
        "postgresql://runtime:secret@ep-example.neon.tech:5432/neondb?sslmode=require"
    )

    assert stale is not None and live is not None
    assert stale[0] != live[0]
    assert stale[0] == stale[1]
    assert live[0] == live[1]


def test_registration_preserves_history_and_applies_only_0315_before_ingress() -> None:
    historical_path = ROOT / "database/migrations/0309_autopilot_uv_p1_bounded_ingress.sql"
    historical = historical_path.read_bytes()
    predecessor_path = ROOT / "database/migrations/0313_autopilot_uv_p1_allowlist_upgrade.sql"
    predecessor = predecessor_path.read_bytes()
    installed_path = ROOT / "database/migrations/0314_autopilot_uv_p1_allowlist_upgrade.sql"
    installed_bytes = installed_path.read_bytes()
    installed = installed_bytes.decode()
    upgrade_path = ROOT / "database/migrations/0315_autopilot_uv_p1_allowlist_upgrade.sql"
    upgrade_bytes = upgrade_path.read_bytes()
    upgrade = upgrade_bytes.decode()
    workflow = _read(".github/workflows/autopilot-uv-p1-register-v3.yml")
    chain_invariants = _read("database/tests/308_autopilot_uv_p1_bounded_ingress.sql")

    assert hashlib.sha256(historical).hexdigest() == (
        "14db4783f63375e79f8340be4c6f26ff27211eb0f920deec8455777098343422"
    )
    assert "uv-p1-intake-pr1000-5af0675a-20260901" in historical.decode()
    assert hashlib.sha256(predecessor).hexdigest() == (
        "e6184520c9df8d3ab8565fc80eb81c604b79a056e8ced4d1ec5b7246c9ccfd39"
    )
    assert hashlib.sha256(installed_bytes).hexdigest() == (
        "69be0dd729f9056d36478ee3fcc16326cfc631ae9f6cdbad85f8c8e7f9c1f2d1"
    )
    installed_blob = hashlib.sha1(
        f"blob {len(installed_bytes)}\0".encode() + installed_bytes,
        usedforsecurity=False,
    ).hexdigest()
    assert installed_blob == "fa2598af4866dd070a6e6623bd670d772cb35028"
    assert "uv-p1-canary-pr1062-8aa4f80b8d20-20260902" in installed
    assert "uv-p1-canary-pr1062-79aec3f732fd-20260902" not in installed
    assert "uv-p1-canary-pr1062-8aa4f80b8d20-20260902" not in upgrade
    assert hashlib.sha256(upgrade_bytes).hexdigest() == (
        "063f07eeb69f1f1cb3e7d2910362f10ae36e211d2ff3b808be648a71815e78d5"
    )
    git_blob = hashlib.sha1(
        f"blob {len(upgrade_bytes)}\0".encode() + upgrade_bytes,
        usedforsecurity=False,
    ).hexdigest()
    assert git_blob == "f6cfb7b87b6d3d16fec001786355c5cea7c9b03a"
    for value in (
        "0315_autopilot_uv_p1_allowlist_upgrade",
        "uv-p1-runtime-pr997-c1515c5af4a4-20260902",
        "uv-p1-canary-pr1062-79aec3f732fd-20260902",
        "uv-p1-idle-pr1061-8ab8d74c2a0f-20260902",
    ):
        assert value in upgrade
        assert value in workflow
    assert "0313_autopilot_uv_p1_allowlist_upgrade" in upgrade
    assert "AUTOPILOT_UV_P1_0313_REQUIRED" in upgrade
    assert "0314_autopilot_uv_p1_allowlist_upgrade" in upgrade
    assert "AUTOPILOT_UV_P1_0314_REQUIRED_OR_CHECKSUM_INVALID" in upgrade
    assert "EXPECTED_0313_BLOB: fa86391dd960ec19752099c83147faf8a9d3c5ae" in workflow
    assert "EXPECTED_0313_SHA256: e6184520c9df8d3ab8565fc80eb81c604b79a056e8ced4d1ec5b7246c9ccfd39" in workflow
    assert 'git hash-object database/migrations/0313_autopilot_uv_p1_allowlist_upgrade.sql' in workflow
    assert '[[ "$migration_0313" == t && "$checksum_0313" == "$EXPECTED_0313_SHA256" ]]' in workflow
    assert "EXPECTED_0314_BLOB: fa2598af4866dd070a6e6623bd670d772cb35028" in workflow
    assert "EXPECTED_0314_SHA256: 69be0dd729f9056d36478ee3fcc16326cfc631ae9f6cdbad85f8c8e7f9c1f2d1" in workflow
    assert "EXPECTED_0315_BLOB: f6cfb7b87b6d3d16fec001786355c5cea7c9b03a" in workflow
    assert "EXPECTED_0315_SHA256: 063f07eeb69f1f1cb3e7d2910362f10ae36e211d2ff3b808be648a71815e78d5" in workflow
    assert "uv-p1-intake-pr1000-5af0675a-20260901" not in upgrade
    assert "uv-p1-canary-pr1062-79aec3f732fd-20260902" in chain_invariants
    assert "uv-p1-canary-pr1062-8aa4f80b8d20-20260902" not in chain_invariants
    apply_step = workflow.index("Apply and verify only forward migration 0315")
    register_step = workflow.index("Register and observe through runtime-only ingress")
    assert apply_step < register_step
    apply_body = workflow[apply_step:register_step]
    assert "-f database/migrations/0315_autopilot_uv_p1_allowlist_upgrade.sql" in apply_body
    assert "-f database/migrations/0314_autopilot_uv_p1_allowlist_upgrade.sql" not in apply_body
    assert "database/scripts/migrate.sh" not in apply_body
    assert "current_setting('neon.branch_id', true)" in apply_body
    assert '[[ "$migration_0314" == t && "$checksum_0314" == "$EXPECTED_0314_SHA256" ]]' in apply_body
    assert 'if [[ "$migration_0315" == t ]]; then' in apply_body
    assert '[[ "$checksum_0315" == "$EXPECTED_0315_SHA256" ]]' in apply_body
    assert '[[ -z "$checksum_0315" ]]' in apply_body
    assert '[[ "$post" == "t|$EXPECTED_0314_SHA256|t|$EXPECTED_0315_SHA256|t|t|t|t|t|t|t|t|t|f" ]]' in apply_body
    assert "WHERE migration_key = '0314_autopilot_uv_p1_allowlist_upgrade'" not in apply_body
    preflight = apply_body.index('pre="$(psql')
    head_recheck = apply_body.index("revalidate_pr_heads", preflight)
    migration_write = apply_body.index(
        '-f database/migrations/0315_autopilot_uv_p1_allowlist_upgrade.sql',
        head_recheck,
    )
    ledger_write = apply_body.index("UPDATE public.schema_migration", migration_write)
    assert preflight < head_recheck < migration_write < ledger_write
    assert apply_body.count("UPDATE public.schema_migration") == 1
    assert "AUTOPILOT_UV_P1_PRE_MIGRATION_HEADS_VERIFIED=PASS" in apply_body
    for binding in (
        '"runtime:997:$APPROVED_RUNTIME_HEAD:$EXPECTED_RUNTIME_HEAD"',
        '"canary:1062:$APPROVED_CANARY_HEAD:$EXPECTED_CANARY_HEAD"',
        '"idle:1061:$APPROVED_IDLE_HEAD:$EXPECTED_IDLE_HEAD"',
    ):
        assert binding in apply_body
    for approved in (
        "APPROVED_RUNTIME_HEAD: c1515c5af4a47c7468d7c4769e91082f7afd163c",
        "APPROVED_CANARY_HEAD: 79aec3f732fdcd8ca9f5f8a4a6ba5a88f4bba8d4",
        "APPROVED_IDLE_HEAD: 8ab8d74c2a0ffd281ae4ccea9e5c8e55eea2ab45",
    ):
        assert approved in workflow
    assert "AUTOPILOT_UV_P1_MIGRATION_APPROVAL_STALE" in workflow
    assert "AUTOPILOT_UV_P1_CACHED_APPROVAL_MISMATCH" in apply_body
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "MIGRATION_APPLIED_BY_THIS_RUN" in workflow


def test_monitor_rejects_missing_or_malformed_durable_task_ids() -> None:
    workflow = _read(".github/workflows/autopilot-uv-p1-oracle-monitor.yml")

    assert "runtime:997 canary:1062 idle:1061" in workflow
    assert "PR #1000" not in workflow
    assert "UV_AUTOPILOT_DURABLE_TASKS_MISSING={missing}" in workflow
    assert "raise SystemExit(76)" in workflow
    assert "[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}" in workflow
    validation = workflow.split("UV_AUTOPILOT_LIVE_BRANCH_VERIFIED=PASS", 1)[1]
    assert 'if [[ "$task_id" != NOT_FOUND ]]' not in validation


def test_monitor_rechecks_all_pr_heads_after_polling_before_receipt() -> None:
    workflow = _read(".github/workflows/autopilot-uv-p1-oracle-monitor.yml")

    polling = workflow.index("Query acting Autopilot task store through its live process")
    recheck = workflow.index("Re-resolve exact PR heads after task polling")
    publish = workflow.index("Publish exact task receipt to issue 946")
    assert polling < recheck < publish
    assert 'runtime:997:"$EXPECTED_RUNTIME_SHA"' in workflow
    assert 'canary:1062:"$EXPECTED_CANARY_SHA"' in workflow
    assert 'idle:1061:"$EXPECTED_IDLE_SHA"' in workflow
    assert "UV_AUTOPILOT_POST_POLL_HEAD_DRIFT" in workflow
    assert "UV_AUTOPILOT_POST_POLL_HEADS_VERIFIED=PASS" in workflow
    assert '[[ "$live" =~ ^[0-9a-f]{40}$ && "$live" == "$expected" ]]' in workflow


def test_monitor_selects_exact_shadow_worker_and_allows_only_observer_peer() -> None:
    workflow = _read(".github/workflows/autopilot-uv-p1-oracle-monitor.yml")

    assert "worker=school-autopilot-shadow.service" in workflow
    assert "observer=school-autopilot-online-observer.service" in workflow
    assert "canary=school-autopilot-production-canary.service" in workflow
    assert 'MainPID --value "$worker"' in workflow
    assert "UV_AUTOPILOT_PRODUCTION_CANARY_DISABLED=YES" in workflow
    assert "UV_AUTOPILOT_ACTIVE_UNIT_ALLOWLIST=PASS" in workflow
    assert "UV_AUTOPILOT_LIVE_RUNTIME_MODE=SHADOW" in workflow
    assert "modes != ['SHADOW']" in workflow
    assert '[[ "$active_unit" == "$worker" || "$active_unit" == "$observer" ]]' in workflow
    assert "systemctl list-units --all" not in workflow
    assert "UV_AUTOPILOT_UNIT_COUNT" not in workflow


def test_monitor_uses_runtime_psycopg_and_only_the_granted_status_view() -> None:
    workflow = _read(".github/workflows/autopilot-uv-p1-oracle-monitor.yml")

    assert "runtime_python=/opt/bridge-school/school-autopilot/.venv/bin/python" in workflow
    assert "import psycopg" in workflow
    assert "default_transaction_read_only=on" in workflow
    assert "LEFT JOIN autopilot.task_status" in workflow
    assert "FROM autopilot.task " not in workflow
    assert "autopilot.task_event" not in workflow
    assert "autopilot.evidence" not in workflow
    assert "command -v psql" not in workflow
    assert 'EXPECTED_PROJECT_ID: misty-poetry-18012774' in workflow
    assert "safe_reason = re.sub" in workflow


def test_runtime_gate_paginates_and_trusts_only_designated_reviewers() -> None:
    gate = _read("ops/verify_uv_runtime_pr_gate.sh")

    assert "pageInfo{hasNextPage endCursor}" in gate
    assert "gh api --paginate --slurp" in gate
    assert '== "chatgpt-codex-connector[bot]"' in gate
    assert '"vercel[bot]"' not in gate
    assert "total == reported_total" in gate
    assert "check_runs=%s/%s" in gate
    assert 'state == "CHANGES_REQUESTED"' in gate
    assert "trusted > 0 && changes_requested == 0 && unresolved == 0" in gate


def test_runtime_image_build_rechecks_complete_gate_after_clone_and_before_build() -> None:
    workflow = _read(".github/workflows/autopilot-uv-runtime-exact-image-gate.yml")

    prepare = workflow.index("Prepare isolated source on Oracle")
    recheck = workflow.index("Revalidate complete live gate after remote source preparation")
    build = workflow.index("Build non-activated image with import preflight")
    cleanup = workflow.index("Remove isolated Oracle build source")
    assert prepare < recheck < build < cleanup
    preparation_body = workflow[prepare:recheck]
    recheck_body = workflow[recheck:build]
    build_body = workflow[build:cleanup]
    assert 'git clone --quiet --no-tags --filter=blob:none "$REPO_URL" "$STAGE/source"' in preparation_body
    assert 'git -C "$STAGE/source" fetch --quiet --no-tags origin "$EXPECTED_COMMIT"' in preparation_body
    assert "UV_RUNTIME_REMOTE_SOURCE_PREPARED" in preparation_body
    assert "UV_GATE_PR_NUMBER=997 bash ops/verify_uv_runtime_pr_gate.sh" in recheck_body
    assert 'grep -Fx "head=$EXPECTED_HEAD_SHA"' in recheck_body
    assert "grep -Fx 'ci=PASS'" in recheck_body
    assert "grep -Fx 'review=PASS'" in recheck_body
    assert "grep -Fx 'unresolved=0'" in recheck_body
    assert "grep -Fx 'changes_requested=0'" in recheck_body
    assert "git clone" not in build_body
    assert 'bash "$BUILD_SCRIPT"' in build_body
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "allow_oracle_image_build == 'YES'" in workflow


def test_idle_resource_probe_accepts_only_pgrep_match_or_no_match() -> None:
    workflow = _read(".github/workflows/autopilot-uv-runtime-exact-image-gate.yml")

    resources = workflow.split("Check Oracle resource availability and active-work exclusions", 1)[1]
    resources = resources.split("Revalidate live gates before remote source preparation", 1)[0]
    assert "set +e" in resources
    assert "heavy_probe_rc=$?" in resources
    assert "heavy_probe_rc == 0 || heavy_probe_rc == 1" in resources
    assert "UV_RESOURCE_PROCESS_PROBE_FAILED=$heavy_probe_rc" in resources
    assert "if (( heavy_probe_rc == 1 )); then" in resources
    assert "heavy=0" in resources


def test_image_preflight_cannot_control_services_or_replace_resident_source() -> None:
    workflow = _read(".github/workflows/autopilot-uv-runtime-exact-image-gate.yml")
    preflight = _read("ops/oracle_universal_video_container_build_preflight.sh")

    assert "oracle_universal_video_run_command.sh" not in workflow
    assert "/opt/bridge-school/universal-video-src" not in workflow
    assert "UNIVERSAL_VIDEO_BUILD_SOURCE_DIR" in preflight
    for forbidden in ("systemctl", "service ", "universal-video.service", "daemon-reload"):
        assert forbidden not in preflight
