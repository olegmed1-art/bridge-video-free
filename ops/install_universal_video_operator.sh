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
install -d -o root -g root -m 0700 /opt/bridge-school/.universal-video-staging || fail 'staging directory install failed'
tmp="$(mktemp)" || fail 'temporary sudoers file unavailable'; trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<'EOF'
# Bounded generic Universal Video controls.
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video submit-drive-base64 *
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video status *
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video enqueue-batch-base64 *
ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video batch-status *
EOF
chmod 0440 "$tmp" || fail 'temporary sudoers mode failed'
visudo -cf "$tmp" >/dev/null || fail 'operator sudoers validation failed'
install -o root -g root -m 0755 "$SOURCE_FILE" "$TARGET" || fail 'operator target install failed'
install -o root -g root -m 0440 "$tmp" "$SUDOERS" || fail 'operator sudoers install failed'
visudo -cf /etc/sudoers >/dev/null || fail 'system sudoers validation failed'
# Retire the three historical video-specific ingress surfaces only after the
# generic Drive-only operator and its exact sudo rule are installed and valid.
for obsolete in \
  /usr/local/sbin/universal-video-diana11 \
  /usr/local/sbin/universal-video-diana11-002 \
  /usr/local/sbin/universal-video-diana11-003 \
  /etc/sudoers.d/universal-video-diana11-ocarun \
  /etc/sudoers.d/universal-video-diana11-002-ocarun \
  /etc/sudoers.d/universal-video-diana11-003-ocarun; do
  if [[ -e "$obsolete" || -L "$obsolete" ]]; then
    [[ -f "$obsolete" && ! -L "$obsolete" ]] || fail "unsafe obsolete ingress target: $obsolete"
    rm -f -- "$obsolete"
  fi
done
visudo -cf /etc/sudoers >/dev/null || fail 'post-retirement sudoers validation failed'
echo UNIVERSAL_VIDEO_OPERATOR_INSTALL_PASS
