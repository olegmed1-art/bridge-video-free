#!/usr/bin/env python3
from pathlib import Path
import re


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{path}: expected one replacement, got {n}')
    p.write_text(text.replace(old, new, 1), encoding='utf-8')


# 1) Guard installation must serialize with STOP.
replace_once(
    '.github/workflows/oracle-idle-guard-exact-install.yml',
    'concurrency:\n  group: oracle-idle-guard-exact-install-627\n  cancel-in-progress: false',
    'concurrency:\n  # Guard promotion is a lifecycle mutation and serializes with STOP.\n  group: oracle-instance-workload-mutation\n  cancel-in-progress: false',
)

# 2) Recheck exact head + owner authorization after all three SCP uploads,
# immediately before invoking the privileged remote installer.
p = Path('.github/workflows/oracle-idle-guard-exact-install.yml')
text = p.read_text(encoding='utf-8')
needle = '''          scp -i '${{ steps.ssh.outputs.key }}' -o BatchMode=yes -o IdentitiesOnly=yes \\
            -o StrictHostKeyChecking=yes -o UserKnownHostsFile='${{ steps.ssh.outputs.known }}' \\
            ops/install_oracle_idle_state_ocarun.sh "$ORACLE_USER@$ORACLE_HOST:/tmp/install_oracle_idle_state_ocarun.sh"\n          ssh -i '${{ steps.ssh.outputs.key }}' -o BatchMode=yes -o IdentitiesOnly=yes \\
'''
late_gate = '''          scp -i '${{ steps.ssh.outputs.key }}' -o BatchMode=yes -o IdentitiesOnly=yes \\
            -o StrictHostKeyChecking=yes -o UserKnownHostsFile='${{ steps.ssh.outputs.known }}' \\
            ops/install_oracle_idle_state_ocarun.sh "$ORACLE_USER@$ORACLE_HOST:/tmp/install_oracle_idle_state_ocarun.sh"\n          # Actual mutation boundary: transfers are complete, but no privileged\n          # installer code has run. Re-read both primary sources now.\n          current="$(gh api --method GET "repos/$GITHUB_REPOSITORY/pulls/1061" --jq '.head.sha')"\n          [[ "$current" == "$GITHUB_SHA" ]] || {\n            echo "INSTALL_FINAL_HEAD_MOVED expected=$GITHUB_SHA actual=$current" >&2\n            exit 70\n          }\n          command="/oracle-idle-guard-install-reviewed $GITHUB_SHA"\n          authorized="$(\n            gh api --method GET --paginate --slurp \\
              "repos/$GITHUB_REPOSITORY/issues/1061/comments?per_page=100" \\
            | jq --arg command "$command" \\
              '[.[][] | select(.user.login == "olegmed1-art" and .body == $command)] | length'\n          )"\n          [[ "$authorized" =~ ^[1-9][0-9]*$ ]] || {\n            echo "INSTALL_FINAL_AUTHORIZATION_MISSING exact_sha=$GITHUB_SHA" >&2\n            exit 71\n          }\n          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"\n          echo "INSTALL_FINAL_HEAD_RECHECK=PASS sha=$GITHUB_SHA"\n          echo "INSTALL_FINAL_AUTHORIZATION=PASS sha=$GITHUB_SHA"\n          ssh -i '${{ steps.ssh.outputs.key }}' -o BatchMode=yes -o IdentitiesOnly=yes \\
'''
if text.count(needle) != 1:
    raise SystemExit(f'install workflow: SCP/SSH boundary marker count={text.count(needle)}')
text = text.replace(needle, late_gate, 1)
p.write_text(text, encoding='utf-8')

