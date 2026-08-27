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
grep -Fq 'ORACLE_IDLE_STATE=IDLE' "$SOURCE_FILE" || fail 'unexpected classifier contract'

readonly TARGET='/usr/local/sbin/oracle-idle-state'
readonly SUDOERS='/etc/sudoers.d/oracle-idle-state-ocarun'
tmp_sudoers="$(mktemp)"
trap 'rm -f "$tmp_sudoers" "$SOURCE_FILE"' EXIT

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

sudo -u ocarun sudo -n "$TARGET" >/tmp/oracle-idle-state-install-proof.txt
grep -Eq '^ORACLE_IDLE_STATE=(IDLE|BUSY|UNKNOWN)$' /tmp/oracle-idle-state-install-proof.txt
rm -f /tmp/oracle-idle-state-install-proof.txt
echo ORACLE_IDLE_STATE_OCARUN_INSTALL_PASS
