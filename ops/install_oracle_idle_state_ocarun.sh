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
readonly FENCE_TARGET='/usr/local/sbin/oracle-idle-stop-fence'
readonly SUDOERS='/etc/sudoers.d/oracle-idle-state-ocarun'
tmp_sudoers="$(mktemp)"
proof='/tmp/oracle-idle-state-install-proof.txt'
trap 'rm -f "$tmp_sudoers" "$SOURCE_FILE" "$proof"' EXIT

install -o root -g root -m 0755 "$SOURCE_FILE" "$TARGET"
cat > "$FENCE_TARGET" <<'FENCE'
#!/usr/bin/env bash
set -Eeuo pipefail
readonly LOCK=/run/lock/oracle-workload-mutation.lock
readonly STATE_DIR=/run/oracle-stop-guard
[[ $# -eq 2 ]] || exit 64
action="$1"; token="$2"
[[ "$token" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || exit 64
proof="$STATE_DIR/$token.proof"
token_file="$STATE_DIR/$token.token"
pid_file="$STATE_DIR/$token.pid"
case "$action" in
  hold)
    install -d -m 0755 "$STATE_DIR"
    rm -f "$proof" "$token_file" "$pid_file"
    exec 9>"$LOCK"
    flock -n 9 || exit 73
    /usr/local/sbin/oracle-idle-state > "$proof"
    printf '%s' "$token" > "$token_file"
    printf '%s' "$$" > "$pid_file"
    sleep 120
    ;;
  read)
    [[ -r "$token_file" && -r "$pid_file" && -r "$proof" ]] || exit 74
    [[ "$(cat "$token_file")" == "$token" ]] || exit 74
    kill -0 "$(cat "$pid_file")" || exit 74
    if flock -n "$LOCK" true; then exit 74; fi
    cat "$proof"
    ;;
  *) exit 64 ;;
esac
FENCE
chown root:root "$FENCE_TARGET"
chmod 0755 "$FENCE_TARGET"
bash -n "$FENCE_TARGET"
cat > "$tmp_sudoers" <<'EOF'
# Exact read-only idle classifier for OCI Run Command. Empty argv is required.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/oracle-idle-state ""
# Bounded token is validated by the root-owned helper; no shell or arbitrary
# command argument is accepted.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/oracle-idle-stop-fence hold *
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/oracle-idle-stop-fence read *
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

test_token="install-test-$$"
sudo -u ocarun sudo -n "$FENCE_TARGET" hold "$test_token" &
fence_pid=$!
for _ in $(seq 1 20); do
  [[ -r "/run/oracle-stop-guard/$test_token.token" ]] && break
  sleep 0.1
done
sudo -u ocarun sudo -n "$FENCE_TARGET" read "$test_token" >/dev/null
kill "$fence_pid"
wait "$fence_pid" 2>/dev/null || true
echo ORACLE_IDLE_STATE_OCARUN_INSTALL_PASS