# 3) Root-owned, digest-verified trusted copies for candidate + authorizer.
p = Path('ops/install_oracle_idle_state_ocarun.sh')
text = p.read_text(encoding='utf-8')
replace_map = [
    (
        "sudoers_restore_probe=''\nproof='/tmp/oracle-idle-state-install-proof.txt'",
        "sudoers_restore_probe=''\ntrusted_source=''\ntrusted_authorizer=''\nproof='/tmp/oracle-idle-state-install-proof.txt'",
    ),
    (
        'trap cleanup_and_rollback EXIT\n\nif [[ -e "$BACKUP" || -L "$BACKUP" ]]; then',
        '''trap cleanup_and_rollback EXIT\n\n# Freeze untrusted /tmp inputs into root-only copies before either is used for\n# promotion or execution. A concurrent writer can only make the digest check fail.\ntrusted_source="$(mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-state.source.XXXXXX)"\ntrusted_authorizer="$(mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-authorizer.source.XXXXXX)"\ninstall -o root -g root -m 0700 "$SOURCE_FILE" "$trusted_source"\ninstall -o root -g root -m 0700 "$AUTHORIZER_FILE" "$trusted_authorizer"\n[[ "$(sha256sum "$trusted_source" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || fail 'trusted source digest mismatch'\n[[ "$(sha256sum "$trusted_authorizer" | awk '{print $1}')" == "$AUTHORIZER_SHA256" ]] || fail 'trusted authorizer digest mismatch'\nbash -n "$trusted_source"\npython3 - "$trusted_authorizer" <<'PYAUTH'\nimport ast, pathlib, sys\nast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), filename=sys.argv[1])\nPYAUTH\n[[ "$(stat -c '%U:%G:%a' "$trusted_source")" == 'root:root:700' ]] || fail 'trusted source ownership/mode invalid'\n[[ "$(stat -c '%U:%G:%a' "$trusted_authorizer")" == 'root:root:700' ]] || fail 'trusted authorizer ownership/mode invalid'\n\nif [[ -e "$BACKUP" || -L "$BACKUP" ]]; then''',
    ),
    ('install -o root -g root -m 0755 "$SOURCE_FILE" "$tmp_target"', 'install -o root -g root -m 0755 "$trusted_source" "$tmp_target"'),
    ('authorization="$(python3 "$AUTHORIZER_FILE" --proof "$proof"', 'authorization="$(python3 "$trusted_authorizer" --proof "$proof"'),
    ('authorizer_sha_readback="$(sha256sum "$AUTHORIZER_FILE" | awk \'{print $1}\')"', 'authorizer_sha_readback="$(sha256sum "$trusted_authorizer" | awk \'{print $1}\')"'),
]
for old, new in replace_map:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'installer replacement count={n} for {old[:50]!r}')
    text = text.replace(old, new, 1)
cleanup_old = '''    "$restore_probe" "$sudoers_restore_probe" "$SOURCE_FILE" "$AUTHORIZER_FILE" \\
    "$proof" "$authorizer_stderr"'''
cleanup_new = '''    "$restore_probe" "$sudoers_restore_probe" "$SOURCE_FILE" "$AUTHORIZER_FILE" \\
    "$trusted_source" "$trusted_authorizer" "$proof" "$authorizer_stderr"'''
if text.count(cleanup_old) != 1:
    raise SystemExit('installer cleanup marker missing')
text = text.replace(cleanup_old, cleanup_new, 1)
p.write_text(text, encoding='utf-8')

# 4) Preserve confirmed BUSY in instance-power outputs; also route status
# requests (dispatch + comment) out of the production mutation fence.
p = Path('.github/workflows/oracle-instance-power.yml')
text = p.read_text(encoding='utf-8')
old_group = '''  group: ${{ github.event_name == 'workflow_dispatch' && 'oracle-instance-workload-mutation' || (github.event_name == 'issue_comment' && github.actor == github.repository_owner && contains(fromJSON('["/oracle-instance status","/oracle-instance start","/oracle-instance stop"]'), github.event.comment.body)) && 'oracle-instance-workload-mutation' || format('oracle-instance-power-noop-{0}', github.run_id) }}'''
new_group = '''  group: ${{ (github.event_name == 'workflow_dispatch' && inputs.action == 'status') && format('oracle-instance-status-{0}', github.run_id) || (github.event_name == 'issue_comment' && github.actor == github.repository_owner && github.event.comment.body == '/oracle-instance status') && format('oracle-instance-status-{0}', github.run_id) || ((github.event_name == 'workflow_dispatch' && contains(fromJSON('["start","stop"]'), inputs.action)) || (github.event_name == 'issue_comment' && github.actor == github.repository_owner && contains(fromJSON('["/oracle-instance start","/oracle-instance stop"]'), github.event.comment.body))) && 'oracle-instance-workload-mutation' || format('oracle-instance-power-noop-{0}', github.run_id) }}'''
if text.count(old_group) != 1:
    raise SystemExit('instance-power concurrency expression missing')
