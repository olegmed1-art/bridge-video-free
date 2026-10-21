"""Static safety regression for the existing main-only database smoke job.

This checks the repository's fixed workflow layout, without a YAML dependency.
It does not execute the workflow or claim a live database connection.
"""
from pathlib import Path
import ast
import re
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/bridge-school-api-ci.yml"


def check_workflow(text: str) -> None:
    job_match = re.search(r"^  production-smoke:\n(.*?)(?=^  [\w-]+:|\Z)", text, re.M | re.S)
    if not job_match:
        raise ValueError("missing production smoke job")
    job = job_match.group(1)
    if "    if: github.event_name == 'push' && github.ref == 'refs/heads/main' && needs.api-static.result == 'success' && needs.api-static.outputs.production_smoke_scope_status == 'run' && needs.api-static.outputs.allow_production_smoke == 'true'\n" not in job:
        raise ValueError("production smoke must be restricted to main push")
    if "allow_production_smoke: ${{ steps.smoke-scope.outputs.allow_smoke }}" not in text:
        raise ValueError("production smoke needs the no-secret scope output")
    if "production_smoke_scope_status: ${{ steps.smoke-scope.outputs.scope_status }}" not in text:
        raise ValueError("production smoke needs explicit successful classification")
    scope_match = re.search(r"^      - name: Classify complete main-push diff before production credentials\n(.*?)(?=^      - name: |\Z)", text, re.M | re.S)
    if not scope_match:
        raise ValueError("missing classifier step")
    classification = scope_match.group(1)
    if ('        if: github.event_name == \'push\' && github.ref == \'refs/heads/main\'\n' not in classification
            or '          set -euo pipefail\n' not in classification
            or '          python -I -B tools/l1_ci_smoke_scope.py >> "$GITHUB_OUTPUT"\n' not in classification
            or re.search(r"^\s+continue-on-error:", classification, re.M)
            or '||' in classification or ';' in classification):
        raise ValueError("scope classification must precede secret-bearing smoke")
    if "    needs: api-static\n" not in job:
        raise ValueError("production smoke must wait for static checks")
    if "    timeout-minutes: 2\n" not in job:
        raise ValueError("production smoke needs the two-minute job budget")
    if re.search(r"^\s+(?:continue-on-error|if):\s*(?:true|always\(\))", job, re.M):
        # The cleanup step alone must still run after a failure.
        without_cleanup = job.split("      - name: Stop API\n")[0]
        if re.search(r"^\s+(?:continue-on-error|if):\s*(?:true|always\(\))", without_cleanup, re.M):
            raise ValueError("preflight failure must stop API startup")
    blocks = re.split(r"^      - name: ", job, flags=re.M)[1:]
    steps = {block.splitlines()[0]: block for block in blocks}
    probe_name = "Verify production database connection read-only"
    for name in (probe_name, "Start API", "Verify authenticated production overview"):
        if name not in steps:
            raise ValueError("missing required smoke step")
    if list(steps).index(probe_name) >= list(steps).index("Start API"):
        raise ValueError("read-only preflight must precede startup")
    for name in (probe_name, "Start API"):
        block = steps[name]
        if "          VERCEL_ENV: production\n" not in block:
            raise ValueError("production endpoint must be selected explicitly")
        if "          BRIDGE_APP_DATABASE_URL: ${{ secrets.BRIDGE_APP_DATABASE_URL }}\n" not in block:
            raise ValueError("use only the existing resident application credential")
    probe = steps[probe_name]
    if "timeout --kill-after=5s 20s python -m bridge_school_api.incident_db_probe --connect\n" not in probe:
        raise ValueError("require one bounded existing read-only probe")
    if "          set -euo pipefail\n" not in probe or "||" in probe:
        raise ValueError("probe failure must propagate")
    for name, limit in (("Start API", 5), ("Verify authenticated production overview", 15)):
        if f"curl --fail --silent --connect-timeout 2 --max-time {limit} " not in steps[name]:
            raise ValueError("HTTP smoke requests need explicit deadlines")
    start = steps["Start API"]
    if "from tools.api_smoke_session import install; install(db); import uvicorn;" not in start:
        raise ValueError("bounded sessions must be installed before app import")
    if text.count("      - 'tools/api_smoke_session.py'") != 2 or text.count("      - 'tests/test_api_smoke_session.py'") != 2:
        raise ValueError("smoke session changes must trigger PR and main checks")
    if "          python -B tests/test_api_smoke_session.py\n" not in text:
        raise ValueError("session regression must run before smoke")
    if "db.CONNECT_ATTEMPTS = 1;" not in start or "workers=1)" not in start:
        raise ValueError("only the CI API process must use one connection attempt")
    if "if (: >/dev/tcp/127.0.0.1/8000) 2>/dev/null; then\n" not in start:
        raise ValueError("readiness must not call a database route")
    if start.count("http://127.0.0.1:8000/healthz") != 1 or "--retry" in start:
        raise ValueError("health must be requested only once")
    if "          set -euo pipefail\n" not in start or "||" in start:
        raise ValueError("health failure must stop the smoke")


class SmokeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_current_workflow(self):
        check_workflow(self.text)

    def test_dangerous_mutations_are_rejected(self):
        mutations = (
            ("install(db); import uvicorn;", "import uvicorn;"),
            ("          python -B tests/test_api_smoke_session.py\n", ""),
            (" && needs.api-static.outputs.allow_production_smoke == 'true'", ""),
            ("allow_production_smoke: ${{ steps.smoke-scope.outputs.allow_smoke }}", "allow_production_smoke: true"),
            (" && needs.api-static.result == 'success'", ""),
            (" && needs.api-static.outputs.production_smoke_scope_status == 'run'", ""),
            ("production_smoke_scope_status: ${{ steps.smoke-scope.outputs.scope_status }}", "production_smoke_scope_status: run"),
            ("        id: smoke-scope\n", "        id: smoke-scope\n        continue-on-error: true\n"),
            ('l1_ci_smoke_scope.py >> "$GITHUB_OUTPUT"\n', 'l1_ci_smoke_scope.py >> "$GITHUB_OUTPUT" || true\n'),
            ("github.event_name == 'push' && github.ref == 'refs/heads/main'", "github.event_name == 'pull_request'"),
            ("    needs: api-static\n", ""),
            ("          VERCEL_ENV: production\n", ""),
            ("20s python -m", "python -m"),
            ("incident_db_probe --connect", "incident_db_probe"),
            ("incident_db_probe --connect\n", "incident_db_probe --connect || true\n"),
            ("      - name: Start API\n", "      - name: Start API\n        if: always()\n"),
            ("--max-time 5", "--max-time 0"),
            ("--max-time 15", "--max-time 0"),
            ("    timeout-minutes: 2\n", "    timeout-minutes: 10\n"),
            ("db.CONNECT_ATTEMPTS = 1;", "db.CONNECT_ATTEMPTS = 3;"),
            ("workers=1)", "workers=2)"),
            ("if (: >/dev/tcp/127.0.0.1/8000) 2>/dev/null; then",
             "if curl --fail http://127.0.0.1:8000/healthz; then"),
            ("/healthz >/tmp/health.json\n", "/healthz >/tmp/health.json || true\n"),
        )
        for old, new in mutations:
            with self.subTest(mutation=old):
                self.assertIn(old, self.text)
                with self.assertRaises(ValueError):
                    check_workflow(self.text.replace(old, new))

    def test_ci_launcher_limits_existing_helper_without_loading_an_app_or_driver(self):
        # Execute only the actual launcher and unchanged helper's AST, with
        # explicit in-memory modules. No client, FastAPI or network is loaded.
        launcher = re.search(r'python -c "([^"\n]*db.CONNECT_ATTEMPTS[^"\n]*)"', self.text).group(1)
        source = ast.parse((WORKFLOW.parents[2] / "bridge_school_api/db.py").read_text(encoding="utf-8"))
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                        and node.name == "_open_connection")
        code = compile(ast.Module(body=[function], type_ignores=[]), "db-helper-fixture", "exec")
        for fail in (False, True):
            with self.subTest(fail=fail):
                db = types.ModuleType("bridge_school_api.db")
                package = types.ModuleType("bridge_school_api")
                package.db = db
                error = type("OperationalError", (Exception,), {})
                connect = MagicMock(side_effect=error("synthetic") if fail else None)
                sleep = MagicMock(side_effect=AssertionError("retry forbidden in smoke"))
                db.__dict__.update(CONNECT_ATTEMPTS=3, INITIAL_RETRY_DELAY_SECONDS=0.25,
                                   database_dsn=lambda: "synthetic-dsn", dict_row=object(),
                                   time=types.SimpleNamespace(sleep=sleep),
                                   psycopg=types.SimpleNamespace(connect=connect, OperationalError=error))
                exec(code, db.__dict__)
                uvicorn = types.ModuleType("uvicorn")
                bounded = types.ModuleType("tools.api_smoke_session")
                bounded.install = MagicMock()
                uvicorn.run = MagicMock(side_effect=lambda *args, **kwargs: db._open_connection())
                with patch.dict(sys.modules, {"bridge_school_api":package,
                                              "bridge_school_api.db":db, "uvicorn":uvicorn,
                                              "tools.api_smoke_session":bounded}):
                    if fail:
                        with self.assertRaises(error):
                            exec(launcher, {})
                    else:
                        exec(launcher, {})
                uvicorn.run.assert_called_once_with("app:app", host="127.0.0.1", port=8000, workers=1)
                bounded.install.assert_called_once_with(db)
                connect.assert_called_once_with("synthetic-dsn", connect_timeout=10,
                                                application_name="bridge-school-api", row_factory=db.dict_row)
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
