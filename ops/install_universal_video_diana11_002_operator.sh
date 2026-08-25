#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# One-time root bootstrap for four exact UV-DIANA11-DURABLE-002 operator commands.
# No shell, editor, arbitrary path, or NOPASSWD:ALL is granted.

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
: "${SOURCE_FILE:?SOURCE_FILE is required}"
: "${EXPECTED_RUNTIME_COMMIT:?EXPECTED_RUNTIME_COMMIT is required}"
[[ -f "$SOURCE_FILE" && ! -L "$SOURCE_FILE" ]] || fail 'operator source must be a regular file'
[[ "$EXPECTED_RUNTIME_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail 'invalid expected runtime commit'
bash -n "$SOURCE_FILE"
grep -Fq "readonly BRIDGE_JOB_ID='diana11-durable-002-20260826-01'" "$SOURCE_FILE" || fail 'unexpected bridge job id'
grep -Fq "readonly BRIDGE_JOB_HASH='e53fa37ce69d97bc9d8c995bc8f416b0e7b5ad42610cda4d75faa2385bcf60fc'" "$SOURCE_FILE" || fail 'unexpected bridge job hash'
grep -Fq "readonly DRIVE_FILE_ID='1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C'" "$SOURCE_FILE" || fail 'unexpected Drive id'
grep -Fq "readonly DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'" "$SOURCE_FILE" || fail 'unexpected Drive results folder id'
! grep -Eq '(^|[[:space:]])(bash|sh)[[:space:]]+-c' "$SOURCE_FILE" || fail 'dynamic shell execution forbidden'
id ocarun >/dev/null 2>&1 || fail 'ocarun user missing'
command -v visudo >/dev/null || fail 'visudo required'

readonly TARGET='/usr/local/sbin/universal-video-diana11-002'
readonly SUDOERS='/etc/sudoers.d/universal-video-diana11-002-ocarun'
readonly RUNTIME_SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly RUNTIME_PYTHON='/opt/bridge-school/universal-video/.venv/bin/python'
readonly CONTROL_PARENT='/opt/bridge-school'
readonly ROOT_STAGING='/opt/bridge-school/.universal-video-diana11-002-staging'
readonly PUBLISHED_DIR='/opt/bridge-school/.universal-video-diana11-002-published'
[[ -d "$CONTROL_PARENT" && ! -L "$CONTROL_PARENT" ]] || fail 'unsafe control parent'
[[ "$(stat -c '%U:%G' "$CONTROL_PARENT")" == 'root:root' ]] || fail 'unsafe control parent ownership'
control_parent_mode="$(stat -c '%a' "$CONTROL_PARENT")"
(( (8#$control_parent_mode & 0022) == 0 )) || fail 'control parent is group/world writable'
[[ -d "$RUNTIME_SOURCE_DIR/.git" && -x "$RUNTIME_PYTHON" ]] || fail 'Universal Video runtime checkout missing'
[[ "$(git -C "$RUNTIME_SOURCE_DIR" rev-parse HEAD)" == "$EXPECTED_RUNTIME_COMMIT" ]] || fail 'runtime commit mismatch'
[[ -z "$(git -C "$RUNTIME_SOURCE_DIR" status --porcelain=v1 --untracked-files=all)" ]] || fail 'runtime checkout is dirty'
[[ -f "$RUNTIME_SOURCE_DIR/universal_video/result_conformance.py" ]] || fail 'result conformance module missing'
[[ -f "$RUNTIME_SOURCE_DIR/universal_video/drive_results.py" ]] || fail 'Drive result module missing'
[[ -f "$RUNTIME_SOURCE_DIR/ops/oracle_universal_video_spool_guard.sh" ]] || fail 'spool path guard missing'
[[ -f "$RUNTIME_SOURCE_DIR/ops/universal_video_receipt_reader.py" ]] || fail 'safe receipt reader missing'
grep -Fq 'universal-video-result-conformance-v1' "$RUNTIME_SOURCE_DIR/universal_video/result_conformance.py" || fail 'unexpected result conformance module'
grep -Fq 'multipart/related; boundary=' "$RUNTIME_SOURCE_DIR/universal_video/drive_results.py" || fail 'hardened Drive multipart transport missing'
grep -Fq 'PUBLISHED_VERIFIED' "$RUNTIME_SOURCE_DIR/universal_video/drive_results.py" || fail 'verified Drive publication missing'
grep -Fq 'os.O_NOFOLLOW' "$RUNTIME_SOURCE_DIR/ops/universal_video_receipt_reader.py" || fail 'no-follow receipt reader missing'
expected_operator_blob="$(git -C "$RUNTIME_SOURCE_DIR" rev-parse "$EXPECTED_RUNTIME_COMMIT:ops/universal_video_diana11_002_operator.sh")"
[[ "$(git hash-object "$SOURCE_FILE")" == "$expected_operator_blob" ]] || fail 'operator source does not match runtime commit'
runuser -u universal-video -- env PYTHONPATH="$RUNTIME_SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 \
  "$RUNTIME_PYTHON" - <<'PY'
from universal_video.result_conformance import verify_result
from universal_video.drive_results import publish_result
assert callable(verify_result) and callable(publish_result)
PY

tmp="$(mktemp)"
cat > "$tmp" <<'EOF'
# Exact one-job Universal Video controls for UV-DIANA11-DURABLE-002.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-002 submit-bridge
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-002 status-bridge
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-002 conform-bridge
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-002 publish-bridge
EOF
chmod 0440 "$tmp"
visudo -cf "$tmp" >/dev/null

backup="$(mktemp -d -t diana11-operator-backup.XXXXXX)"
had_target=0; had_sudoers=0; had_staging=0; had_published=0; completed=0
if [[ -e "$TARGET" ]]; then cp -a "$TARGET" "$backup/target"; had_target=1; fi
if [[ -e "$SUDOERS" ]]; then cp -a "$SUDOERS" "$backup/sudoers"; had_sudoers=1; fi
if [[ -e "$ROOT_STAGING" || -L "$ROOT_STAGING" ]]; then
  [[ -d "$ROOT_STAGING" && ! -L "$ROOT_STAGING" ]] || fail 'unsafe existing root staging path'
  [[ "$(stat -c '%U:%G:%a' "$ROOT_STAGING")" == 'root:root:700' ]] || fail 'unsafe existing root staging ownership/mode'
  had_staging=1
fi
if [[ -e "$PUBLISHED_DIR" || -L "$PUBLISHED_DIR" ]]; then
  [[ -d "$PUBLISHED_DIR" && ! -L "$PUBLISHED_DIR" ]] || fail 'unsafe existing published path'
  [[ "$(stat -c '%U:%G:%a' "$PUBLISHED_DIR")" == 'root:root:700' ]] || fail 'unsafe existing published ownership/mode'
  had_published=1
fi
cleanup(){
  local rc=$?
  set +e
  if (( completed == 0 )); then
    if (( had_target == 1 )); then cp -a "$backup/target" "$TARGET"; else rm -f "$TARGET"; fi
    if (( had_sudoers == 1 )); then cp -a "$backup/sudoers" "$SUDOERS"; else rm -f "$SUDOERS"; fi
    if (( had_staging == 0 )); then rmdir "$ROOT_STAGING" 2>/dev/null || true; fi
    if (( had_published == 0 )); then rmdir "$PUBLISHED_DIR" 2>/dev/null || true; fi
    visudo -cf /etc/sudoers >/dev/null 2>&1 || true
  fi
  rm -f "$tmp"
  rm -rf "$backup"
  return "$rc"
}
trap cleanup EXIT

if (( had_staging == 0 )); then install -d -o root -g root -m 0700 "$ROOT_STAGING"; fi
if (( had_published == 0 )); then install -d -o root -g root -m 0700 "$PUBLISHED_DIR"; fi
[[ "$(stat -c '%d' "$ROOT_STAGING")" == "$(stat -c '%d' /opt/bridge-school/universal-video/spool/inbox)" ]] \
  || fail 'root staging and inbox must share a filesystem'

# Exercise the exact read-only status path after the protected control dirs exist.
bash "$SOURCE_FILE" status-bridge >/dev/null

install -o root -g root -m 0755 "$SOURCE_FILE" "$TARGET"
install -o root -g root -m 0440 "$tmp" "$SUDOERS"
visudo -cf /etc/sudoers >/dev/null
! grep -Eq 'NOPASSWD:[[:space:]]*ALL' "$SUDOERS" || fail 'broad sudo grant detected'
[[ "$(stat -c '%U:%G:%a' "$TARGET")" == 'root:root:755' ]] || fail 'operator ownership/mode mismatch'
[[ "$(stat -c '%U:%G:%a' "$SUDOERS")" == 'root:root:440' ]] || fail 'sudoers ownership/mode mismatch'

sudo -u ocarun sudo -n "$TARGET" status-bridge >/dev/null
completed=1
echo UNIVERSAL_VIDEO_DIANA11_002_OPERATOR_BOOTSTRAP_PASS