text = text.replace(old_group, new_group, 1)
old_auth = '''          expected_authorization=$'ORACLE_STOP_AUTHORIZED=YES\\nORACLE_STOP_AUTHORIZATION_REASON=fresh_exact_idle'\n          if [[ $authorization_rc -eq 0 && "$authorization" == "$expected_authorization" ]]; then\n            stop_authorized=YES\n            idle_state=IDLE\n            idle_reason=fresh_exact_idle\n          else\n            stop_authorized=NO\n            idle_state=UNKNOWN\n            idle_reason=exact_idle_authorization_absent\n          fi\n'''
new_auth = '''          expected_authorization=$'ORACLE_STOP_AUTHORIZED=YES\\nORACLE_STOP_AUTHORIZATION_REASON=fresh_exact_idle'\n          busy_refusal=$'ORACLE_STOP_AUTHORIZED=NO\\nORACLE_STOP_AUTHORIZATION_REASON=state_busy_forbids_stop'\n          if [[ $authorization_rc -eq 0 && "$authorization" == "$expected_authorization" ]]; then\n            stop_authorized=YES\n            idle_state=IDLE\n            idle_reason=fresh_exact_idle\n          elif [[ $authorization_rc -ne 0 && "$authorization" == "$busy_refusal" ]]; then\n            stop_authorized=NO\n            idle_state=BUSY\n            idle_reason="$(sed -n 's/^ORACLE_IDLE_REASON=//p' "$proof")"\n            [[ -n "$idle_reason" ]] || idle_reason=confirmed_busy\n          else\n            stop_authorized=NO\n            idle_state=UNKNOWN\n            idle_reason=exact_idle_authorization_absent\n          fi\n'''
if text.count(old_auth) != 1:
    raise SystemExit('instance-power authorization mapping missing')
text = text.replace(old_auth, new_auth, 1)
p.write_text(text, encoding='utf-8')

# 5) DDS3 status is read-only and must not replace a pending mutation.
p = Path('.github/workflows/oracle-dds3-pilot10k-operator.yml')
text = p.read_text(encoding='utf-8')
lines = text.splitlines()
matching = [i for i, line in enumerate(lines) if line.startswith('  group: ${{') and 'oracle-dds3-pilot10k-operator-noop' in line]
if len(matching) != 1:
    raise SystemExit(f'DDS3 group line count={len(matching)}')
lines[matching[0]] = '''  group: ${{ github.event_name == 'issue_comment' && github.event.comment.user.login == github.repository_owner && github.event.comment.body == '/dds3-pilot10k status' && format('oracle-dds3-pilot10k-status-{0}', github.run_id) || (github.event_name == 'issue_comment' && github.event.comment.user.login == github.repository_owner && contains(fromJSON('["/dds3-pilot10k start","/dds3-main30k deploy","/dds3-main30k stage","/dds3-main30k start","/dds3-main30k reconcile"]'), github.event.comment.body)) && 'oracle-instance-workload-mutation' || format('oracle-dds3-pilot10k-operator-noop-{0}', github.run_id) }}'''
p.write_text('\n'.join(lines) + '\n', encoding='utf-8')

