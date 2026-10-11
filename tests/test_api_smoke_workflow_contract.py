"""Static safety regression for the existing main-only database smoke job.

This checks the repository's fixed workflow layout, without a YAML dependency.
It does not execute the workflow or claim a live database connection.
"""
from pathlib import Path
import re
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/bridge-school-api-ci.yml"


def check_workflow(text: str) -> None:
    job_match = re.search(r"^  production-smoke:\n(.*?)(?=^  [\w-]+:|\Z)", text, re.M | re.S)
    if not job_match:
        raise ValueError("missing production smoke job")
    job = job_match.group(1)
    if "    if: github.event_name == 'push' && github.ref == 'refs/heads/main'\n" not in job:
        raise ValueError("production smoke must be restricted to main push")
    if "    needs: api-static\n" not in job:
        raise ValueError("production smoke must wait for static checks")
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


class SmokeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_current_workflow(self):
        check_workflow(self.text)

    def test_dangerous_mutations_are_rejected(self):
        mutations = (
            ("github.event_name == 'push' && github.ref == 'refs/heads/main'", "github.event_name == 'pull_request'"),
            ("    needs: api-static\n", ""),
            ("          VERCEL_ENV: production\n", ""),
            ("20s python -m", "python -m"),
            ("incident_db_probe --connect", "incident_db_probe"),
            ("incident_db_probe --connect\n", "incident_db_probe --connect || true\n"),
            ("      - name: Start API\n", "      - name: Start API\n        if: always()\n"),
            ("--max-time 5", "--max-time 0"),
            ("--max-time 15", "--max-time 0"),
        )
        for old, new in mutations:
            with self.subTest(mutation=old):
                self.assertIn(old, self.text)
                with self.assertRaises(ValueError):
                    check_workflow(self.text.replace(old, new))


if __name__ == "__main__":
    unittest.main()
