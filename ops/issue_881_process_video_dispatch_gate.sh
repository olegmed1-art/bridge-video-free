#!/usr/bin/env bash

set -Eeuo pipefail

: "${GH_TOKEN:?GitHub token is required}"
: "${GITHUB_REPOSITORY:?GitHub repository is required}"
: "${RUNNER_TEMP:?Runner temp directory is required}"

readonly action="${1:-}"
readonly state_file="${2:-}"
readonly workflow_file='process-video.yml'
readonly workflow_path='.github/workflows/process-video.yml'
readonly workflow_name='Process bridge video'

[[ "$state_file" == "$RUNNER_TEMP/issue-881-process-video-workflow-state" ]] \
  || { echo 'Process-video workflow state path is invalid' >&2; exit 70; }

read_workflow(){
  local document
  document="$(gh api --method GET \
    "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow_file")" \
    || { echo 'Process-video workflow lookup failed' >&2; return 1; }
  jq -e --arg path "$workflow_path" --arg name "$workflow_name" '
    type == "object"
    and (.id | type) == "number"
    and (.id | floor) == .id
    and .id > 0
    and .path == $path
    and .name == $name
    and (.state == "active" or .state == "disabled_manually")
  ' <<<"$document" >/dev/null \
    || { echo 'Process-video workflow identity or state is unsafe' >&2; return 1; }
  jq -r '.state' <<<"$document"
}

validate_state_file(){
  [[ -f "$state_file" && ! -L "$state_file" ]] \
    || { echo 'Process-video workflow initial-state receipt is missing or unsafe' >&2; return 1; }
  [[ "$(stat -c '%u:%a:%h' "$state_file")" == "$(id -u):600:1" ]] \
    || { echo 'Process-video workflow initial-state receipt metadata is unsafe' >&2; return 1; }
  [[ "$(wc -l < "$state_file")" == 1 ]] \
    || { echo 'Process-video workflow initial-state receipt is ambiguous' >&2; return 1; }
  grep -Exq 'active|disabled_manually' "$state_file" \
    || { echo 'Process-video workflow initial state is invalid' >&2; return 1; }
}

case "$action" in
  suspend)
    [[ ! -e "$state_file" && ! -L "$state_file" ]] \
      || { echo 'Process-video workflow initial-state receipt already exists' >&2; exit 1; }
    initial_state="$(read_workflow)"
    (umask 077; set -o noclobber; printf '%s\n' "$initial_state" > "$state_file")
    validate_state_file
    changed=false
    if [[ "$initial_state" == active ]]; then
      gh api --method PUT \
        "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow_file/disable" >/dev/null
      changed=true
    fi
    [[ "$(read_workflow)" == disabled_manually ]] \
      || { echo 'Process-video workflow dispatch was not suspended' >&2; exit 1; }
    printf 'PROCESS_VIDEO_DISPATCH_SUSPEND initial_state=%s final_state=disabled_manually changed=%s result=PASS\n' \
      "$initial_state" "$changed"
    ;;
  verify)
    validate_state_file
    [[ "$(read_workflow)" == disabled_manually ]] \
      || { echo 'Process-video workflow dispatch suspension was lost' >&2; exit 1; }
    echo 'PROCESS_VIDEO_DISPATCH_VERIFY state=disabled_manually result=PASS'
    ;;
  restore)
    validate_state_file
    initial_state="$(cat "$state_file")"
    case "$initial_state" in
      active)
        gh api --method PUT \
          "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow_file/enable" >/dev/null
        ;;
      disabled_manually)
        gh api --method PUT \
          "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow_file/disable" >/dev/null
        ;;
      *) echo 'Process-video workflow restoration state is invalid' >&2; exit 1 ;;
    esac
    [[ "$(read_workflow)" == "$initial_state" ]] \
      || { echo 'Process-video workflow state restoration failed' >&2; exit 1; }
    printf 'PROCESS_VIDEO_DISPATCH_RESTORE initial_state=%s final_state=%s result=PASS\n' \
      "$initial_state" "$initial_state"
    ;;
  *) echo 'Usage: issue_881_process_video_dispatch_gate.sh suspend|verify|restore STATE_FILE' >&2; exit 64 ;;
esac
