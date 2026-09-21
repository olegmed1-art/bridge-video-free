"""Every candidate head needs real evidence for the external pre-canary gate."""

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "filename",
    [
        "issue-881-contract-ci.yml",
        "issue-881-precanary-evidence.yml",
        "issue-881-current-main-authoritative-ci.yml",
    ],
)
def test_required_workflows_produce_evidence_for_every_candidate_head(filename):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
    # PyYAML's YAML 1.1 loader treats the unquoted GitHub key `on` as True.
    events = workflow.get("on", workflow.get(True))
    assert "pull_request" in events
    assert events["pull_request"] in (None, {})
    runner = (ROOT / "ops/issue_881_external_precanary_workflow.sh").read_text()
    required = runner.split("required_workflows=(", 1)[1].split(")", 1)[0]
    assert workflow["name"] in required
