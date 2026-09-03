from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/oracle-idle-guard-exact-install.yml"
OP = ROOT / ".github/workflows/oracle-operator-v2.yml"
INSTALLER = ROOT / "ops/install_oracle_idle_state_ocarun.sh"
TEST = ROOT / "tests/test_oracle_idle_workload_fence.py"
RUNNER = ROOT / ".github/workflows/p0-627-apply-three-p1.yml"
SELF = Path(__file__).resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, got {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# P1: final exact-head attempt must also be created for these contract surfaces.
replace_once(
    WF,
    "      - 'tests/test_universal_video_idle_candidate_contract.py'\n",
    "      - 'tests/test_universal_video_idle_candidate_contract.py'\n"
    "      - 'tests/test_oracle_idle_workload_fence.py'\n"
    "      - '.github/workflows/oracle-operator-v2.yml'\n",
)

replace_once(
    WF,
    "          guard_sha=\"$(sha256sum ops/oracle_idle_state.sh | awk '{print $1}')\"\n"
    "          authorizer_sha=\"$(sha256sum ops/oracle_idle_stop_guard.py | awk '{print $1}')\"\n",
    "          guard_sha=\"$(sha256sum ops/oracle_idle_state.sh | awk '{print $1}')\"\n"
    "          authorizer_sha=\"$(sha256sum ops/oracle_idle_stop_guard.py | awk '{print $1}')\"\n"
    "          installer_sha=\"$(sha256sum ops/install_oracle_idle_state_ocarun.sh | awk '{print $1}')\"\n",
)

old_remote = """            \"set -Eeuo pipefail; chmod 0755 /tmp/install_oracle_idle_state_ocarun.sh; sudo -n env SOURCE_FILE=/tmp/oracle_idle_state.sh SOURCE_SHA256=$guard_sha AUTHORIZER_FILE=/tmp/oracle_idle_stop_guard.py AUTHORIZER_SHA256=$authorizer_sha /tmp/install_oracle_idle_state_ocarun.sh; rm -f /tmp/install_oracle_idle_state_ocarun.sh; printf 'INSTALLED_GUARD_SHA256=%s\\\\n' \\\"\\$(sha256sum /usr/local/sbin/oracle-idle-state | awk '{print \\$1}')\\\"; test \\\"\\$(sha256sum /usr/local/sbin/oracle-idle-state | awk '{print \\$1}')\\\" = '$guard_sha'\"\n"""
new_remote = """            \"sudo -n bash -s -- '$installer_sha' '$guard_sha' '$authorizer_sha'\" <<'REMOTE'\n          set -Eeuo pipefail\n          installer_sha=\"$1\"\n          guard_sha=\"$2\"\n          authorizer_sha=\"$3\"\n          readonly upload='/tmp/install_oracle_idle_state_ocarun.sh'\n          readonly backup_dir='/var/backups/oracle-idle-guard'\n          install -d -o root -g root -m 0700 \"$backup_dir\"\n          [[ -f \"$upload\" && ! -L \"$upload\" ]]\n          trusted_installer=\"$(mktemp --tmpdir=\"$backup_dir\" .oracle-idle-installer.XXXXXX)\"\n          cleanup(){ rm -f \"$trusted_installer\" \"$upload\"; }\n          trap cleanup EXIT\n          install -o root -g root -m 0700 \"$upload\" \"$trusted_installer\"\n          [[ -f \"$trusted_installer\" && ! -L \"$trusted_installer\" ]]\n          [[ \"$(sha256sum \"$trusted_installer\" | awk '{print $1}')\" == \"$installer_sha\" ]]\n          bash -n \"$trusted_installer\"\n          [[ \"$(stat -c '%U:%G:%a' \"$trusted_installer\")\" == 'root:root:700' ]]\n          env SOURCE_FILE=/tmp/oracle_idle_state.sh SOURCE_SHA256=\"$guard_sha\" AUTHORIZER_FILE=/tmp/oracle_idle_stop_guard.py AUTHORIZER_SHA256=\"$authorizer_sha\" \"$trusted_installer\"\n          installed=\"$(sha256sum /usr/local/sbin/oracle-idle-state | awk '{print $1}')\"\n          printf 'INSTALLED_GUARD_SHA256=%s\\n' \"$installed\"\n          [[ \"$installed\" == \"$guard_sha\" ]]\n          REMOTE\n"""
replace_once(WF, old_remote, new_remote)

# P1: root redirections must never use attacker-precreatable fixed /tmp paths.
replace_once(
    INSTALLER,
    "proof='/tmp/oracle-idle-state-install-proof.txt'\n"
    "authorizer_stderr='/tmp/oracle-idle-state-install-authorizer.stderr'\n",
    "proof=\"$(mktemp --tmpdir=\"$BACKUP_DIR\" .oracle-idle-state-install-proof.XXXXXX)\"\n"
    "authorizer_stderr=\"$(mktemp --tmpdir=\"$BACKUP_DIR\" .oracle-idle-state-install-authorizer-stderr.XXXXXX)\"\n"
    "for secure_tmp in \"$proof\" \"$authorizer_stderr\"; do\n"
    "  chmod 0600 \"$secure_tmp\"\n"
    "  [[ -f \"$secure_tmp\" && ! -L \"$secure_tmp\" ]] || fail 'secure transaction temp path invalid'\n"
    "  [[ \"$(stat -c '%U:%G:%a' \"$secure_tmp\")\" == 'root:root:600' ]] || fail 'secure transaction temp ownership/mode invalid'\n"
    "done\n",
)

# P1: remaining Oracle Operator production ingress shares the lifecycle fence.
replace_once(
    OP,
    "concurrency:\n  group: oracle-operator-v2-${{ github.event.issue.number || github.run_id }}\n  cancel-in-progress: false\n",
    "concurrency:\n"
    "  group: >-\n"
    "    ${{ github.event_name == 'pull_request' &&\n"
    "        format('oracle-operator-v2-pr-{0}', github.event.pull_request.number) ||\n"
    "        (github.event_name == 'issue_comment' &&\n"
    "         github.event.comment.user.login == github.repository_owner &&\n"
    "         github.event.comment.body == '/oracle-v2 diagnose-ben') &&\n"
    "        format('oracle-operator-v2-diagnose-{0}', github.run_id) ||\n"
    "        (github.event_name == 'issue_comment' &&\n"
    "         github.event.comment.user.login == github.repository_owner &&\n"
    "         contains(fromJSON('[\"/oracle-v2 rollout-worker\",\"/oracle-v2 rollout-dds3-runtime\",\"/oracle-v2 canary-worlds\",\"/oracle-v2 rollout-ben\",\"/oracle-v2 canary-ben\",\"/oracle-v2 canary-ben-dds3\",\"/oracle-v2 benchmark-ben-100-500\"]'), github.event.comment.body)) &&\n"
    "        'oracle-instance-workload-mutation' ||\n"
    "        format('oracle-operator-v2-noop-{0}', github.run_id) }}\n"
    "  cancel-in-progress: false\n",
)

# Existing ordering test must point at the trusted installer boundary.
replace_once(
    TEST,
    '    remote_install = workflow.index("sudo -n env SOURCE_FILE=", late_auth)\n'
    '    assert third_upload < late_head < late_auth < remote_install\n',
    '    remote_install = workflow.index("trusted_installer=", late_auth)\n'
    '    assert third_upload < late_head < late_auth < remote_install\n',
)

regressions = r'''


def test_exact_installer_upload_is_digest_pinned_and_never_directly_root_executed() -> None:
    workflow = _workflow_text("oracle-idle-guard-exact-install.yml")
    pin = workflow.index("installer_sha=\"$(sha256sum ops/install_oracle_idle_state_ocarun.sh")
    upload = workflow.index(
        'ops/install_oracle_idle_state_ocarun.sh "$ORACLE_USER@$ORACLE_HOST:/tmp/install_oracle_idle_state_ocarun.sh"'
    )
    trusted = workflow.index('trusted_installer="$(mktemp --tmpdir="$backup_dir"')
    verify = workflow.index('== "$installer_sha"', trusted)
    execute = workflow.index('env SOURCE_FILE=/tmp/oracle_idle_state.sh', verify)
    assert pin < upload < trusted < verify < execute
    assert "AUTHORIZER_SHA256=$authorizer_sha /tmp/install_oracle_idle_state_ocarun.sh" not in workflow
    assert "chmod 0755 /tmp/install_oracle_idle_state_ocarun.sh" not in workflow


def test_installer_uses_root_only_transaction_proof_files() -> None:
    installer = (ROOT / "ops" / "install_oracle_idle_state_ocarun.sh").read_text(encoding="utf-8")
    assert "/tmp/oracle-idle-state-install-proof.txt" not in installer
    assert "/tmp/oracle-idle-state-install-authorizer.stderr" not in installer
    assert 'mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-state-install-proof.' in installer
    assert 'mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-state-install-authorizer-stderr.' in installer
    assert "secure transaction temp path invalid" in installer
    assert "secure transaction temp ownership/mode invalid" in installer


def test_oracle_operator_v2_has_exact_effect_to_fence_mapping() -> None:
    events, concurrency = _workflow_contract("oracle-operator-v2.yml")
    assert events == {"pull_request", "issue_comment"}
    expected = (
        "${{ github.event_name == 'pull_request' && "
        "format('oracle-operator-v2-pr-{0}', github.event.pull_request.number) || "
        "(github.event_name == 'issue_comment' && "
        "github.event.comment.user.login == github.repository_owner && "
        "github.event.comment.body == '/oracle-v2 diagnose-ben') && "
        "format('oracle-operator-v2-diagnose-{0}', github.run_id) || "
        "(github.event_name == 'issue_comment' && "
        "github.event.comment.user.login == github.repository_owner && "
        "contains(fromJSON('[\"/oracle-v2 rollout-worker\",\"/oracle-v2 rollout-dds3-runtime\","
        "\"/oracle-v2 canary-worlds\",\"/oracle-v2 rollout-ben\",\"/oracle-v2 canary-ben\","
        "\"/oracle-v2 canary-ben-dds3\",\"/oracle-v2 benchmark-ben-100-500\"]'), "
        "github.event.comment.body)) && 'oracle-instance-workload-mutation' || "
        "format('oracle-operator-v2-noop-{0}', github.run_id) }}"
    )
    actual = " ".join(str(concurrency.get("group", "")).split())
    assert actual == " ".join(expected.split())
    assert concurrency.get("cancel-in-progress") is False
'''

text = TEST.read_text(encoding="utf-8")
marker = "def test_exact_installer_upload_is_digest_pinned_and_never_directly_root_executed()"
if marker in text:
    raise SystemExit("regressions already present")
TEST.write_text(text + regressions, encoding="utf-8")

# Remove all temporary patch machinery from the final commit.
if RUNNER.exists():
    RUNNER.unlink()
SELF.unlink()
