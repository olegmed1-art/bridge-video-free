#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Installs exactly one read-only, no-argument preflight command for #566.
# It does not stop/start services, enqueue a job, process media, or write Drive.

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
: "${SOURCE_FILE:?SOURCE_FILE is required}"
: "${EXPECTED_RUNTIME_COMMIT:?EXPECTED_RUNTIME_COMMIT is required}"
[[ -f "$SOURCE_FILE" && ! -L "$SOURCE_FILE" ]] || fail 'preflight source must be a regular file'
[[ "$EXPECTED_RUNTIME_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail 'invalid expected runtime commit'
python3 -m py_compile "$SOURCE_FILE"
grep -Fq 'JOB_ID = "diana11-shadow-20260826-001"' "$SOURCE_FILE" || fail 'unexpected fresh job id'
grep -Fq 'EXPECTED_JOB_HASH = "a43e11beb0765aa91551d4c4a69767f02c4dcb3b5e485cd5bb0f2996e734d73d"' "$SOURCE_FILE" || fail 'unexpected fresh job hash'
grep -Fq 'UV003_EXECUTION_AUTHORIZED=NO' "$SOURCE_FILE" || fail 'execution guard missing'
grep -Fq 'UV003_PUBLICATION_AUTHORIZED=NO' "$SOURCE_FILE" || fail 'publication guard missing'
grep -Fq 'len(sys.argv) != 1' "$SOURCE_FILE" || fail 'no-argument guard missing'
for forbidden in 'enqueue(' 'submit_for' 'submit-bridge' 'drive_results' 'GOOGLE_DRIVE_OAUTH' 'ffmpeg' 'faster_whisper' 'WhisperModel('; do
  ! grep -Fq "$forbidden" "$SOURCE_FILE" || fail "forbidden execution surface: $forbidden"
done

readonly TARGET='/usr/local/sbin/universal-video-diana11-shadow-preflight'
readonly SUDOERS='/etc/sudoers.d/universal-video-diana11-shadow-preflight-ocarun'
readonly RUNTIME_ENV='/opt/bridge-school/universal-video/universal-video.env'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly SPOOL_ROOT='/opt/bridge-school/universal-video/spool'

id ocarun >/dev/null 2>&1 || fail 'ocarun user missing'
command -v visudo >/dev/null || fail 'visudo required'
[[ -f "$RUNTIME_ENV" && ! -L "$RUNTIME_ENV" ]] || fail 'runtime env missing or unsafe'
[[ -d "$SOURCE_DIR/.git" && ! -L "$SOURCE_DIR" ]] || fail 'runtime source checkout missing or unsafe'
[[ -d "$SPOOL_ROOT" && ! -L "$SPOOL_ROOT" ]] || fail 'runtime spool missing or unsafe'
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$EXPECTED_RUNTIME_COMMIT" ]] || fail 'runtime commit mismatch'
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all)" ]] || fail 'runtime checkout is dirty'
RUNTIME_ENV="$RUNTIME_ENV" EXPECTED_RUNTIME_COMMIT="$EXPECTED_RUNTIME_COMMIT" python3 - <<'PY'
import os
from pathlib import Path
values={}
for raw in Path(os.environ['RUNTIME_ENV']).read_text(encoding='utf-8').splitlines():
    line=raw.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key,value=line.split('=',1)
    if key in {'UNIVERSAL_VIDEO_SOURCE_COMMIT','UNIVERSAL_VIDEO_WHISPER_MODEL'}:
        values[key]=value.strip()
assert values.get('UNIVERSAL_VIDEO_SOURCE_COMMIT') == os.environ['EXPECTED_RUNTIME_COMMIT']
model=values.get('UNIVERSAL_VIDEO_WHISPER_MODEL','')
assert model and len(model) <= 80 and not any(ch.isspace() for ch in model)
PY

# Exercise the exact read-only preflight before modifying the sudo surface.
python3 "$SOURCE_FILE" >/dev/null

tmp="$(mktemp)"
cat > "$tmp" <<'EOF'
# Exact no-argument read-only preflight for UV-DIANA11-DURABLE-003.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11-shadow-preflight ""
EOF
chmod 0440 "$tmp"
visudo -cf "$tmp" >/dev/null

backup="$(mktemp -d -t uv003-preflight-backup.XXXXXX)"
had_target=0; had_sudoers=0; completed=0
if [[ -e "$TARGET" ]]; then cp -a "$TARGET" "$backup/target"; had_target=1; fi
if [[ -e "$SUDOERS" ]]; then cp -a "$SUDOERS" "$backup/sudoers"; had_sudoers=1; fi
cleanup(){
  local rc=$?
  set +e
  if (( completed == 0 )); then
    if (( had_target == 1 )); then cp -a "$backup/target" "$TARGET"; else rm -f "$TARGET"; fi
    if (( had_sudoers == 1 )); then cp -a "$backup/sudoers" "$SUDOERS"; else rm -f "$SUDOERS"; fi
    visudo -cf /etc/sudoers >/dev/null 2>&1 || true
  fi
  rm -f "$tmp"
  rm -rf "$backup"
  return "$rc"
}
trap cleanup EXIT

install -o root -g root -m 0755 "$SOURCE_FILE" "$TARGET"
install -o root -g root -m 0440 "$tmp" "$SUDOERS"
visudo -cf /etc/sudoers >/dev/null
! grep -Eq 'NOPASSWD:[[:space:]]*ALL' "$SUDOERS" || fail 'broad sudo grant detected'
[[ "$(stat -c '%U:%G:%a' "$TARGET")" == 'root:root:755' ]] || fail 'operator ownership/mode mismatch'
[[ "$(stat -c '%U:%G:%a' "$SUDOERS")" == 'root:root:440' ]] || fail 'sudoers ownership/mode mismatch'

sudo -u ocarun sudo -n "$TARGET" >/dev/null
if sudo -u ocarun sudo -n "$TARGET" unexpected >/dev/null 2>&1; then
  fail 'preflight sudo surface unexpectedly accepts arguments'
fi
completed=1
echo UV003_PREFLIGHT_OPERATOR_INSTALLED