# 6) Update structural regression expressions and add transaction-order tests.
p = Path('tests/test_oracle_idle_workload_fence.py')
text = p.read_text(encoding='utf-8')
power_start = text.index('POWER_GROUP = (')
power_end = text.index('\n\nMASS_LAUNCH_GROUP', power_start)
new_power = '''POWER_GROUP = (\n    "${{ (github.event_name == 'workflow_dispatch' && inputs.action == 'status') && "\n    "format('oracle-instance-status-{0}', github.run_id) || (github.event_name == "\n    "'issue_comment' && github.actor == github.repository_owner && "\n    "github.event.comment.body == '/oracle-instance status') && "\n    "format('oracle-instance-status-{0}', github.run_id) || "\n    "((github.event_name == 'workflow_dispatch' && "\n    "contains(fromJSON('[\\\"start\\\",\\\"stop\\\"]'), inputs.action)) || "\n    "(github.event_name == 'issue_comment' && github.actor == github.repository_owner && "\n    "contains(fromJSON('[\\\"/oracle-instance start\\\",\\\"/oracle-instance stop\\\"]'), "\n    "github.event.comment.body))) && 'oracle-instance-workload-mutation' || "\n    "format('oracle-instance-power-noop-{0}', github.run_id) }}"\n)'''
text = text[:power_start] + new_power + text[power_end:]
mass_start = text.index('MASS_OPERATOR_GROUP = (')
mass_end = text.index('\n\nEXPECTED_PRODUCERS', mass_start)
new_mass = '''MASS_OPERATOR_GROUP = (\n    "${{ github.event_name == 'issue_comment' && "\n    "github.event.comment.user.login == github.repository_owner && "\n    "github.event.comment.body == '/dds3-pilot10k status' && "\n    "format('oracle-dds3-pilot10k-status-{0}', github.run_id) || "\n    "(github.event_name == 'issue_comment' && "\n    "github.event.comment.user.login == github.repository_owner && "\n    "contains(fromJSON('[\\\"/dds3-pilot10k start\\\",\\\"/dds3-main30k deploy\\\","\n    "\\\"/dds3-main30k stage\\\",\\\"/dds3-main30k start\\\","\n    "\\\"/dds3-main30k reconcile\\\"]'), github.event.comment.body)) && "\n    "'oracle-instance-workload-mutation' || "\n    "format('oracle-dds3-pilot10k-operator-noop-{0}', github.run_id) }}"\n)'''
text = text[:mass_start] + new_mass + text[mass_end:]
if 'test_guard_install_serializes_with_stop_and_gates_after_uploads' not in text:
    text += '''\n\ndef test_guard_install_serializes_with_stop_and_gates_after_uploads() -> None:\n    workflow = _workflow_text("oracle-idle-guard-exact-install.yml")\n    _, concurrency = _workflow_contract("oracle-idle-guard-exact-install.yml")\n    assert concurrency == {"group": SHARED_FENCE, "cancel-in-progress": False}\n    third_upload = workflow.index("ops/install_oracle_idle_state_ocarun.sh")\n    late_head = workflow.index("INSTALL_FINAL_HEAD_MOVED", third_upload)\n    late_auth = workflow.index("INSTALL_FINAL_AUTHORIZATION_MISSING", late_head)\n    remote_install = workflow.index("sudo -n env SOURCE_FILE=", late_auth)\n    assert third_upload < late_head < late_auth < remote_install\n\n\ndef test_installer_executes_verified_root_owned_authorizer_copy() -> None:\n    workflow = (ROOT / "ops" / "install_oracle_idle_state_ocarun.sh").read_text(encoding="utf-8")\n    copy = workflow.index("trusted_authorizer=\")\n    verify = workflow.index("trusted authorizer digest mismatch", copy)\n    mode = workflow.index("trusted authorizer ownership/mode invalid", verify)\n    execute = workflow.index('python3 "$trusted_authorizer" --proof', mode)\n    assert copy < verify < mode < execute\n    assert 'python3 "$AUTHORIZER_FILE" --proof' not in workflow\n\n\ndef test_instance_power_preserves_confirmed_busy_state() -> None:\n    workflow = _workflow_text("oracle-instance-power.yml")\n    busy = workflow.index("state_busy_forbids_stop")\n    no = workflow.index("stop_authorized=NO", busy)\n    mapped = workflow.index("idle_state=BUSY", no)\n    assert busy < no < mapped\n'''
p.write_text(text, encoding='utf-8')

print('PATCH_OK')
