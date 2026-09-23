import pytest

from ops.verify_light_broker_1703_release import (
    ARTIFACT, POLICY, PROVENANCE, SOURCE, verified_release,
)

URL = "https://bridge-school-autopilot-abc123-olegmed1-4368s-projects.vercel.app/v1/github/draft-repair"


def health():
    return dict(status="ok", service="school-autopilot-github-token-broker",
                repository="olegmed1-art/bridge-video-free", role_dispatch_mailbox_pr=1703,
                source_revision=SOURCE, artifact_sha256=ARTIFACT, policy_sha256=POLICY,
                provenance_sha256=PROVENANCE, broker_policy_version="physical-no-merge-v2",
                source_attested=True, artifact_attested=True, preview_only=True,
                github_token_broker_enabled=True, bounded_draft_executor_enabled=True,
                bounded_project_head_enabled=True, bounded_role_dispatch_enabled=True,
                production_mutations_enabled=False, raw_installation_token_exposed=False,
                merge_endpoint_enabled=False, ref_update_delete_enabled=False,
                actions_endpoint_enabled=False, deployments_endpoint_enabled=False)


def test_exact_health_emits_pinned_release():
    assert verified_release(health(), URL)["broker_source_sha"] == SOURCE


@pytest.mark.parametrize("key,value", [
    ("role_dispatch_mailbox_pr", 1150), ("role_dispatch_mailbox_pr", 1685),
    ("role_dispatch_mailbox_pr", "1703"), ("artifact_sha256", "0" * 64),
    ("source_revision", "e632334f2d5dc28f8e8ea4892e43d341850c3f11"),
    ("policy_sha256", "0" * 64), ("provenance_sha256", "0" * 64),
    ("preview_only", 1), ("merge_endpoint_enabled", True),
    ("production_mutations_enabled", True), ("bounded_role_dispatch_enabled", False),
])
def test_incompatible_release_rejected(key, value):
    payload = health()
    payload[key] = value
    with pytest.raises(ValueError):
        verified_release(payload, URL)


def test_every_required_field_must_be_present():
    for key in health():
        payload = health()
        del payload[key]
        with pytest.raises(ValueError):
            verified_release(payload, URL)


@pytest.mark.parametrize("url", ["http://example.org", URL + "?x=1", URL.replace(".vercel.app", ".vercel.app.evil")])
def test_untrusted_url_rejected(url):
    with pytest.raises(ValueError):
        verified_release(health(), url)
