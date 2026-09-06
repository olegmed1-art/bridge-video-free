from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/issue-881-readonly-principal-bootstrap.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
COMMAND = "/oracle-ops issue-881-bootstrap-readonly-principal"


def _oci_commands() -> list[str]:
    commands: list[str] = []
    for raw in TEXT.splitlines():
        line = raw.strip()
        if re.match(r"^(request_json .* )?oci ", line):
            commands.append(line.split("oci ", 1)[1])
        elif re.match(r"^timeout .* oci ", line):
            commands.append(line.split("oci ", 1)[1])
        elif "oci " in line.lower() and not line.startswith(("name:", "echo ", "- name:")):
            raise AssertionError(f"unclassified OCI invocation: {line}")
    return commands


def test_positive_exact_owner_command_and_receipt_are_wired() -> None:
    assert TEXT.count(COMMAND) == 2
    assert "github.event.issue.number == 881" in TEXT
    assert "github.event.comment.user.login == 'olegmed1-art'" in TEXT
    assert '[[ "$OWNER_COMMAND" == "$EXACT_COMMAND" ]] || exit 23' in TEXT
    assert "READY_FOR_SECRET_INSTALL" in TEXT


def test_negative_no_cloud_resource_or_host_mutation_surface() -> None:
    commands = _oci_commands()
    assert commands
    forbidden_services = {
        "compute",
        "bv",
        "network",
        "object-storage",
        "os",
        "db",
        "ce",
    }
    assert not any(command.split()[0] in forbidden_services for command in commands)
    assert "instance launch" not in TEXT
    assert "volume create" not in TEXT
    assert "media-canary" not in TEXT
    assert "systemctl" not in TEXT


def test_boundary_iam_mutations_are_exactly_the_required_principal_objects() -> None:
    commands = _oci_commands()
    mutations = [
        command
        for command in commands
        if set(command.split()) & {"create", "add-user", "upload", "update", "delete", "remove-user"}
    ]
    assert len(mutations) == 5
    assert any(command.startswith("iam user create ") for command in mutations)
    assert any(command.startswith("iam group create ") for command in mutations)
    assert any(command.startswith("iam group add-user ") for command in mutations)
    assert any(command.startswith("iam policy create ") for command in mutations)
    assert any(command.startswith("iam user api-key upload ") for command in mutations)
    assert not any(set(command.split()) & {"update", "delete", "remove-user"} for command in mutations)


def test_boundary_policy_is_read_only_and_exact() -> None:
    assert TEXT.count("to inspect compartments in tenancy") == 1
    assert TEXT.count("to inspect instance-family in tenancy") == 1
    assert TEXT.count("to read limits in tenancy") == 1
    for verb in ("manage", "use"):
        assert f"to {verb} " not in TEXT


def test_regression_mutation_credentials_are_scoped_after_owner_gate() -> None:
    bootstrap = TEXT.index("Create dedicated read-only OCI principal")
    exact_gate = TEXT.index("Validate exact owner request before credentials")
    install = TEXT.index("Install pinned OCI CLI without credentials")
    first_secret = TEXT.index("${{ secrets.OCI_CLI_USER }}")
    assert exact_gate < install < bootstrap < first_secret
    assert TEXT.count("${{ secrets.OCI_CLI_") == 5
    assert "secrets.OCI_CLI_" not in TEXT[:bootstrap]
    assert "persist-credentials: false" in TEXT


def test_regression_sensitive_metadata_is_only_published_encrypted() -> None:
    assert "openssl pkeyutl -encrypt" in TEXT
    assert "CIPHERTEXT=v1" in TEXT
    assert '(( ${#value} > 0 && ${#value} <= 190 ))' in TEXT
    assert "encrypted credential metadata" in TEXT
    assert "echo \"ciphertext=$CIPHERTEXT\"" in TEXT
    assert "echo \"user=$USER_ID\"" not in TEXT
    assert "echo \"tenancy=$TENANCY_ID\"" not in TEXT
    assert "echo \"fingerprint=$EXPECTED_FINGERPRINT\"" not in TEXT


def test_regression_preexisting_identity_objects_are_rejected() -> None:
    assert '[[ -z "$EXISTING_USER_ID" && -z "$EXISTING_GROUP_ID" && -z "$EXISTING_POLICY_ID" ]] || exit 24' in TEXT
    assert "Never inherit additive permissions from a reused user or group" in TEXT
    assert "KEY_STATE" not in TEXT
    assert "MEMBER_COUNT" not in TEXT
