#!/usr/bin/env bash
set -Eeuo pipefail

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
: "${SOURCE_FILE:?SOURCE_FILE is required}"
: "${SOURCE_SHA256:?SOURCE_SHA256 is required}"
: "${AUTHORIZER_FILE:?AUTHORIZER_FILE is required}"
: "${AUTHORIZER_SHA256:?AUTHORIZER_SHA256 is required}"
[[ "$SOURCE_FILE" == /tmp/oracle_idle_state.sh ]] || fail 'unexpected source path'
[[ "$AUTHORIZER_FILE" == /tmp/oracle_idle_stop_guard.py ]] || fail 'unexpected authorizer path'
[[ "$SOURCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid source sha256'
[[ "$AUTHORIZER_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid authorizer sha256'
id ocarun >/dev/null 2>&1 || fail 'ocarun user does not exist'
command -v visudo >/dev/null || fail 'visudo is required'
command -v python3 >/dev/null || fail 'python3 is required'
[[ "$(sha256sum "$SOURCE_FILE" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || fail 'source digest mismatch'
[[ "$(sha256sum "$AUTHORIZER_FILE" | awk '{print $1}')" == "$AUTHORIZER_SHA256" ]] || fail 'authorizer digest mismatch'
bash -n "$SOURCE_FILE"
python3 - "$AUTHORIZER_FILE" <<'PY'
import ast, pathlib, sys
ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), filename=sys.argv[1])
PY
grep -Fq 'ORACLE_IDLE_STATE=IDLE|BUSY|UNKNOWN' "$SOURCE_FILE" || fail 'unexpected classifier contract'

readonly TARGET='/usr/local/sbin/oracle-idle-state'
readonly FENCE_TARGET='/usr/local/sbin/oracle-idle-stop-fence'
readonly SUDOERS='/etc/sudoers.d/oracle-idle-state-ocarun'
readonly BACKUP_DIR='/var/backups/oracle-idle-guard'
[[ -f "$TARGET" && ! -L "$TARGET" ]] || fail 'existing guard required for recoverable backup'
old_sha="$(sha256sum "$TARGET" | awk '{print $1}')"
[[ "$old_sha" =~ ^[0-9a-f]{64}$ ]] || fail 'existing guard digest invalid'
readonly BACKUP="$BACKUP_DIR/oracle-idle-state-${old_sha}"
old_sudoers_present=0
old_sudoers_sha=''
SUDOERS_BACKUP=''
old_fence_present=0
old_fence_sha=''
FENCE_BACKUP=''

install -d -o root -g root -m 0700 "$BACKUP_DIR"
if [[ -e "$SUDOERS" || -L "$SUDOERS" ]]; then
  [[ -f "$SUDOERS" && ! -L "$SUDOERS" ]] || fail 'existing sudoers path is unsafe'
  [[ "$(stat -c '%U:%G:%a' "$SUDOERS")" == 'root:root:440' ]] || fail 'existing sudoers ownership/mode is unsafe'
  visudo -cf "$SUDOERS" >/dev/null || fail 'existing sudoers syntax invalid'
  old_sudoers_present=1
  old_sudoers_sha="$(sha256sum "$SUDOERS" | awk '{print $1}')"
  [[ "$old_sudoers_sha" =~ ^[0-9a-f]{64}$ ]] || fail 'existing sudoers digest invalid'
  SUDOERS_BACKUP="$BACKUP_DIR/oracle-idle-state-ocarun-sudoers-${old_sudoers_sha}"
fi
if [[ -e "$FENCE_TARGET" || -L "$FENCE_TARGET" ]]; then
  [[ -f "$FENCE_TARGET" && ! -L "$FENCE_TARGET" ]] || fail 'existing fence helper path is unsafe'
  [[ "$(stat -c '%U:%G:%a' "$FENCE_TARGET")" == 'root:root:755' ]] || fail 'existing fence helper ownership/mode is unsafe'
  bash -n "$FENCE_TARGET"
  old_fence_present=1
  old_fence_sha="$(sha256sum "$FENCE_TARGET" | awk '{print $1}')"
  [[ "$old_fence_sha" =~ ^[0-9a-f]{64}$ ]] || fail 'existing fence helper digest invalid'
  FENCE_BACKUP="$BACKUP_DIR/oracle-idle-stop-fence-$old_fence_sha"
  if [[ ! -e "$FENCE_BACKUP" ]]; then
    install -o root -g root -m 0755 "$FENCE_TARGET" "$FENCE_BACKUP"
  fi
  [[ -f "$FENCE_BACKUP" && ! -L "$FENCE_BACKUP" ]] || fail 'fence rollback backup path is unsafe'
  [[ "$(sha256sum "$FENCE_BACKUP" | awk '{print $1}')" == "$old_fence_sha" ]] || fail 'fence rollback backup digest mismatch'
fi

tmp_sudoers="$(mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-sudoers.install.XXXXXX)"
tmp_target=''
tmp_backup=''
tmp_sudoers_backup=''
tmp_fence=''
restore_probe=''
sudoers_restore_probe=''
trusted_source=''
trusted_authorizer=''
proof="$(mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-install-proof.XXXXXX)"
authorizer_stderr="$(mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-authorizer.stderr.XXXXXX)"
chmod 0600 "$proof" "$authorizer_stderr"
for capture in "$proof" "$authorizer_stderr"; do
  [[ -f "$capture" && ! -L "$capture" ]] || fail 'unsafe install proof capture'
  [[ "$(stat -c '%U:%G:%a' "$capture")" == 'root:root:600' ]] || fail 'install proof capture ownership/mode invalid'
done
promoted=0
committed=0

atomic_copy_executable_verified() {
  local source="$1"
  local expected_sha="$2"
  local destination="$3"
  local staged=''
  local destination_dir="${destination%/*}"
  local destination_name="${destination##*/}"
  [[ "$destination_dir" != "$destination" ]] || return 1
  staged="$(mktemp --tmpdir="$destination_dir" ".${destination_name}.stage.XXXXXX")" || return 1
  install -o root -g root -m 0755 "$source" "$staged" || { rm -f "$staged"; return 1; }
  [[ "$(sha256sum "$staged" | awk '{print $1}')" == "$expected_sha" ]] || { rm -f "$staged"; return 1; }
  bash -n "$staged" || { rm -f "$staged"; return 1; }
  mv -f "$staged" "$destination" || { rm -f "$staged"; return 1; }
  [[ "$(sha256sum "$destination" | awk '{print $1}')" == "$expected_sha" ]]
}

atomic_copy_sudoers_verified() {
  local source="$1"
  local expected_sha="$2"
  local destination="$3"
  local staged=''
  local destination_dir="${destination%/*}"
  local destination_name="${destination##*/}"
  [[ "$destination_dir" != "$destination" ]] || return 1
  staged="$(mktemp --tmpdir="$destination_dir" ".${destination_name}.stage.XXXXXX")" || return 1
  install -o root -g root -m 0440 "$source" "$staged" || { rm -f "$staged"; return 1; }
  [[ "$(sha256sum "$staged" | awk '{print $1}')" == "$expected_sha" ]] || { rm -f "$staged"; return 1; }
  visudo -cf "$staged" >/dev/null || { rm -f "$staged"; return 1; }
  mv -f "$staged" "$destination" || { rm -f "$staged"; return 1; }
  [[ "$(sha256sum "$destination" | awk '{print $1}')" == "$expected_sha" ]] || return 1
  [[ "$(stat -c '%U:%G:%a' "$destination")" == 'root:root:440' ]] || return 1
  visudo -cf "$destination" >/dev/null
}

restore_previous_fence() {
  if (( old_fence_present == 1 )); then
    atomic_copy_executable_verified "$FENCE_BACKUP" "$old_fence_sha" "$FENCE_TARGET"
  else
    rm -f "$FENCE_TARGET"
    [[ ! -e "$FENCE_TARGET" && ! -L "$FENCE_TARGET" ]]
  fi
}

restore_previous_sudoers() {
  local destination="$1"
  if (( old_sudoers_present == 1 )); then
    atomic_copy_sudoers_verified "$SUDOERS_BACKUP" "$old_sudoers_sha" "$destination"
  else
    rm -f "$destination"
    [[ ! -e "$destination" && ! -L "$destination" ]]
  fi
}

cleanup_and_rollback() {
  local rc=$?
  local guard_ok=0
  local sudoers_ok=0
  local fence_ok=0
  trap - EXIT
  if (( rc != 0 && promoted == 1 && committed == 0 )); then
    if atomic_copy_executable_verified "$BACKUP" "$old_sha" "$TARGET"; then
      guard_ok=1
    fi
    if restore_previous_sudoers "$SUDOERS" && visudo -cf /etc/sudoers >/dev/null; then
      sudoers_ok=1
    fi
    if restore_previous_fence; then
      fence_ok=1
    fi
    if (( guard_ok == 1 && sudoers_ok == 1 && fence_ok == 1 )); then
      printf 'ORACLE_IDLE_INSTALL_ROLLBACK=PASS\n' >&2
      printf 'ORACLE_IDLE_ROLLBACK_SHA256=%s\n' "$old_sha" >&2
      if (( old_sudoers_present == 1 )); then
        printf 'ORACLE_IDLE_ROLLBACK_SUDOERS_SHA256=%s\n' "$old_sudoers_sha" >&2
      else
        printf 'ORACLE_IDLE_ROLLBACK_SUDOERS_STATE=ABSENT\n' >&2
      fi
    else
      printf 'ORACLE_IDLE_INSTALL_ROLLBACK=FAIL guard_ok=%s sudoers_ok=%s fence_ok=%s\n' "$guard_ok" "$sudoers_ok" "$fence_ok" >&2
      rc=97
    fi
  fi
    rm -f "$tmp_sudoers" "$tmp_target" "$tmp_backup" "$tmp_sudoers_backup" "$tmp_fence" \
    "$restore_probe" "$sudoers_restore_probe" "$SOURCE_FILE" "$AUTHORIZER_FILE" \
    "$trusted_source" "$trusted_authorizer" "$proof" "$authorizer_stderr"
  exit "$rc"
}
trap cleanup_and_rollback EXIT

# Freeze untrusted /tmp inputs into root-only copies before either is used for
# promotion or execution. A concurrent writer can only make the digest check fail.
trusted_source="$(mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-state.source.XXXXXX)"
trusted_authorizer="$(mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-authorizer.source.XXXXXX)"
install -o root -g root -m 0700 "$SOURCE_FILE" "$trusted_source"
install -o root -g root -m 0700 "$AUTHORIZER_FILE" "$trusted_authorizer"
[[ "$(sha256sum "$trusted_source" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || fail 'trusted source digest mismatch'
[[ "$(sha256sum "$trusted_authorizer" | awk '{print $1}')" == "$AUTHORIZER_SHA256" ]] || fail 'trusted authorizer digest mismatch'
bash -n "$trusted_source"
python3 - "$trusted_authorizer" <<'PYAUTH'
import ast, pathlib, sys
ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), filename=sys.argv[1])
PYAUTH
[[ "$(stat -c '%U:%G:%a' "$trusted_source")" == 'root:root:700' ]] || fail 'trusted source ownership/mode invalid'
[[ "$(stat -c '%U:%G:%a' "$trusted_authorizer")" == 'root:root:700' ]] || fail 'trusted authorizer ownership/mode invalid'

if [[ -e "$BACKUP" || -L "$BACKUP" ]]; then
  [[ -f "$BACKUP" && ! -L "$BACKUP" ]] || fail 'rollback backup path is unsafe'
  [[ "$(sha256sum "$BACKUP" | awk '{print $1}')" == "$old_sha" ]] || fail 'rollback backup digest mismatch'
else
  tmp_backup="$(mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-state.rollback.XXXXXX)"
  install -o root -g root -m 0755 "$TARGET" "$tmp_backup"
  [[ "$(sha256sum "$tmp_backup" | awk '{print $1}')" == "$old_sha" ]] || fail 'rollback backup verification failed'
  bash -n "$tmp_backup"
  mv -f "$tmp_backup" "$BACKUP"
  tmp_backup=''
fi

if (( old_sudoers_present == 1 )); then
  if [[ -e "$SUDOERS_BACKUP" || -L "$SUDOERS_BACKUP" ]]; then
    [[ -f "$SUDOERS_BACKUP" && ! -L "$SUDOERS_BACKUP" ]] || fail 'sudoers rollback backup path is unsafe'
    [[ "$(sha256sum "$SUDOERS_BACKUP" | awk '{print $1}')" == "$old_sudoers_sha" ]] || fail 'sudoers rollback backup digest mismatch'
    visudo -cf "$SUDOERS_BACKUP" >/dev/null || fail 'sudoers rollback backup syntax invalid'
  else
    tmp_sudoers_backup="$(mktemp --tmpdir="$BACKUP_DIR" .oracle-idle-sudoers.rollback.XXXXXX)"
    install -o root -g root -m 0440 "$SUDOERS" "$tmp_sudoers_backup"
    [[ "$(sha256sum "$tmp_sudoers_backup" | awk '{print $1}')" == "$old_sudoers_sha" ]] || fail 'sudoers rollback backup verification failed'
    visudo -cf "$tmp_sudoers_backup" >/dev/null
    mv -f "$tmp_sudoers_backup" "$SUDOERS_BACKUP"
    tmp_sudoers_backup=''
  fi
fi

restore_probe="/usr/local/sbin/.oracle-idle-state-restore-probe.$$"
[[ ! -e "$restore_probe" && ! -L "$restore_probe" ]] || fail 'restore probe path collision'
atomic_copy_executable_verified "$BACKUP" "$old_sha" "$restore_probe" || fail 'rollback restore probe failed'
[[ "$(sha256sum "$restore_probe" | awk '{print $1}')" == "$old_sha" ]] || fail 'rollback restore probe digest mismatch'
rm -f "$restore_probe"
restore_probe=''
printf 'ORACLE_IDLE_ROLLBACK_GUARD_PROBE=PASS\n'

sudoers_restore_probe="/etc/sudoers.d/oracle-idle-state-ocarun-restore-probe-$$"
[[ ! -e "$sudoers_restore_probe" && ! -L "$sudoers_restore_probe" ]] || fail 'sudoers restore probe path collision'
if (( old_sudoers_present == 0 )); then
  printf 'ocarun ALL=(root) NOPASSWD: /usr/bin/true ""\n' > "$tmp_sudoers"
  chmod 0440 "$tmp_sudoers"
  visudo -cf "$tmp_sudoers" >/dev/null
  install -o root -g root -m 0440 "$tmp_sudoers" "$sudoers_restore_probe"
fi
restore_previous_sudoers "$sudoers_restore_probe" || fail 'sudoers rollback restore probe failed'
if (( old_sudoers_present == 1 )); then
  [[ "$(sha256sum "$sudoers_restore_probe" | awk '{print $1}')" == "$old_sudoers_sha" ]] || fail 'sudoers restore probe digest mismatch'
  rm -f "$sudoers_restore_probe"
else
  [[ ! -e "$sudoers_restore_probe" && ! -L "$sudoers_restore_probe" ]] || fail 'sudoers absence restore probe failed'
fi
sudoers_restore_probe=''
printf 'ORACLE_IDLE_ROLLBACK_SUDOERS_PROBE=PASS\n'

tmp_target="$(mktemp --tmpdir=/usr/local/sbin .oracle-idle-state.install.XXXXXX)"
install -o root -g root -m 0755 "$trusted_source" "$tmp_target"
[[ "$(sha256sum "$tmp_target" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || fail 'staged target digest mismatch'
bash -n "$tmp_target"
mv -f "$tmp_target" "$TARGET"
tmp_target=''
promoted=1
[[ "$(sha256sum "$TARGET" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || fail 'installed target digest mismatch'

tmp_fence="$(mktemp --tmpdir=/usr/local/sbin .oracle-idle-stop-fence.install.XXXXXX)"
cat > "$tmp_fence" <<'FENCE'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
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
    touch "$LOCK"
    chown root:universal-video "$LOCK"
    chmod 0660 "$LOCK"
    exec 9>"$LOCK"
    flock -n 9 || exit 73
    /usr/local/sbin/oracle-idle-state > "$proof"
    printf '%s' "$token" > "$token_file"
    printf '%s' "$$" > "$pid_file"
    # Longer than the workflow's 600-second STOP wait; normal completion is
    # the instance shutdown terminating this holder, not timer expiry.
    sleep 900
    ;;
  read)
    # The classifier can legitimately consume its bounded database timeouts.
    # Wait for the holder to publish all three files instead of treating a
    # not-yet-ready proof as terminal.
    for _ in $(seq 1 450); do
      [[ -r "$token_file" && -r "$pid_file" && -r "$proof" ]] && break
      sleep 0.1
    done
    [[ -r "$token_file" && -r "$pid_file" && -r "$proof" ]] || exit 74
    [[ "$(cat "$token_file")" == "$token" ]] || exit 74
    kill -0 "$(cat "$pid_file")" || exit 74
    if flock -n "$LOCK" true; then exit 74; fi
    cat "$proof"
    ;;
  release)
    [[ -r "$token_file" && -r "$pid_file" ]] || exit 74
    [[ "$(cat "$token_file")" == "$token" ]] || exit 74
    holder_pid="$(cat "$pid_file")"
    [[ "$holder_pid" =~ ^[1-9][0-9]*$ ]] || exit 74
    [[ -r "/proc/$holder_pid/cmdline" ]] || exit 74
    tr '\0' '\n' < "/proc/$holder_pid/cmdline" \
      | grep -Fx '/usr/local/sbin/oracle-idle-stop-fence' >/dev/null || exit 74
    tr '\0' '\n' < "/proc/$holder_pid/cmdline" | grep -Fx "$token" >/dev/null || exit 74
    kill "$holder_pid"
    for _ in $(seq 1 100); do
      kill -0 "$holder_pid" 2>/dev/null || break
      sleep 0.1
    done
    kill -0 "$holder_pid" 2>/dev/null && exit 75
    rm -f "$proof" "$token_file" "$pid_file"
    flock -n "$LOCK" true || exit 75
    ;;
  *) exit 64 ;;
esac
FENCE
chmod 0755 "$tmp_fence"
fence_sha="$(sha256sum "$tmp_fence" | awk '{print $1}')"
atomic_copy_executable_verified "$tmp_fence" "$fence_sha" "$FENCE_TARGET" || fail 'fence helper install failed'
rm -f "$tmp_fence"
tmp_fence=''

cat > "$tmp_sudoers" <<'EOF'
# Exact read-only idle classifier for OCI Run Command. Empty argv is required.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/oracle-idle-state ""
# Bounded validated token only; no arbitrary shell or command is accepted.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/oracle-idle-stop-fence hold *
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/oracle-idle-stop-fence read *
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/oracle-idle-stop-fence release *
EOF
chmod 0440 "$tmp_sudoers"
visudo -cf "$tmp_sudoers" >/dev/null
atomic_copy_sudoers_verified "$tmp_sudoers" "$(sha256sum "$tmp_sudoers" | awk '{print $1}')" "$SUDOERS" || fail 'sudoers install failed'
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
state="${lines[4]#ORACLE_IDLE_STATE=}"

test_token="install-test-$$"
sudo -u ocarun sudo -n "$FENCE_TARGET" hold "$test_token" &
fence_pid=$!
token_ready=0
for _ in $(seq 1 600); do
  if [[ -r "/run/oracle-stop-guard/$test_token.token" ]]; then token_ready=1; break; fi
  kill -0 "$fence_pid" 2>/dev/null || break
  sleep 0.1
done
[[ "$token_ready" == 1 ]] || fail 'fence token preflight timed out'
sudo -u ocarun sudo -n "$FENCE_TARGET" read "$test_token" >/dev/null
sudo -u ocarun sudo -n "$FENCE_TARGET" release "$test_token"
wait "$fence_pid" 2>/dev/null || true

# The exact STOP authorizer is part of the installation transaction. A proof
# that is syntactically valid but stale, contradictory, or otherwise rejected
# must roll the promoted guard and sudoers state back before this script exits.
set +e
authorization="$(python3 "$trusted_authorizer" --proof "$proof" --max-age-seconds 30 --max-duration-seconds 30 --max-future-skew-seconds 5 2>"$authorizer_stderr")"
authorization_rc=$?
set -e
[[ ! -s "$authorizer_stderr" ]] || { cat "$authorizer_stderr" >&2; fail 'exact authorizer emitted stderr'; }
printf '%s\n' "$authorization"
case "$state" in
  IDLE)
    [[ $authorization_rc -eq 0 ]] || fail 'exact IDLE authorizer rejected proof'
    [[ "$authorization" == $'ORACLE_STOP_AUTHORIZED=YES\nORACLE_STOP_AUTHORIZATION_REASON=fresh_exact_idle' ]] || fail 'exact IDLE authorization mismatch'
    ;;
  BUSY)
    [[ $authorization_rc -ne 0 ]] || fail 'BUSY unexpectedly authorized STOP'
    [[ "$authorization" == $'ORACLE_STOP_AUTHORIZED=NO\nORACLE_STOP_AUTHORIZATION_REASON=state_busy_forbids_stop' ]] || fail 'BUSY refusal mismatch'
    ;;
  UNKNOWN)
    [[ $authorization_rc -ne 0 ]] || fail 'UNKNOWN unexpectedly authorized STOP'
    [[ "$authorization" == $'ORACLE_STOP_AUTHORIZED=NO\nORACLE_STOP_AUTHORIZATION_REASON=state_unknown_forbids_stop' ]] || fail 'UNKNOWN refusal mismatch'
    ;;
  *) fail 'unreachable classifier state' ;;
esac

authorizer_sha_readback="$(sha256sum "$trusted_authorizer" | awk '{print $1}')"
[[ "$authorizer_sha_readback" == "$AUTHORIZER_SHA256" ]] || fail 'authorizer changed during transaction'

committed=1
printf 'ORACLE_IDLE_BACKUP_PATH=%s\n' "$BACKUP"
printf 'ORACLE_IDLE_BACKUP_SHA256=%s\n' "$old_sha"
if (( old_sudoers_present == 1 )); then
  printf 'ORACLE_IDLE_SUDOERS_BACKUP_PATH=%s\n' "$SUDOERS_BACKUP"
  printf 'ORACLE_IDLE_SUDOERS_BACKUP_SHA256=%s\n' "$old_sudoers_sha"
else
  printf 'ORACLE_IDLE_SUDOERS_BACKUP_STATE=ABSENT\n'
fi
printf 'ORACLE_IDLE_AUTHORIZER_SHA256=%s\n' "$AUTHORIZER_SHA256"
printf 'ORACLE_IDLE_INSTALLED_SHA256=%s\n' "$SOURCE_SHA256"
echo ORACLE_IDLE_STATE_OCARUN_INSTALL_PASS
