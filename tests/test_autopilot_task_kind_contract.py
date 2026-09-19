from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "ops" / "autopilot" / "validate_task_kinds.py"
SPEC = importlib.util.spec_from_file_location("validate_task_kinds", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class AutopilotTaskKindContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contracts = ROOT / "ops" / "autopilot" / "task-kinds"

    def test_checked_in_contracts_pass(self) -> None:
        paths = VALIDATOR.validate_directory(self.contracts)
        self.assertGreaterEqual(len(paths), 2)

    def test_filename_must_match_kind(self) -> None:
        source = json.loads((self.contracts / "AUTOPILOT_SMOKE_V1.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "WRONG_NAME.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(VALIDATOR.ContractError, "filename must match kind"):
                VALIDATOR.validate_contract(source, path=path)

    def test_unknown_executor_fails_closed(self) -> None:
        data = json.loads((self.contracts / "AUTOPILOT_SMOKE_V1.json").read_text(encoding="utf-8"))
        data["allowed_executors"].append("arbitrary_shell")
        with self.assertRaisesRegex(VALIDATOR.ContractError, "unsupported executors"):
            VALIDATOR.validate_contract(data, path=Path("AUTOPILOT_SMOKE_V1.json"))

    def test_model_turns_require_openai_executor(self) -> None:
        data = json.loads((self.contracts / "AUTOPILOT_SMOKE_V1.json").read_text(encoding="utf-8"))
        data["retry_policy"]["max_model_turns"] = 1
        with self.assertRaisesRegex(VALIDATOR.ContractError, "zero without openai executor"):
            VALIDATOR.validate_contract(data, path=Path("AUTOPILOT_SMOKE_V1.json"))

    def test_owner_required_transition_requires_exact_policy(self) -> None:
        data = json.loads((self.contracts / "RECOVERY_SHADOW_V1.json").read_text(encoding="utf-8"))
        data["approval_policy"]["required_for"] = []
        with self.assertRaisesRegex(VALIDATOR.ContractError, "OWNER_REQUIRED events"):
            VALIDATOR.validate_contract(data, path=Path("RECOVERY_SHADOW_V1.json"))

    def test_scope_digest_gate_cannot_be_disabled(self) -> None:
        data = json.loads((self.contracts / "RECOVERY_SHADOW_V1.json").read_text(encoding="utf-8"))
        data["approval_policy"]["scope_digest_required"] = False
        with self.assertRaisesRegex(VALIDATOR.ContractError, "scope_digest_required"):
            VALIDATOR.validate_contract(data, path=Path("RECOVERY_SHADOW_V1.json"))

    def test_smoke_contract_cannot_add_openai(self) -> None:
        data = json.loads((self.contracts / "AUTOPILOT_SMOKE_V1.json").read_text(encoding="utf-8"))
        data["allowed_executors"].append("openai")
        with self.assertRaisesRegex(VALIDATOR.ContractError, "must not call OpenAI"):
            VALIDATOR.validate_contract(data, path=Path("AUTOPILOT_SMOKE_V1.json"))

    def test_shadow_contract_cannot_gain_write_capable_executor(self) -> None:
        data = json.loads((self.contracts / "RECOVERY_SHADOW_V1.json").read_text(encoding="utf-8"))
        data["allowed_executors"].append("oracle_via_github")
        with self.assertRaisesRegex(VALIDATOR.ContractError, "write-capable executors"):
            VALIDATOR.validate_contract(data, path=Path("RECOVERY_SHADOW_V1.json"))


if __name__ == "__main__":
    unittest.main()
