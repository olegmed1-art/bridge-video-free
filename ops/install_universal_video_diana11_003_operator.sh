#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Installs exactly five fixed UV-DIANA11-DURABLE-003 commands.
# No shell, editor, arbitrary path, argument, or NOPASSWD:ALL is granted.

stage='BOOTSTRAP_INPUT'
fail(){
  printf 'UV003_OPERATOR_BOOTSTRAP_FAILURE=%s\n' "$stage" >&2
  exit 1
}
on_exit(){
  local rc=$?
  if (( rc != 0 )); then printf 'UV003_OPERATOR_BOOTSTRAP_FAILURE=%s\n' "$stage" >&2; fi
  return "$rc"
}
trap on_exit EXIT

[[ $(id -u) -eq 0 ]] || fail
: "${SOURCE_FILE:?SOURCE_FILE is required}"
: "${EXPECTED_RUNTIME_COMMIT:?EXPECTED_RUNTIME_COMMIT is required}"
: "${EXPECTED_SOURCE_SHA256:?EXPECTED_SOURCE_SHA256 is required}"
[[ -f "$SOURCE_FILE" && ! -L "$SOURCE_FILE" ]] || fail
[[ "$EXPECTED_RUNTIME_COMMIT" == '6a4e8248eedd00f849fcefd1bf41a51b26f5e7c6' ]] || fail
[[ "$EXPECTED_SOURCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail
[[ "$(sha256sum "$SOURCE_FILE" | awk '{print $1}')" == "$EXPECTED_SOURCE_SHA256" ]] || fail

stage='SOURCE_CONTRACT'
bash -n "$SOURCE_FILE"
grep -Fq "readonly BRIDGE_JOB_ID='diana11-shadow-20260826-001'" "$SOURCE_FILE" || fail
grep -Fq "readonly BRIDGE_JOB_HASH='a43e11beb0765aa91551d4c4a69767f02c4dcb3b5e485cd5bb0f2996e734d73d'" "$SOURCE_FILE" || fail
grep -Fq "readonly DRIVE_FILE_ID='1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C'" "$SOURCE_FILE" || fail
grep -Fq "readonly SOURCE_SIZE_BYTES='740292560'" "$SOURCE_FILE" || fail
grep -Fq "readonly DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'" "$SOURCE_FILE" || fail
grep -Fq "readonly EXPECTED_RUNTIME_COMMIT='6a4e8248eedd00f849fcefd1bf41a51b26f5e7c6'" "$SOURCE_FILE" || fail
grep -Fq "readonly EXPECTED_WHISPER_MODEL='small'" "$SOURCE_FILE" || fail
grep -Fq "readonly EXPECTED_PROCESSING_FINGERPRINT='371661d2a1858e576e2f618ddf504da724edc30089a9af88f9dd3a140ca30951'" "$SOURCE_FILE" || fail
grep -Fq "'max_duration_seconds':43200.0" "$SOURCE_FILE" || fail
grep -Fq "UV-DIANA11-DURABLE-003 fresh provenance shadow" "$SOURCE_FILE" || fail
grep -Fq 'UNIVERSAL_VIDEO_DIANA11_003_READBACK_PASS' "$SOURCE_FILE" || fail
! grep -Eq '(^|[[:space:]])(bash|sh)[[:space:]]+-c' "$SOURCE_FILE" || fail

readonly TARGET='/usr/local/sbin/universal-video-diana11-003'
readonly SUDOERS='/etc/sudoers.d/universal-video-diana11-003-ocarun'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly RUNTIME_ENV='/opt/bridge-school/universal-video/universal-video.env'
readonly RUNTIME_PYTHON='/opt/bridge-school/universal-video/.venv/bin/python'
readonly CONTROL_PARENT='/opt/bridge-school'
readonly ROOT_STAGING='/opt/bridge-school/.universal-video-diana11-003-staging'
readonly PUBLISHED_DIR='/opt/bridge-school/.universal-video-diana11-003-published'

stage='RUNTIME_CONTRACT'
id ocarun >/dev/null 2>&1 || fail
id universal-video >/dev/null 2>&1 || fail
command -v visudo >/dev/null || fail
[[ -d "$CONTROL_PARENT" && ! -L "$CONTROL_PARENT" ]] || fail
[[ "$(stat -c '%U:%G' "$CONTROL_PARENT")" == 'root:root' ]] || fail
control_parent_mode="$(stat -c '%a' "$CONTROL_PARENT")"
(( (8#$control_parent_mode & 0022) == 0 )) || fail
[[ -d "$SOURCE_DIR/.git" && ! -L "$SOURCE_DIR" && -x "$RUNTIME_PYTHON" ]] || fail
[[ -f "$RUNTIME_ENV" && ! -L "$RUNTIME_ENV" ]] || fail
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$EXPECTED_RUNTIME_COMMIT" ]] || fail
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all)" ]] || fail
for required in \
  universal_video/result_conformance.py \
  universal_video/drive_results.py \
  universal_video/drive_adapter.py \
  ops/oracle_universal_video_spool_guard.sh \
  ops/universal_video_receipt_reader.py; do
  [[ -f "$SOURCE_DIR/$required" ]] || fail
done
grep -Fq 'universal-video-result-conformance-v1' "$SOURCE_DIR/universal_video/result_conformance.py" || fail
grep -Fq 'PUBLISHED_VERIFIED' "$SOURCE_DIR/universal_video/drive_results.py" || fail
grep -Fq 'remote_verification' "$SOURCE_DIR/universal_video/drive_results.py" || fail
grep -Fq 'os.O_NOFOLLOW' "$SOURCE_DIR/ops/universal_video_receipt_reader.py" || fail
runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 "$RUNTIME_PYTHON" - <<'PY'
from universal_video.result_conformance import verify_result
from universal_video.drive_results import publish_result
from universal_video.drive_adapter import access_token
assert callable(verify_result) and callable(publish_result) and callable(access_token)
PY

stage='SUDOERS_TEMPLATE'
tmp="$(mktemp)"
cat > "$tmp" <<'EOF'
# Exact one-job controls for UV-DIANA11-DURABLE-003.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-003 submit-bridge
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-003 status-bridge
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-003 conform-bridge
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-003 publish-bridge
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-003 readback-bridge
EOF
chmod 0440 "$tmp"
visudo -cf "$tmp" >/dev/null
! grep -Eq 'NOPASSWD:[[:space:]]*ALL' "$tmp" || fail

stage='INSTALL_SURFACE'
backup="$(mktemp -d -t uv003-operator-backup.XXXXXX)"
had_target=0; had_sudoers=0; had_staging=0; had_published=0; completed=0
if [[ -e "$TARGET" ]]; then cp -a "$TARGET" "$backup/target"; had_target=1; fi
if [[ -e "$SUDOERS" ]]; then cp -a "$SUDOERS" "$backup/sudoers"; had_sudoers=1; fi
for path in "$ROOT_STAGING" "$PUBLISHED_DIR"; do
  if [[ -e "$path" || -L "$path" ]]; then
    [[ -d "$path" && ! -L "$path" ]] || fail
    [[ "$(stat -c '%U:%G:%a' "$path")" == 'root:root:700' ]] || fail
    if [[ "$path" == "$ROOT_STAGING" ]]; then had_staging=1; else had_published=1; fi
  fi
done
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
[[ "$(stat -c '%d' "$ROOT_STAGING")" == "$(stat -c '%d' /opt/bridge-school/universal-video/spool/inbox)" ]] || fail

# Exercise only the exact read-only status path before installing the sudo grant.
bash "$SOURCE_FILE" status-bridge >/dev/null
install -o root -g root -m 0755 "$SOURCE_FILE" "$TARGET"
install -o root -g root -m 0440 "$tmp" "$SUDOERS"
visudo -cf /etc/sudoers >/dev/null
[[ "$(stat -c '%U:%G:%a' "$TARGET")" == 'root:root:755' ]] || fail
[[ "$(stat -c '%U:%G:%a' "$SUDOERS")" == 'root:root:440' ]] || fail
sudo -u ocarun sudo -n "$TARGET" status-bridge >/dev/null
if sudo -u ocarun sudo -n "$TARGET" unexpected >/dev/null 2>&1; then fail; fi
completed=1
trap - EXIT
rm -f "$tmp"
rm -rf "$backup"
echo 'UV003_OPERATOR_BOOTSTRAP=PASS'
