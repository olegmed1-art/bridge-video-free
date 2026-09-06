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
        if line.startswith("#"):
            continue
        if re.match(r"^(request_json .* |retry_delete )?oci ", line):
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
    assert len(mutations) == 10
    assert any(command.startswith("iam user create ") for command in mutations)
    assert any(command.startswith("iam group create ") for command in mutations)
    assert any(command.startswith("iam group add-user ") for command in mutations)
    assert any(command.startswith("iam policy create ") for command in mutations)
    assert any(command.startswith("iam user api-key upload ") for command in mutations)
    assert any(command.startswith("iam user api-key delete ") for command in mutations)
    assert any(command.startswith("iam group remove-user ") for command in mutations)
    assert any(command.startswith("iam policy delete ") for command in mutations)
    assert any(command.startswith("iam group delete ") for command in mutations)
    assert any(command.startswith("iam user delete ") for command in mutations)
    assert not any(" update " in f" {command} " for command in mutations)


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
    assert "matches = [x for x in rows if x.get('name') == sys.argv[2] and" not in TEXT
    assert 'NAME_STAMP="issue-881-paid-preflight-readonly-${GITHUB_RUN_ID}"' in TEXT


def test_regression_partial_iam_creation_is_rolled_back_in_dependency_order() -> None:
    cleanup = TEXT.index("cleanup()")
    key = TEXT.index("iam user api-key delete", cleanup)
    membership = TEXT.index("iam group remove-user", cleanup)
    policy = TEXT.index("iam policy delete", cleanup)
    group = TEXT.index("iam group delete", cleanup)
    user = TEXT.index("iam user delete", cleanup)
    assert cleanup < key < membership < policy < group < user
    assert "trap cleanup EXIT" in TEXT
    assert "ROLLBACK_INCOMPLETE" in TEXT
    assert "failed-step IAM rollback" in TEXT
    assert "GITHUB_STEP_SUMMARY" in TEXT


def test_regression_ambiguous_creates_are_reconciled_inside_global_deadline() -> None:
    assert "timeout-minutes: 30" in TEXT
    assert "Reserve job-wide cleanup and receipt deadline before setup" in TEXT
    assert "JOB_DEADLINE_EPOCH=$job_deadline" in TEXT
    assert "MUTATION_CUTOFF_EPOCH=$(( job_deadline - 360 ))" in TEXT
    assert "CLEANUP_DEADLINE_EPOCH=$(( now + 240 ))" in TEXT
    assert "JOB_DEADLINE_EPOCH - 120" in TEXT
    assert 'remaining=$(( CLEANUP_DEADLINE_EPOCH - now ))' in TEXT
    cutoff = TEXT.index('(( $(date +%s) <= MUTATION_CUTOFF_EPOCH )) || exit 25')
    first_mutation = TEXT.index('oci iam user create', TEXT.index("exit 24"))
    assert cutoff < first_mutation
    for marker, command in (
        ("USER_CREATED=1", 'oci iam user create'),
        ("GROUP_CREATED=1", 'oci iam group create'),
        ("MEMBERSHIP_CREATED=1", 'oci iam group add-user'),
        ("POLICY_CREATED=1", 'oci iam policy create'),
        ("KEY_CREATED=1", 'oci iam user api-key upload'),
    ):
        assert TEXT.index(marker, TEXT.index("exit 24")) < TEXT.index(command, TEXT.index("exit 24"))
    assert "Recover only this run's exact stamped IDs before rollback" in TEXT
    assert "Three repeated exact-stamp inventories are authoritative" in TEXT
    assert 'for pass in 1 2 3; do' in TEXT
