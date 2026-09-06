from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/issue-881-paid-preflight-readonly.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
COMMAND = "/oracle-ops issue-881-paid-preflight-readonly"


def _live_oci_commands() -> list[str]:
    commands = []
    for raw in TEXT.splitlines():
        line = raw.strip()
        if line.startswith("request_json ") and " oci " in line:
            commands.append(line.split(" oci ", 1)[1])
        elif re.match(r"^timeout .* oci ", line):
            commands.append(line.split(" oci ", 1)[1])
        elif "oci " in line:
            raise AssertionError(f"unclassified OCI invocation: {line}")
    return commands


def test_positive_exact_owner_command_and_receipt_are_wired() -> None:
    assert TEXT.count(COMMAND) == 2
    assert "github.event.issue.number == 881" in TEXT
    assert "github.event.comment.user.login == 'olegmed1-art'" in TEXT
    assert '[[ "$OWNER_COMMAND" == "$EXACT_COMMAND" ]] || exit 23' in TEXT
    assert "Issue #881 paid preflight read-only audit" in TEXT


def test_negative_live_oci_surface_contains_no_mutation_command() -> None:
    commands = _live_oci_commands()
    assert commands
    forbidden = {
        "create",
        "update",
        "delete",
        "launch",
        "terminate",
        "action",
        "attach",
        "detach",
        "restore",
    }
    assert not any(set(command.split()) & forbidden for command in commands)


def test_boundary_live_oci_surface_is_exactly_the_required_reads() -> None:
    commands = _live_oci_commands()
    assert len(commands) == 6
    assert any(command.startswith("iam region list ") for command in commands)
    assert any(command.startswith("iam compartment list ") for command in commands)
    assert any(command.startswith("compute instance list ") for command in commands)
    assert any(command.startswith("compute shape list ") for command in commands)
    assert sum(command.startswith("limits resource-availability get ") for command in commands) == 2
    assert "compute instance get" not in TEXT


def test_regression_source_instance_is_found_across_active_compartments() -> None:
    assert "--compartment-id-in-subtree true" in TEXT
    assert "--access-level ACCESSIBLE" in TEXT
    assert "'INACTIVE'" in TEXT
    assert "x.get('lifecycle-state') == 'ACTIVE'" in TEXT
    assert "ocid1.compartment." in TEXT
    assert 'for compartment in "${compartments[@]}"; do' in TEXT
    assert '--compartment-id "$compartment" --all' in TEXT
    assert "compute instance list --compartment-id \"$compartment\" --display-name" not in TEXT
    assert "empty rc=0 payload for the server-filtered" in TEXT
    assert "combined['data'].extend(page['data'])" in TEXT
    assert "assert len(matches) == 1" in TEXT
    assert '[[ "$compartment_id" == "$tenancy_id" ]]' not in TEXT
    assert 'grep -Fxq -- "$compartment_id"' not in TEXT
    assert 'for known_compartment in "${compartments[@]}"; do' in TEXT
    assert '[[ "$known_compartment" == "$compartment_id" ]]' in TEXT
    assert "(( compartment_known == 1 ))" in TEXT


def test_regression_rejected_guard_result_is_published_without_launching() -> None:
    assert "[[ \"$guard_rc\" == 0 || \"$guard_rc\" == 2 ]]" in TEXT
    assert "reason=LIVE_INPUT_COLLECTION_FAILED" in TEXT
    assert "python ops/oci_paid_acceptance_guard.py" in TEXT
    assert "OCI resource creation, update, deletion, launch" in TEXT
    assert "oci compute instance launch" not in TEXT


def test_regression_oci_secrets_exist_only_after_exact_authorization() -> None:
    audit = TEXT.split("  audit:\n", 1)[1]
    job_preamble = audit.split("    steps:\n", 1)[0]
    assert "secrets.OCI_" not in job_preamble
    exact_gate = TEXT.index("Validate exact owner request before credentials")
    install = TEXT.index("Install pinned OCI CLI without credentials")
    first_secret = TEXT.index("${{ secrets.OCI_READONLY_CLI_USER }}")
    assert exact_gate < install < first_secret
    assert TEXT.count("${{ secrets.OCI_READONLY_CLI_") == 5
    assert "${{ secrets.OCI_CLI_" not in TEXT
    assert "trap 'rm -f \"$HOME/.oci/config\" \"$HOME/.oci/key.pem\"' EXIT" in TEXT


def test_negative_mutation_capable_principal_is_not_accepted() -> None:
    assert "dedicated read-only credentials required" in TEXT
    assert "host mutation commands: `absent`" in TEXT
    assert "impossible_in_this_workflow" not in TEXT
