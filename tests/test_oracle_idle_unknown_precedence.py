from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BASE_TEST = ROOT / "tests" / "test_oracle_idle_stop_guard.py"

spec = importlib.util.spec_from_file_location("oracle_idle_base_tests", BASE_TEST)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load Oracle idle test harness")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


class OracleIdleUnknownPrecedenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = base.ClassifierHarness()

    def tearDown(self) -> None:
        self.harness.close()

    def assert_state(self, output: str, state: str) -> None:
        self.assertEqual(
            f"ORACLE_IDLE_STATE={state}",
            output.splitlines()[-1],
            output,
        )

    def test_pgrep_error_is_unknown_not_empty_process_telemetry(self) -> None:
        original_write = base._write_executable

        def force_pgrep_error(path: Path, body: str) -> None:
            if path.name == "pgrep":
                original_write(path, "#!/bin/sh\nexit 2\n")
            else:
                original_write(path, body)

        with mock.patch.object(
            base, "_write_executable", side_effect=force_pgrep_error
        ):
            completed = self.harness.run()

        self.assertIn(
            "ORACLE_IDLE_REASON=process_telemetry_unavailable",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_busy_spool_cannot_mask_missing_video_telemetry(self) -> None:
        (self.harness.spool / "work.json").write_text("{}\n", encoding="utf-8")
        completed = self.harness.run(video_dsn_present=False)
        self.assertIn(
            "ORACLE_IDLE_REASON=video_queue_dsn_unavailable",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_active_lease_cannot_mask_missing_autopilot_telemetry(self) -> None:
        import time

        completed = self.harness.run(
            lease_text=f"expires_at_epoch={int(time.time()) + 300}\n",
            autopilot_env_present=False,
        )
        self.assertIn(
            "ORACLE_IDLE_REASON=autopilot_env_unavailable",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_database_busy_cannot_mask_missing_spool_telemetry(self) -> None:
        missing = self.harness.root / "missing-spool"
        completed = self.harness.run(
            spool=missing,
            result=(
                "BUSY:jobs=1,research=0,research_children=0,"
                "control=0,operator_lease=0,autopilot=0,video=0"
            ),
        )
        self.assertIn(
            "ORACLE_IDLE_REASON=local_spool_unavailable",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "UNKNOWN")

    def test_observer_busy_with_complete_telemetry_remains_busy(self) -> None:
        completed = self.harness.run(observer_busy=True)
        self.assertIn(
            "ORACLE_IDLE_REASON=observer_experiment_process",
            completed.stdout,
        )
        self.assert_state(completed.stdout, "BUSY")


if __name__ == "__main__":
    unittest.main()
