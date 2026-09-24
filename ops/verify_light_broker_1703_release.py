"""Verify an administrator-captured broker health response before pinning it."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SOURCE = "1c5ba7074ab73a861d43503043563c6b7be5e716"
ARTIFACT = "2b4b74a1d5d02138b12f862e4e77e4dc59e550fcab84d5f6dd58855025241d9a"
POLICY = "46aa85fb04b8221edbd2341ce26d30eb1f1648d11e8726cd18eb4067eb981aaa"
PROVENANCE = "441e25b065f31159d1f9b1f33e50fd7545933102917109b4cf94df1789e9998b"


def verified_release(health: object, broker_url: str) -> dict[str, object]:
    if not re.fullmatch(
        r"https://bridge-school-autopilot-[a-z0-9]+-olegmed1-4368s-projects"
        r"\.vercel\.app/v1/github/draft-repair", broker_url
    ):
        raise ValueError("BROKER_URL_INVALID")
    expected = {
        "status": "ok", "service": "school-autopilot-github-token-broker",
        "repository": "olegmed1-art/bridge-video-free",
        "role_dispatch_mailbox_pr": 1703,
        "source_revision": SOURCE, "artifact_sha256": ARTIFACT,
        "policy_sha256": POLICY, "provenance_sha256": PROVENANCE,
        "broker_policy_version": "physical-no-merge-v2",
        "source_attested": True, "artifact_attested": True, "preview_only": True,
        "github_token_broker_enabled": True, "bounded_draft_executor_enabled": True,
        "bounded_project_head_enabled": True, "bounded_role_dispatch_enabled": True,
        "production_mutations_enabled": False, "raw_installation_token_exposed": False,
        "merge_endpoint_enabled": False, "ref_update_delete_enabled": False,
        "actions_endpoint_enabled": False, "deployments_endpoint_enabled": False,
    }
    if not isinstance(health, dict) or any(
        type(health.get(k)) is not type(v) or health.get(k) != v
        for k, v in expected.items()
    ):
        raise ValueError("BROKER_HEALTH_INCOMPATIBLE")
    statement = {"source_sha": SOURCE, "artifact_sha256": ARTIFACT,
                 "policy_sha256": POLICY, "policy_version": "physical-no-merge-v2"}
    digest = hashlib.sha256(json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != PROVENANCE:
        raise ValueError("BROKER_PROVENANCE_INVALID")
    return {"schema_version": 1, "broker_url": broker_url,
            "broker_source_sha": SOURCE, "broker_artifact_sha256": ARTIFACT,
            "broker_policy_sha256": POLICY, "broker_policy_version": "physical-no-merge-v2",
            "broker_provenance_sha256": PROVENANCE}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-json", type=Path, required=True)
    parser.add_argument("--broker-url", required=True)
    args = parser.parse_args()
    try:
        result = verified_release(json.loads(args.health_json.read_text()), args.broker_url)
    except (OSError, ValueError):
        parser.exit(1, "BROKER_RELEASE_VERIFICATION_FAILED\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
