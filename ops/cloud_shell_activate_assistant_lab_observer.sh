#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility wrapper. Canonical bootstrap/recovery lives in
# ops/cloud_shell_activate_assistant_lab_stack.sh.
# Legacy CI/safety markers retained intentionally:
# bridge-school-dds3-frankfurt / eu-frankfurt-1 / oci instance-agent command create
# ASSISTANT_LAB_OBSERVER_HOST_ACTIVATION_PASS
# arbitrary_shell / video_analyzer_result_access / other_oracle_result_access

readonly CANONICAL_LAUNCHER_COMMIT='b47be399c683d3f8a050ebff530ed3cadbe45750'
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
