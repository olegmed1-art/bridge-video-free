from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER = ROOT / "ops" / "oracle_idle_state.sh"
AUTHORIZER = ROOT / "ops" / "oracle_idle_stop_guard.py"
SCHEMA = ROOT / "assistant_lab" / "oracle_idle_schema.sql"
FINALIZER = ROOT / ".github" / "workflows" / "oracle-autopilot-staging-finalize.yml"
INSTANCE_POWER = ROOT / ".github" / "workflows" / "oracle-instance-power.yml"
CANONICAL_IDLE_REASON = (
    "jobs=0,research=0,research_children=0,control=0,"
    "operator_lease=0,autopilot=0,video=0"
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


class ClassifierHarness:
    def __init__(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        self.set_mass_service_states()
        _write_executable(self.bindir / "pgrep", "#!/bin/sh\nexit 1\n")

        self.fake_python = self.root / "python"
        _write_executable(
            self.fake_python,
            (
                "#!/bin/sh\n"
                "printf '%s\\n' \"$ORACLE_IDLE_TEST_RESULT\"\n"
                "if [ -n \"${ORACLE_IDLE_TEST_STDERR:-}\" ]; then "
                "printf '%s\\n' \"$ORACLE_IDLE_TEST_STDERR\" >&2; fi\n"
                "exit \"${ORACLE_IDLE_TEST_EXIT_CODE:-0}\"\n"
            ),
        )
        self.env_file = self.root / "assistant-lab.env"
        self.env_file.write_text(
            "ASSISTANT_LAB_DATABASE_URL=postgres://assistant-lab\n",
            encoding="utf-8",
        )
        self.autopilot_env_file = self.root / "autopilot-shadow.env"
        self.autopilot_env_file.write_text(
            "AUTOPILOT_DATABASE_URL=postgres://autopilot\n",
            encoding="utf-8",
        )
        self.queue_file = self.root / "video-queue-dsn"
        self.queue_file.write_text("postgres://video\n", encoding="utf-8")
        self.lease_file = self.root / "oracle-host-lease"
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.video_inbox = self.root / "universal-video" / "spool" / "inbox"
        self.video_running = (
            self.root / "universal-video" / "spool" / "running"
        )
        self.observer_pending = (
            self.root / "assistant-lab-observer" / "jobs" / "pending"
        )
        self.observer_running = (
            self.root / "assistant-lab-observer" / "jobs" / "running"
        )
        for path in (
            self.video_inbox,
            self.video_running,
            self.observer_pending,
            self.observer_running,
        ):
            path.mkdir(parents=True)

    def set_mass_service_states(
        self,
        *,
        pilot: tuple[str, int] = ("inactive", 3),
        main: tuple[str, int] = ("inactive", 3),
    ) -> None:
        script = f"""#!/bin/sh
case "$*" in
  "is-active assistant-lab.service") echo active; exit 0 ;;
  "is-active dds3-mass@10000.service") echo {pilot[0]}; exit {pilot[1]} ;;
  "is-active dds3-mass@30000.service") echo {main[0]}; exit {main[1]} ;;
  *) echo unknown; exit 4 ;;
esac
"""
        _write_executable(self.bindir / "systemctl", script)

    def close(self) -> None:
        self._temp.cleanup()

    def run(
        self,
        *,
        result: str = (
            "IDLE:jobs=0,research=0,research_children=0,"
            "control=0,operator_lease=0,autopilot=0,video=0"
        ),
        spool: Path | str | None = None,
        observer_busy: bool = False,
        video_dsn_present: bool = True,
        env_present: bool = True,
        autopilot_env_present: bool = True,
        lease_text: str | None = None,
        python_exit_code: int = 0,
        python_stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        _write_executable(
            self.bindir / "pgrep",
            "#!/bin/sh\nexit %d\n" % (0 if observer_busy else 1),
        )
        if video_dsn_present:
            self.queue_file.write_text("postgres://video\n", encoding="utf-8")
        else:
            self.queue_file.unlink(missing_ok=True)
        if env_present:
            self.env_file.write_text(
                "ASSISTANT_LAB_DATABASE_URL=postgres://assistant-lab\n",
                encoding="utf-8",
            )
        else:
            self.env_file.unlink(missing_ok=True)
        if autopilot_env_present:
            self.autopilot_env_file.write_text(
                "AUTOPILOT_DATABASE_URL=postgres://autopilot\n",
                encoding="utf-8",
            )
        else:
            self.autopilot_env_file.unlink(missing_ok=True)
        if lease_text is None:
            self.lease_file.unlink(missing_ok=True)
        else:
            self.lease_file.write_text(lease_text, encoding="utf-8")

        selected_spool = self.spool if spool is None else spool
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bindir}:{env.get('PATH', '')}",
                "ASSISTANT_LAB_ENV_FILE": str(self.env_file),
                "AUTOPILOT_ENV_FILE": str(self.autopilot_env_file),
                "ASSISTANT_LAB_PYTHON": str(self.fake_python),
                "BRIDGE_VIDEO_QUEUE_DSN_FILE": str(self.queue_file),
                "ORACLE_HOST_LEASE_FILE": str(self.lease_file),
                "ORACLE_IDLE_REQUIRED_LOCAL_SPOOLS": str(selected_spool),
                "ORACLE_IDLE_TEST_RESULT": result,
                "ORACLE_IDLE_TEST_EXIT_CODE": str(python_exit_code),
                "ORACLE_IDLE_TEST_STDERR": python_stderr,
            }
        )
        return subprocess.run(
            ["bash", str(CLASSIFIER)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    @property
    def active_work_spools(self) -> str:
        return ":".join(
            str(path)
            for path in (
                self.video_inbox,
                self.video_running,
                self.observer_pending,
                self.observer_running,
            )
        )


def _proof_text(
    state: str,
    *,
    started: int | None = None,
    observed: int | None = None,
    reason: str = CANONICAL_IDLE_REASON,
) -> str:
    now = int(time.time())
    observed = now if observed is None else observed
    started = observed - 1 if started is None else started
    return (
        "ORACLE_IDLE_CONTRACT_VERSION=2\n"
        f"ORACLE_IDLE_STARTED_AT_EPOCH={started}\n"
        f"ORACLE_IDLE_OBSERVED_AT_EPOCH={observed}\n"
        f"ORACLE_IDLE_REASON={reason}\n"
        f"ORACLE_IDLE_STATE={state}\n"
    )


def _run_authorizer(
    proof_text: str | None,
    *,
    max_age: int = 30,
    max_duration: int = 30,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        proof = Path(tmp) / "proof.txt"
        if proof_text is not None:
            proof.write_text(proof_text, encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(AUTHORIZER),
                "--proof",
                str(proof),
                "--max-age-seconds",
                str(max_age),
                "--max-duration-seconds",
                str(max_duration),
            ],
            check=False,
            capture_output=True,
            text=True,
        )


class OracleIdleClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = ClassifierHarness()

    def tearDown(self) -> None:
        self.harness.close()

    def assert_state(self, output: str, state: str) -> None:
        lines = output.splitlines()
        self.assertEqual(5, len(lines), output)
        self.assertEqual("ORACLE_IDLE_CONTRACT_VERSION=2", lines[0])
        self.assertRegex(lines[1], r"^ORACLE_IDLE_STARTED_AT_EPOCH=[0-9]+$")
        self.assertRegex(lines[2], r"^ORACLE_IDLE_OBSERVED_AT_EPOCH=[0-9]+$")
        self.assertRegex(lines[3], r"^ORACLE_IDLE_REASON=[A-Za-z0-9_./,:=+-]+$")
        self.assertEqual(f"ORACLE_IDLE_STATE={state}", lines[4])

    def test_all_sources_proved_empty_is_idle(self) -> None:
        completed = self.harness.run()
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assert_state(completed.stdout, "IDLE")

    # Required negative family 1: assistant_lab.job.
    def test_assistant_lab_job_queued_claimed_or_running_is_busy(self) -> None:
        completed = self.harness.run(
            result=(
                "BUSY:jobs=1,research=0,research_children=0,"
                "control=0,operator_lease=0,autopilot=0,video=0"
            )
        )
        self.assert_state(completed.stdout, "BUSY")

    # Required negative family 2: assistant_lab.control_command.
    def test_control_command_active_is_busy(self) -> None:
        completed = self.harness.run(
            result=(
                "BUSY:jobs=0,research=0,research_children=0,"
                "control=1,operator_lease=0,autopilot=0,video=0"
            )
        )
        self.assert_state(completed.stdout, "BUSY")

    # Required negative family 3: assistant_lab.research_job.
    def test_research_job_nonterminal_is_busy(self) -> None:
        completed = self.harness.run(
            result=(
                "BUSY:jobs=0,research=1,research_children=0,"
                "control=0,operator_lease=0,autopilot=0,video=0"
            )
        )
        self.assert_state(completed.stdout, "BUSY")

    def test_active_research_child_work_is_busy(self) -> None:
        completed = self.harness.run(
            result=(
                "BUSY:jobs=0,research=0,research_children=1,"
                "control=0,operator_lease=0,autopilot=0,video=0"
            )
        )
        self.assert_state(completed.stdout, "BUSY")

    # Required negative family 4: Universal Video queue.
    def test_universal_video_active_queue_is_busy(self) -> None:
        completed = self.harness.run(
            result=(
                "BUSY:jobs=0,research=0,research_children=0,"
                "control=0,operator_lease=0,autopilot=0,video=1"
            )
        )
        self.assert_state(completed.stdout, "BUSY")

    def test_autopilot_active_task_is_busy(self) -> None:
        completed = self.harness.run(
            result=(
                "BUSY:jobs=0,research=0,research_children=0,"
                "control=0,operator_lease=0,autopilot=1,video=0"
            )
        )
        self.assert_state(completed.stdout, "BUSY")

    def test_dds3_pilot_mass_service_is_busy(self) -> None:
        self.harness.set_mass_service_states(pilot=("active", 0))
        completed = self.harness.run()
        self.assertIn(
            "ORACLE_IDLE_REASON=dds3_mass_service_active", completed.stdout
        )
        self.assert_state(completed.stdout, "BUSY")

    def test_dds3_main_mass_service_is_busy(self) -> None:
        self.harness.set_mass_service_states(main=("active", 0))
        completed = self.harness.run()
        self.assertIn(
            "ORACLE_IDLE_REASON=dds3_mass_service_active", completed.stdout
        )
        self.assert_state(completed.stdout, "BUSY")

    def test_missing_dds3_mass_unit_telemetry_is_unknown(self) -> None:
        self.harness.set_mass_service_states(pilot=("unknown", 4))
        completed = self.harness.run()
        self.assertIn(
            "ORACLE_IDLE_REASON=dds3_mass_service_unknown", completed.stdout
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_failed_dds3_mass_unit_is_unknown(self) -> None:
        self.harness.set_mass_service_states(main=("failed", 3))
        completed = self.harness.run()
        self.assertIn(
            "ORACLE_IDLE_REASON=dds3_mass_service_failed", completed.stdout
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_transitioning_dds3_mass_unit_is_unknown(self) -> None:
        self.harness.set_mass_service_states(pilot=("activating", 3))
        completed = self.harness.run()
        self.assertIn(
            "ORACLE_IDLE_REASON=dds3_mass_service_activating", completed.stdout
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    # Required negative family 5: local spool.
    def test_local_spool_with_work_is_busy(self) -> None:
        (self.harness.spool / "queued-work.json").write_text(
            "{}\n", encoding="utf-8"
        )
        completed = self.harness.run()
        self.assertIn(
            "ORACLE_IDLE_REASON=local_spool_has_work", completed.stdout
        )
        self.assert_state(completed.stdout, "BUSY")

    def test_universal_video_inbox_job_is_busy(self) -> None:
        (self.harness.video_inbox / "queued.json").write_text(
            "{}\n", encoding="utf-8"
        )
        completed = self.harness.run(spool=self.harness.active_work_spools)
        self.assertIn("ORACLE_IDLE_REASON=local_spool_has_work", completed.stdout)
        self.assert_state(completed.stdout, "BUSY")

    def test_universal_video_running_job_is_busy(self) -> None:
        (self.harness.video_running / "running.json").write_text(
            "{}\n", encoding="utf-8"
        )
        completed = self.harness.run(spool=self.harness.active_work_spools)
        self.assertIn("ORACLE_IDLE_REASON=local_spool_has_work", completed.stdout)
        self.assert_state(completed.stdout, "BUSY")

    def test_observer_pending_job_is_busy(self) -> None:
        (self.harness.observer_pending / "queued.json").write_text(
            "{}\n", encoding="utf-8"
        )
        completed = self.harness.run(spool=self.harness.active_work_spools)
        self.assertIn("ORACLE_IDLE_REASON=local_spool_has_work", completed.stdout)
        self.assert_state(completed.stdout, "BUSY")

    def test_observer_running_job_is_busy(self) -> None:
        (self.harness.observer_running / "running.json").write_text(
            "{}\n", encoding="utf-8"
        )
        completed = self.harness.run(spool=self.harness.active_work_spools)
        self.assertIn("ORACLE_IDLE_REASON=local_spool_has_work", completed.stdout)
        self.assert_state(completed.stdout, "BUSY")

    # Required negative family 6: operator/maintenance lease.
    def test_bounded_host_operator_lease_is_busy(self) -> None:
        expiry = int(time.time()) + 300
        completed = self.harness.run(
            lease_text=f"expires_at_epoch={expiry}\n"
        )
        self.assertIn("ORACLE_IDLE_REASON=host_lease_active", completed.stdout)
        self.assert_state(completed.stdout, "BUSY")

    def test_database_operator_or_maintenance_lease_is_busy(self) -> None:
        completed = self.harness.run(
            result=(
                "BUSY:jobs=0,research=0,research_children=0,"
                "control=0,operator_lease=1,autopilot=0,video=0"
            )
        )
        self.assert_state(completed.stdout, "BUSY")

    # Required negative family 7: stale telemetry.
    def test_stale_host_lease_telemetry_is_unknown(self) -> None:
        completed = self.harness.run(
            lease_text="expires_at_epoch=1000000000\n"
        )
        self.assertIn("ORACLE_IDLE_REASON=host_lease_stale", completed.stdout)
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_stale_database_snapshot_is_unknown(self) -> None:
        completed = self.harness.run(
            result="UNKNOWN:assistant_lab_telemetry_stale"
        )
        self.assertIn(
            "ORACLE_IDLE_REASON=assistant_lab_telemetry_stale",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    # Required negative family 8: missing telemetry.
    def test_missing_universal_video_telemetry_is_unknown(self) -> None:
        completed = self.harness.run(video_dsn_present=False)
        self.assertIn(
            "ORACLE_IDLE_REASON=video_queue_dsn_unavailable",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_missing_autopilot_telemetry_is_unknown(self) -> None:
        completed = self.harness.run(autopilot_env_present=False)
        self.assertIn(
            "ORACLE_IDLE_REASON=autopilot_env_unavailable",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_missing_local_spool_telemetry_is_unknown(self) -> None:
        missing = self.harness.root / "missing-spool"
        completed = self.harness.run(spool=missing)
        self.assertIn(
            "ORACLE_IDLE_REASON=local_spool_unavailable",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_unavailable_database_is_unknown(self) -> None:
        completed = self.harness.run(
            result="UNKNOWN:database_check_failed"
        )
        self.assertIn(
            "ORACLE_IDLE_REASON=database_check_failed", completed.stdout
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_database_classifier_nonzero_after_idle_text_is_unknown(self) -> None:
        completed = self.harness.run(
            result=(
                "IDLE:jobs=0,research=0,research_children=0,"
                "control=0,operator_lease=0,autopilot=0,video=0"
            ),
            python_exit_code=7,
        )
        self.assertIn(
            "ORACLE_IDLE_REASON=database_classifier_failed",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_database_classifier_stderr_after_idle_text_is_unknown(self) -> None:
        completed = self.harness.run(
            result=(
                "IDLE:jobs=0,research=0,research_children=0,"
                "control=0,operator_lease=0,autopilot=0,video=0"
            ),
            python_stderr="partial telemetry failure",
        )
        self.assertIn(
            "ORACLE_IDLE_REASON=database_classifier_stderr",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_database_classifier_mixed_lines_are_unknown(self) -> None:
        completed = self.harness.run(
            result=(
                "IDLE:jobs=0,research=0,research_children=0,"
                "control=0,operator_lease=0,autopilot=0,video=0\\n"
                "UNKNOWN:database_check_failed"
            )
        )
        self.assertIn(
            "ORACLE_IDLE_REASON=invalid_database_classifier_output",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_unbounded_host_lease_is_unknown(self) -> None:
        expiry = int(time.time()) + 172800
        completed = self.harness.run(
            lease_text=f"expires_at_epoch={expiry}\n"
        )
        self.assertIn(
            "ORACLE_IDLE_REASON=host_lease_unbounded",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")


class OracleStopAuthorizerTests(unittest.TestCase):
    def assert_forbidden(
        self, completed: subprocess.CompletedProcess[str], reason: str
    ) -> None:
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual(
            (
                "ORACLE_STOP_AUTHORIZED=NO\n"
                f"ORACLE_STOP_AUTHORIZATION_REASON={reason}\n"
            ),
            completed.stdout,
        )

    def test_fresh_exact_idle_is_the_only_allowed_proof(self) -> None:
        completed = _run_authorizer(_proof_text("IDLE"))
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(
            (
                "ORACLE_STOP_AUTHORIZED=YES\n"
                "ORACLE_STOP_AUTHORIZATION_REASON=fresh_exact_idle\n"
            ),
            completed.stdout,
        )

    def test_busy_forbids_stop(self) -> None:
        self.assert_forbidden(
            _run_authorizer(_proof_text("BUSY")),
            "state_busy_forbids_stop",
        )

    def test_contradictory_idle_reason_forbids_stop(self) -> None:
        self.assert_forbidden(
            _run_authorizer(
                _proof_text("IDLE", reason="database_check_failed")
            ),
            "idle_reason_not_canonical",
        )

    # Explicit required invariant: UNKNOWN -> STOP forbidden.
    def test_unknown_forbids_stop(self) -> None:
        self.assert_forbidden(
            _run_authorizer(_proof_text("UNKNOWN")),
            "state_unknown_forbids_stop",
        )

    def test_stale_proof_forbids_stop(self) -> None:
        old = int(time.time()) - 120
        self.assert_forbidden(
            _run_authorizer(
                _proof_text("IDLE", started=old - 1, observed=old)
            ),
            "proof_stale",
        )

    def test_missing_proof_forbids_stop(self) -> None:
        self.assert_forbidden(
            _run_authorizer(None),
            "proof_missing_or_unreadable",
        )

    def test_missing_line_forbids_stop(self) -> None:
        partial = "\n".join(_proof_text("IDLE").splitlines()[:-1]) + "\n"
        self.assert_forbidden(
            _run_authorizer(partial),
            "proof_line_count_invalid",
        )

    def test_partially_successful_mixed_output_forbids_stop(self) -> None:
        mixed = _proof_text("IDLE") + "ORACLE_IDLE_STATE=UNKNOWN\n"
        self.assert_forbidden(
            _run_authorizer(mixed),
            "proof_line_count_invalid",
        )

    def test_extra_output_forbids_stop(self) -> None:
        extra = "diagnostic\n" + _proof_text("IDLE")
        self.assert_forbidden(
            _run_authorizer(extra),
            "proof_line_count_invalid",
        )

    def test_extra_terminal_newline_forbids_stop(self) -> None:
        self.assert_forbidden(
            _run_authorizer(_proof_text("IDLE") + "\n"),
            "proof_framing_invalid",
        )

    def test_long_running_probe_forbids_stop(self) -> None:
        now = int(time.time())
        self.assert_forbidden(
            _run_authorizer(
                _proof_text("IDLE", started=now - 60, observed=now),
                max_duration=30,
            ),
            "proof_duration_exceeded",
        )


class StaticCoverageAndConsumerTests(unittest.TestCase):
    def test_snapshot_covers_every_required_database_family(self) -> None:
        sql = SCHEMA.read_text(encoding="utf-8")
        self.assertIn(
            "status IN ('QUEUED', 'CLAIMED', 'RUNNING')", sql
        )
        self.assertIn(
            "'QUEUED', 'ACCEPTED', 'RUNNING', 'CHECKPOINTED', 'VALIDATING'",
            sql,
        )
        self.assertIn("active_research_child_jobs", sql)
        self.assertIn("assistant_lab.control_command", sql)
        self.assertIn("assistant_lab.operator_maintenance_lease", sql)
        self.assertIn("stale_operator_maintenance_leases", sql)

        classifier = CLASSIFIER.read_text(encoding="utf-8")
        self.assertIn("FROM autopilot.task_status", classifier)
        self.assertNotIn(
            "status IN ('READY', 'RUNNING', 'WAITING_EXTERNAL', 'EVALUATING')",
            classifier,
        )
        self.assertIn(
            "status NOT IN (",
            classifier,
        )
        for terminal in (
            "OWNER_REQUIRED",
            "FAILED_CLOSED",
            "BUDGET_STOP",
            "DONE",
            "CANCELLED",
        ):
            self.assertIn(f"'{terminal}'", classifier)
        start = classifier.index("WHERE status NOT IN (")
        terminal_clause = classifier[start : classifier.index(")", start)]
        for nonterminal in (
            "NEW",
            "VALIDATING",
            "READY",
            "EVALUATING",
            "RUNNING",
            "WAITING_EXTERNAL",
        ):
            self.assertNotIn(f"'{nonterminal}'", terminal_clause)

    def test_universal_video_statuses_are_exactly_covered(self) -> None:
        script = CLASSIFIER.read_text(encoding="utf-8")
        self.assertIn(
            "status IN ('PENDING_CANARY', 'QUEUED', 'LEASED')",
            script,
        )

    def test_default_spool_inventory_matches_deployed_active_leaves(self) -> None:
        script = CLASSIFIER.read_text(encoding="utf-8")
        default_line = next(
            line for line in script.splitlines()
            if line.startswith("REQUIRED_LOCAL_SPOOLS=")
        )
        self.assertIn("$VIDEO_DIR/spool/inbox", default_line)
        self.assertIn("$VIDEO_DIR/spool/running", default_line)
        self.assertIn("$OBSERVER_DIR/jobs/pending", default_line)
        self.assertIn("$OBSERVER_DIR/jobs/running", default_line)
        self.assertNotIn("/var/lib/bridge-school/uv-spool", default_line)
        for terminal_leaf in ("/done", "/failed", "/results", "/progress"):
            self.assertNotIn(terminal_leaf, default_line)

    def test_stop_consumer_uses_exact_authorizer_not_raw_idle_grep(self) -> None:
        workflow = FINALIZER.read_text(encoding="utf-8")
        self.assertNotIn("grep -Fx 'ORACLE_IDLE_STATE=IDLE'", workflow)
        authorizer = workflow.index(
            "authorization=\"$(python3 ops/oracle_idle_stop_guard.py"
        )
        exact_yes = workflow.index(
            "ORACLE_STOP_AUTHORIZED=YES", authorizer
        )
        stop = workflow.index(
            "oci compute instance action --instance-id "
            "\"$OCI_INSTANCE_OCID\" --action STOP"
        )
        self.assertLess(authorizer, exact_yes)
        self.assertLess(exact_yes, stop)
        self.assertIn("idle_stderr", workflow)
        self.assertNotIn("schedule:", workflow)

    def test_finalizer_rechecks_autopilot_service_at_final_idle_boundary(self) -> None:
        workflow = FINALIZER.read_text(encoding="utf-8")
        final_step = workflow.index(
            "Stop exact instance only after fresh exact IDLE authorization"
        )
        final_gate = workflow.index("FINAL_AUTOPILOT_SERVICE_GATE", final_step)
        active = workflow.index(
            "systemctl is-active school-autopilot-shadow.service", final_gate
        )
        enabled = workflow.index(
            "systemctl is-enabled school-autopilot-shadow.service", final_gate
        )
        inactive_check = workflow.index(
            '[[ "$active" == inactive && "$enabled" == disabled ]]',
            final_gate,
        )
        revision_read = workflow.index(
            "current/SOURCE_REVISION", final_gate
        )
        revision_check = workflow.index(
            '[[ "$staged_revision" == "${{ steps.request.outputs.expected_staged_revision }}" ]]',
            final_gate,
        )
        idle_probe = workflow.index(
            "sudo -n /usr/local/sbin/oracle-idle-state", final_gate
        )
        authorizer = workflow.index(
            "authorization=\"$(python3 ops/oracle_idle_stop_guard.py", idle_probe
        )
        stop = workflow.index(
            "oci compute instance action --instance-id "
            "\"$OCI_INSTANCE_OCID\" --action STOP",
            authorizer,
        )
        self.assertLess(active, enabled)
        self.assertLess(enabled, revision_read)
        self.assertLess(revision_read, inactive_check)
        self.assertLess(inactive_check, idle_probe)
        self.assertLess(revision_check, idle_probe)
        self.assertLess(idle_probe, authorizer)
        self.assertLess(authorizer, stop)

    def test_instance_power_gets_final_proof_after_paginated_epoch(self) -> None:
        workflow = INSTANCE_POWER.read_text(encoding="utf-8")
        final_step = workflow.index("Stop exact instance only with IDLE proof")
        final_probe = workflow.index(
            "bridge-school-oracle-final-idle-proof-${GITHUB_RUN_ID}", final_step
        )
        authorizer = workflow.index(
            "authorization=\"$(python3 ops/oracle_idle_stop_guard.py", final_probe
        )
        epoch = workflow.index("final_epoch_state=", final_step)
        paginated = workflow.index("gh api --paginate --slurp", epoch)
        complete = workflow.index("len(runs)==total", paginated)
        newer = workflow.index('r.get(\"event\")!=\"pull_request\"', complete)
        terminal = workflow.index('row.get(\"status\")!=\"completed\"', newer)
        current = workflow.index('[[ \"$final_epoch_state\" == CURRENT ]]', terminal)
        stop = workflow.index(
            "oci compute instance action --instance-id "
            '"$OCI_INSTANCE_OCID" --action STOP',
            current,
        )
        self.assertLess(epoch, paginated)
        self.assertLess(paginated, complete)
        self.assertLess(complete, final_probe)
        self.assertLess(final_probe, authorizer)
        self.assertLess(complete, newer)
        self.assertLess(newer, terminal)
        self.assertLess(terminal, current)
        self.assertLess(current, stop)

    def test_instance_power_rechecks_epoch_after_final_probe(self) -> None:
        workflow = INSTANCE_POWER.read_text(encoding="utf-8")
        final_step = workflow.index("Stop exact instance only with IDLE proof")
        final_probe = workflow.index(
            "bridge-school-oracle-final-idle-proof-${GITHUB_RUN_ID}", final_step
        )
        first_authorizer = workflow.index(
            "authorization=\"$(python3 ops/oracle_idle_stop_guard.py", final_probe
        )
        post_probe_epoch = workflow.index("post_probe_epoch_state=", first_authorizer)
        second_paginated = workflow.index(
            "gh api --paginate --slurp", post_probe_epoch
        )
        post_probe_current = workflow.index(
            '[[ "$post_probe_epoch_state" == CURRENT ]]', second_paginated
        )
        second_authorizer = workflow.index(
            "authorization=\"$(python3 ops/oracle_idle_stop_guard.py",
            post_probe_current,
        )
        stop = workflow.index(
            "oci compute instance action --instance-id "
            '"$OCI_INSTANCE_OCID" --action STOP',
            second_authorizer,
        )
        self.assertLess(final_probe, first_authorizer)
        self.assertLess(first_authorizer, post_probe_epoch)
        self.assertLess(post_probe_epoch, second_paginated)
        self.assertLess(second_paginated, post_probe_current)
        self.assertLess(post_probe_current, second_authorizer)
        self.assertLess(second_authorizer, stop)

    def test_instance_power_stop_uses_exact_authorizer(self) -> None:
        workflow = INSTANCE_POWER.read_text(encoding="utf-8")
        self.assertNotIn(
            "steps.idle.outputs.idle_state == 'IDLE'", workflow
        )
        authorizer = workflow.index("ops/oracle_idle_stop_guard.py")
        exact_yes = workflow.index("ORACLE_STOP_AUTHORIZED=YES", authorizer)
        stop = workflow.index(
            "oci compute instance action --instance-id "
            '"$OCI_INSTANCE_OCID" --action STOP'
        )
        self.assertLess(authorizer, exact_yes)
        self.assertLess(exact_yes, stop)

    def test_instance_power_preserves_zero_exit_and_proof_framing(self) -> None:
        workflow = INSTANCE_POWER.read_text(encoding="utf-8")
        self.assertNotIn('get("exit-code","") or ""', workflow)
        self.assertNotIn('printf \'%s\\n\' "$text" > "$proof"', workflow)
        self.assertNotIn('text="$(printf \'%s\' "$execution_json"', workflow)
        self.assertGreaterEqual(
            workflow.count("sys.stdout.write(value)' > \"$proof\""),
            2,
        )

    def test_no_stop_command_exists_in_classifier_or_authorizer(self) -> None:
        combined = (
            CLASSIFIER.read_text(encoding="utf-8")
            + AUTHORIZER.read_text(encoding="utf-8")
        )
        self.assertNotIn("oci compute instance action", combined)
        self.assertNotIn("gcloud compute instances stop", combined)


if __name__ == "__main__":
    unittest.main()
