#!/usr/bin/env bash
set -Eeuo pipefail

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
: "${SOURCE_FILE:?SOURCE_FILE is required}"
: "${SOURCE_SHA256:?SOURCE_SHA256 is required}"
[[ "$SOURCE_FILE" == /tmp/oracle_idle_state.sh ]] || fail 'unexpected source path'
[[ "$SOURCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid source sha256'
id ocarun >/dev/null 2>&1 || fail 'ocarun user does not exist'
command -v visudo >/dev/null || fail 'visudo is required'
[[ "$(sha256sum "$SOURCE_FILE" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || fail 'source digest mismatch'
bash -n "$SOURCE_FILE"
grep -Fq 'ORACLE_IDLE_STATE=IDLE|BUSY|UNKNOWN' "$SOURCE_FILE" || fail 'unexpected classifier contract'

readonly TARGET='/usr/local/sbin/oracle-idle-state'
readonly SUDOERS='/etc/sudoers.d/oracle-idle-state-ocarun'
tmp_sudoers="$(mktemp)"
proof='/tmp/oracle-idle-state-install-proof.txt'
trap 'rm -f "$tmp_sudoers" "$SOURCE_FILE" "$proof"' EXIT

install -o root -g root -m 0755 "$SOURCE_FILE" "$TARGET"
cat > "$tmp_sudoers" <<'EOF'
# Exact read-only idle classifier for OCI Run Command. Empty argv is required.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/oracle-idle-state ""
EOF
chmod 0440 "$tmp_sudoers"
visudo -cf "$tmp_sudoers" >/dev/null
install -o root -g root -m 0440 "$tmp_sudoers" "$SUDOERS"
visudo -cf /etc/sudoers >/dev/null
! grep -Eq 'NOPASSWD:[[:space:]]*ALL' "$SUDOERS" || fail 'broad sudo grant detected'

sudo -u ocarun sudo -n "$TARGET" >"$proof"
mapfile -t lines < "$proof"
[[ ${#lines[@]} -eq 5 ]] || fail 'classifier line count mismatch'
[[ "${lines[0]}" == 'ORACLE_IDLE_CONTRACT_VERSION=2' ]] || fail 'classifier version mismatch'
[[ "${lines[1]}" =~ ^ORACLE_IDLE_STARTED_AT_EPOCH=[0-9]+$ ]] || fail 'classifier start timestamp invalid'
[[ "${lines[2]}" =~ ^ORACLE_IDLE_OBSERVED_AT_EPOCH=[0-9]+$ ]] || fail 'classifier observation timestamp invalid'
[[ "${lines[3]}" =~ ^ORACLE_IDLE_REASON=[A-Za-z0-9_./,:=+-]+$ ]] || fail 'classifier reason invalid'
[[ "${lines[4]}" =~ ^ORACLE_IDLE_STATE=(IDLE|BUSY|UNKNOWN)$ ]] || fail 'classifier state invalid'
echo ORACLE_IDLE_STATE_OCARUN_INSTALL_PASS
