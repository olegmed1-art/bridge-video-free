"""Offline contract for the explicit Oracle route-install event gate.

This checks workflow source and executes only its tiny scope guard with
synthetic environment values. It never invokes SSH, an installer or GitHub.
"""
import ast
import contextlib
import io
import os
from pathlib import Path
import textwrap
import unittest
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/oracle-light-route-lease.yml"
REPOSITORY = "olegmed1-art/bridge-video-free"
MAIN = "refs/heads/main"
SHA = "a" * 40


def source():
    return WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")


def tunnel(source_text):
    return source_text.split("\n  tunnel:\n", 1)[1]


def guard_code(tunnel_text):
    marker = "          python -I -B - <<'PY'\n"
    return textwrap.dedent(tunnel_text.split(marker, 1)[1].split("\n          PY", 1)[0])


def evaluate_scope(code, values):
    tree = ast.parse(code)
    imports = [name.name for node in ast.walk(tree) if isinstance(node, ast.Import)
               for name in node.names]
    if sorted(imports) != ["os", "re"]:
        raise AssertionError("scope guard imports changed")
    if any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)):
        raise AssertionError("scope guard gained a from-import")
    allowed_calls = {"require", "os.environ.get", "re.fullmatch", "SystemExit"}
    if any(ast.unparse(node.func) not in allowed_calls for node in ast.walk(tree)
           if isinstance(node, ast.Call)):
        raise AssertionError("scope guard gained another call")
    output = io.StringIO()
    with patch.dict(os.environ, values, clear=True), contextlib.redirect_stdout(output), \
            contextlib.redirect_stderr(output):
        try:
            exec(compile(tree, "<synthetic-oracle-install-gate>", "exec"), {})
        except SystemExit as error:
            if output.getvalue():
                raise AssertionError("scope guard leaked a value") from error
            return str(error)
    if output.getvalue():
        raise AssertionError("scope guard emitted data")
    return None


class OracleInstallEventGate(unittest.TestCase):
    def test_contract_events_remain_and_tunnel_expression_is_exact(self):
        text = source()
        events = text.split("\npermissions:", 1)[0]
        self.assertIn("\n  pull_request:\n", events)
        self.assertIn("\n  push:\n    branches: [main]\n", events)
        self.assertIn("\n  workflow_dispatch:\n    inputs:\n      install_route:\n", events)
        self.assertIn("        type: boolean\n        default: false\n", events)
        contract = text.split("\n  contract:\n", 1)[1].split("\n  tunnel:\n", 1)[0]
        self.assertNotIn("\n    if:", contract)
        job = tunnel(text)
        self.assertTrue(job.startswith("    needs: contract\n"))
        expression = job.split("\n    if: >-\n", 1)[1].split("\n    runs-on:", 1)[0]
        self.assertEqual(" ".join(expression.split()),
            "github.repository == 'olegmed1-art/bridge-video-free' && "
            "github.ref == 'refs/heads/main' && "
            "github.event_name == 'workflow_dispatch' && "
            "inputs.install_route == true && github.run_attempt == 1")

    def test_exact_case_sensitive_precheck_rejects_auto_and_bad_manual_events(self):
        code = guard_code(tunnel(source()))
        good = {"EVENT_NAME": "workflow_dispatch", "EVENT_REPOSITORY": REPOSITORY,
                "EVENT_REF": MAIN, "EVENT_SHA": SHA, "RUN_ATTEMPT": "1",
                "INSTALL_ROUTE": "true"}
        self.assertIsNone(evaluate_scope(code, good))
        for key, values in {
            "EVENT_NAME": ("push", "pull_request", "schedule", "WORKFLOW_DISPATCH", ""),
            "EVENT_REPOSITORY": ("other/bridge-video-free", REPOSITORY.upper(), ""),
            "EVENT_REF": ("refs/heads/Main", "refs/heads/feature", ""),
            "EVENT_SHA": ("", "A" * 40, "a" * 39, "g" * 40),
            "RUN_ATTEMPT": ("", "2", "0"),
            "INSTALL_ROUTE": ("", "false", "TRUE", "1"),
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(evaluate_scope(code, {**good, key: value}),
                                     "ORACLE_ROUTE_INSTALL_SCOPE_REJECTED")

    def test_precheck_precedes_checkout_and_secret_and_installer_is_unchanged(self):
        job = tunnel(source())
        precheck = job.index("- name: Validate exact manual install scope")
        checkout = job.index("- uses: actions/checkout@")
        secret = job.index("SSH_PRIVATE_KEY:")
        installer = job.index("python ops/oracle_light_route_install.py bundle")
        self.assertLess(precheck, checkout)
        self.assertLess(checkout, secret)
        self.assertLess(secret, installer)
        self.assertIn("ref: ${{ github.sha }}", job)
        self.assertIn("StrictHostKeyChecking=yes", job)
        self.assertIn("bash ops/oracle_known_hosts_from_scan.sh 92.5.47.149 "
                      "SHA256:XBR1x74uJ41BxmDF7Y9P20GjIjNbrYXqieV4c2MC0Go",
                      job)
        self.assertIn("python ops/oracle_light_pg_tunnel_probe.py", job)
        self.assertIn("python ops/oracle_light_route_probe.py", job)


if __name__ == "__main__":
    unittest.main()
