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
readonly BACKUP_DIR='/var/backups/oracle-idle-guard'
[[ -f "$TARGET" && ! -L "$TARGET" ]] || fail 'existing guard required for recoverable backup'
old_sha="$(sha256sum "$TARGET" | awk '{print $1}')"
[[ "$old_sha" =~ ^[0-9a-f]{64}$ ]] || fail 'existing guard digest invalid'
readonly BACKUP="$BACKUP_DIR/oracle-idle-state-${old_sha}"
old_sudoers_present=0
old_sudoers_sha=''
SUDOERS_BACKUP=''

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

tmp_sudoers="$(mktemp)"
tmp_target=''
tmp_backup=''
tmp_sudoers_backup=''
restore_probe=''
sudoers_restore_probe=''
proof='/tmp/oracle-idle-state-install-proof.txt'
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
  trap - EXIT
  if (( rc != 0 && promoted == 1 && committed == 0 )); then
    if atomic_copy_executable_verified "$BACKUP" "$old_sha" "$TARGET"; then
      guard_ok=1
    fi
    if restore_previous_sudoers "$SUDOERS" && visudo -cf /etc/sudoers >/dev/null; then
      sudoers_ok=1
    fi
    if (( guard_ok == 1 && sudoers_ok == 1 )); then
      printf 'ORACLE_IDLE_INSTALL_ROLLBACK=PASS\n' >&2
      printf 'ORACLE_IDLE_ROLLBACK_SHA256=%s\n' "$old_sha" >&2
      if (( old_sudoers_present == 1 )); then
        printf 'ORACLE_IDLE_ROLLBACK_SUDOERS_SHA256=%s\n' "$old_sudoers_sha" >&2
      else
        printf 'ORACLE_IDLE_ROLLBACK_SUDOERS_STATE=ABSENT\n' >&2
      fi
    else
      printf 'ORACLE_IDLE_INSTALL_ROLLBACK=FAIL guard_ok=%s sudoers_ok=%s\n' "$guard_ok" "$sudoers_ok" >&2
      rc=97
    fi
  fi
  rm -f "$tmp_sudoers" "$tmp_target" "$tmp_backup" "$tmp_sudoers_backup" \
    "$restore_probe" "$sudoers_restore_probe" "$SOURCE_FILE" "$proof"
  exit "$rc"
}
trap cleanup_and_rollback EXIT

# Persist an exact, non-sudoers-included backup of the currently installed
# executable before any promotion. Reuse is allowed only by matching digest.
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

# Persist the pre-install sudoers state outside /etc/sudoers.d so the backup
# cannot itself grant privileges. If the file was absent, absence is the state.
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

# Exercise the exact executable restore primitive against a sacrificial path on
# the target filesystem before promotion.
restore_probe="/usr/local/sbin/.oracle-idle-state-restore-probe.$$"
[[ ! -e "$restore_probe" && ! -L "$restore_probe" ]] || fail 'restore probe path collision'
atomic_copy_executable_verified "$BACKUP" "$old_sha" "$restore_probe" || fail 'rollback restore probe failed'
[[ "$(sha256sum "$restore_probe" | awk '{print $1}')" == "$old_sha" ]] || fail 'rollback restore probe digest mismatch'
rm -f "$restore_probe"
restore_probe=''
printf 'ORACLE_IDLE_ROLLBACK_GUARD_PROBE=PASS\n'

# Exercise the exact sudoers restore primitive on a sacrificial path. For an
# originally absent sudoers file, first create a valid dummy and prove restore
# removes it; for a present one, prove exact digest/mode/syntax restoration.
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

# Stage the exact candidate on the same filesystem and atomically rename it
# into place only after source, backup and restore validation have passed.
tmp_target="$(mktemp --tmpdir=/usr/local/sbin .oracle-idle-state.install.XXXXXX)"
install -o root -g root -m 0755 "$SOURCE_FILE" "$tmp_target"
[[ "$(sha256sum "$tmp_target" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || fail 'staged target digest mismatch'
bash -n "$tmp_target"
mv -f "$tmp_target" "$TARGET"
tmp_target=''
promoted=1
[[ "$(sha256sum "$TARGET" | awk '{print $1}')" == "$SOURCE_SHA256" ]] || fail 'installed target digest mismatch'

cat > "$tmp_sudoers" <<'EOF'
# Exact read-only idle classifier for OCI Run Command. Empty argv is required.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/oracle-idle-state ""
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

# All post-promotion validation passed. Disable rollback only now.
committed=1
printf 'ORACLE_IDLE_BACKUP_PATH=%s\n' "$BACKUP"
printf 'ORACLE_IDLE_BACKUP_SHA256=%s\n' "$old_sha"
if (( old_sudoers_present == 1 )); then
  printf 'ORACLE_IDLE_SUDOERS_BACKUP_PATH=%s\n' "$SUDOERS_BACKUP"
  printf 'ORACLE_IDLE_SUDOERS_BACKUP_SHA256=%s\n' "$old_sudoers_sha"
else
  printf 'ORACLE_IDLE_SUDOERS_BACKUP_STATE=ABSENT\n'
fi
printf 'ORACLE_IDLE_INSTALLED_SHA256=%s\n' "$SOURCE_SHA256"
echo ORACLE_IDLE_STATE_OCARUN_INSTALL_PASS
