#!/usr/bin/env bash
# Installs only the narrow generic submit/status control plane.
set -Eeuo pipefail
umask 077
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail 'must run as root'
: "${SOURCE_FILE:?SOURCE_FILE is required}"
: "${EXPECTED_RUNTIME_COMMIT:?EXPECTED_RUNTIME_COMMIT is required}"
[[ -f "$SOURCE_FILE" && ! -L "$SOURCE_FILE" ]] || fail 'operator source must be regular'
[[ "$EXPECTED_RUNTIME_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail 'invalid runtime commit'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly TARGET='/usr/local/sbin/universal-video'
# The generic submit/status surface and the OCI admin surface have independent
# ownership. Sharing one sudoers path lets either installer erase the other's
# exact grants during a later idempotent bootstrap.
readonly SUDOERS='/etc/sudoers.d/universal-video-operator-ocarun'
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$EXPECTED_RUNTIME_COMMIT" ]] || fail 'runtime commit mismatch'
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all)" ]] || fail 'runtime checkout is dirty'
[[ "$(git -C "$SOURCE_DIR" rev-parse "$EXPECTED_RUNTIME_COMMIT:ops/universal_video_operator.sh")" == "$(git hash-object "$SOURCE_FILE")" ]] || fail 'operator does not match checkout'
install -d -o root -g root -m 0700 /opt/bridge-school/.universal-video-staging
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<'EOF'
# Bounded generic Universal Video controls.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video submit-base64 *
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video status *
EOF
chmod 0440 "$tmp"; visudo -cf "$tmp" >/dev/null
install -o root -g root -m 0755 "$SOURCE_FILE" "$TARGET"
install -o root -g root -m 0440 "$tmp" "$SUDOERS"
visudo -cf /etc/sudoers >/dev/null
echo UNIVERSAL_VIDEO_OPERATOR_INSTALL_PASS
