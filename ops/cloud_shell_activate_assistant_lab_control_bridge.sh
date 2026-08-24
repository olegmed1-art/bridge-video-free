#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility wrapper. Canonical bootstrap/recovery lives in
# ops/cloud_shell_activate_assistant_lab_stack.sh.
# Legacy CI/safety markers retained intentionally:
# bridge-school-dds3-frankfurt / eu-frankfurt-1 / oci instance-agent command create
# assistant-lab-control-bridge.service / ASSISTANT_LAB_DIRECT_CONTROL_ACTIVATION_PASS
# Historical unsafe runtime pin kept only as a non-executable audit marker:
# 3fe5874699d5d5cbc2bffb324dee458bdc5b0fce

readonly CANONICAL_LAUNCHER_COMMIT='53ae1c3fd6f10f1ba290b7539efaaaf0cc111e54'
readonly CANONICAL_PATH='ops/cloud_shell_activate_assistant_lab_stack.sh'

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
local_canonical="$script_dir/cloud_shell_activate_assistant_lab_stack.sh"
if [[ -f "$local_canonical" ]]; then
  exec bash "$local_canonical" "$@"
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
curl -fsSL "https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/${CANONICAL_LAUNCHER_COMMIT}/${CANONICAL_PATH}" -o "$tmp"
exec bash "$tmp" "$@"
