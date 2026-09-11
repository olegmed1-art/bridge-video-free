from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-autopilot-rollout.yml"
RELEASE = ROOT / "ops/autopilot/broker-release.json"


def test_rollout_is_owner_bounded_and_rollback_safe() -> None:
    workflow = WORKFLOW.read_text()
    assert "github.event.comment.user.login == 'olegmed1-art'" in workflow
    assert "github.event.issue.number == 1131" in workflow
    assert "'/autopilot rollout-failure-continuation-v1'" in workflow
    assert "group: oracle-instance-workload-mutation" not in workflow
    assert "'oracle-instance-workload-mutation'" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "AUTOPILOT_ROLLOUT_QUEUE_BUSY" in workflow
    assert "AUTOPILOT_ROLLOUT_ROLLBACK=PASS" in workflow
    assert "AUTOPILOT_NEW_WORKER_NOT_STABLE" in workflow
    assert "AUTOPILOT_NEW_WORKER_RESTARTED" in workflow
    assert "postgresql://REDACTED" in workflow
    assert "oracle_autopilot.worker_v17" in workflow
    assert "systemd-analyze verify" in workflow
    assert "systemctl disable --now \"$service\"" in workflow
    assert "systemctl enable --now \"$service\"" in workflow
    assert "ORACLE_INSTANCE_STOP_REQUESTED=NO" in workflow
    assert "oci compute instance action" not in workflow
    assert "git reset" not in workflow
    assert "git push" not in workflow


def test_release_provenance_is_self_consistent() -> None:
    release = json.loads(RELEASE.read_text())
    assert set(release) == {
        "schema_version",
        "broker_url",
        "broker_source_sha",
        "broker_artifact_sha256",
        "broker_policy_sha256",
        "broker_policy_version",
        "broker_provenance_sha256",
    }
    assert release["schema_version"] == 1
    assert re.fullmatch(
        r"https://bridge-school-autopilot-[a-z0-9]+-olegmed1-4368s-projects"
        r"\.vercel\.app/v1/github/draft-repair",
        release["broker_url"],
    )
    assert release["broker_policy_version"] == "physical-no-merge-v2"
    assert re.fullmatch(r"[0-9a-f]{40}", release["broker_source_sha"])
    statement = {
        "artifact_sha256": release["broker_artifact_sha256"],
        "policy_sha256": release["broker_policy_sha256"],
        "policy_version": release["broker_policy_version"],
        "source_sha": release["broker_source_sha"],
    }
    expected = hashlib.sha256(
        json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert release["broker_provenance_sha256"] == expected


def test_rollout_never_exposes_or_replaces_protected_credentials() -> None:
    workflow = WORKFLOW.read_text()
    assert "AUTOPILOT_TOKEN_BROKER_SECRET" in workflow
    assert "AUTOPILOT_VERCEL_BYPASS_SECRET" in workflow
    assert "values.update" in workflow
    assert "os.replace(temporary, path)" in workflow
    assert "print(values" not in workflow
    assert "echo $AUTOPILOT" not in workflow
    assert "production migration: not performed by this command" in workflow
