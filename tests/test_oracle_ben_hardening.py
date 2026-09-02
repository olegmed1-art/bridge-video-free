from pathlib import Path


PINNED_BEN_IMAGE = (
    "ghcr.io/lorserker/ben@sha256:"
    "474065d99c796a54e32cab4a83dee09685749cff2fbde5edc84ab2b961ba514d"
)
BEN_WORKFLOWS = (
    ".github/workflows/oracle-operator-v2.yml",
    ".github/workflows/oracle-ben-runtime-rollout.yml",
    ".github/workflows/oracle-operator-commands.yml",
    ".github/workflows/oracle-operator-v3.yml",
    ".github/workflows/bridge-ai-e2e-ben.yml",
    ".github/workflows/bridge-ai-queue-worker.yml",
)
ORACLE_WORKFLOWS = BEN_WORKFLOWS[:4]


def test_ben_workflows_use_one_reviewed_immutable_image():
    for path in BEN_WORKFLOWS:
        text = Path(path).read_text(encoding="utf-8")
        assert PINNED_BEN_IMAGE in text
        assert "ghcr.io/lorserker/ben:latest" not in text


def test_oracle_workflows_share_exact_host_key_and_runtime_installers():
    for path in ORACLE_WORKFLOWS:
        text = Path(path).read_text(encoding="utf-8")
        assert "ops/oracle_known_hosts_from_scan.sh" in text
        assert "ops/install_ben_runtime.sh" in text
        assert 'python - "$raw"' not in text


def test_ben_runtime_is_resource_bounded_and_has_watchdog():
    text = Path("ops/install_ben_runtime.sh").read_text(encoding="utf-8")
    for required in (
        "--pull=never",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--pids-limit=256",
        "--memory=6g",
        "--tmpfs /tmp:rw,nosuid,nodev,noexec,size=1g",
        "--tmpfs /logs:rw,nosuid,nodev,noexec,size=64m",
        "--tmpfs /app/logs:rw,nosuid,nodev,noexec,size=64m",
        "failed BEN rollout diagnostics (before rollback)",
        "mounts={{json .Mounts}}",
        "127.0.0.1:8085:8085",
        "bridge-ben-healthcheck.timer",
    ):
        assert required in text
    assert "systemctl enable bridge-ben.service\\nsystemctl restart bridge-ben.service" in text
    assert "systemctl enable --now bridge-ben.service" not in text


def test_dds3_runtime_rollout_is_commit_pinned_and_rollback_safe():
    text = Path("ops/install_dds3_runtime.sh").read_text(encoding="utf-8")
    assert '[[ "$target_ref" =~ ^[0-9a-f]{40}$ ]]' in text
    assert "bridge-school-dds3-runtime:rollback-last" in text
    assert "trap rollback ERR" in text
    assert "deal_pbn_sha256" in text
    assert "dds3-healthcheck.timer" in text
    assert "docker restart bridge-school-dds3-runtime" in text
    workflow = Path(".github/workflows/oracle-operator-v2.yml").read_text(encoding="utf-8")
    assert "/oracle-v2 rollout-dds3-runtime" in workflow
    assert "ops/install_dds3_runtime.sh" in workflow


def test_known_hosts_builder_publishes_only_matching_fingerprints():
    text = Path("ops/oracle_known_hosts_from_scan.sh").read_text(encoding="utf-8")
    assert '[[ "$fingerprint" == "$expected_fingerprint" ]]' in text
    assert 'printf \'%s\\n\' "$line" >> "$output"' in text
    assert "unverified Oracle SSH key reached known_hosts" in text


def test_every_oracle_ssh_workflow_uses_the_filtered_known_hosts_builder():
    for path in Path(".github/workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        if "UserKnownHostsFile" in text:
            assert "ops/oracle_known_hosts_from_scan.sh" in text, path
