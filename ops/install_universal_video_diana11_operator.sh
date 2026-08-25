#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# One-time root bootstrap for exactly two Diana 11 operator commands.
# No shell, editor, arbitrary path, or NOPASSWD:ALL is granted.

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
: "${SOURCE_FILE:?SOURCE_FILE is required}"
[[ -f "$SOURCE_FILE" && ! -L "$SOURCE_FILE" ]] || fail 'operator source must be a regular file'
bash -n "$SOURCE_FILE"
grep -Fq "readonly JOB_ID='diana11-transcript-20260825-01'" "$SOURCE_FILE" || fail 'unexpected job id'
grep -Fq "readonly DRIVE_FILE_ID='1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C'" "$SOURCE_FILE" || fail 'unexpected Drive id'
! grep -Eq '(^|[[:space:]])(bash|sh)[[:space:]]+-c' "$SOURCE_FILE" || fail 'dynamic shell execution forbidden'
id ocarun >/dev/null 2>&1 || fail 'ocarun user missing'
command -v visudo >/dev/null || fail 'visudo required'

readonly TARGET='/usr/local/sbin/universal-video-diana11'
readonly SUDOERS='/etc/sudoers.d/universal-video-diana11-ocarun'
install -o root -g root -m 0755 "$SOURCE_FILE" "$TARGET"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT INT TERM
cat > "$tmp" <<'EOF'
# Exact single-job Universal Video acceptance controls for Diana 11.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11 submit
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-diana11 status
EOF
chmod 0440 "$tmp"
visudo -cf "$tmp" >/dev/null
install -o root -g root -m 0440 "$tmp" "$SUDOERS"
visudo -cf /etc/sudoers >/dev/null
! grep -Eq 'NOPASSWD:[[:space:]]*ALL' "$SUDOERS" || fail 'broad sudo grant detected'
[[ "$(stat -c '%U:%G:%a' "$TARGET")" == 'root:root:755' ]] || fail 'operator ownership/mode mismatch'
[[ "$(stat -c '%U:%G:%a' "$SUDOERS")" == 'root:root:440' ]] || fail 'sudoers ownership/mode mismatch'

sudo -u ocarun sudo -n "$TARGET" status >/dev/null
echo UNIVERSAL_VIDEO_DIANA11_OPERATOR_BOOTSTRAP_PASS
