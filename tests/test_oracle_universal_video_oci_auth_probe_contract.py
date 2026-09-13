from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-oci-auth-probe.yml"


def test_probe_is_presence_only_and_never_calls_oracle_or_shell():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "github.event_name == 'push'" in text
    assert "OCI_CLI_STANDARD" in text
    assert "OCI_INDIVIDUAL_STANDARD" in text
    assert "OCI_CONFIG_BUNDLE" in text
    assert "OCI_AUTH_STATE" in text
    assert "No OCI API call, SSH login, host command, or service change occurred" in text
    assert "oci compute" not in text
    assert "oci instance-agent" not in text
    assert "ssh " not in text
    assert "curl " not in text
    assert "eval " not in text
    assert "bash -s" not in text


def test_probe_never_prints_secret_values():
    text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = [
        'echo "$OCI_',
        "printf '%s' \"$OCI_",
        'env |',
        'set -x',
        'printenv',
    ]
    for token in forbidden:
        assert token not in text
