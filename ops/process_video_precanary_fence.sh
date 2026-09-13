#!/usr/bin/env bash

set -Eeuo pipefail

: "${GH_TOKEN:?GitHub token is required}"
: "${GITHUB_REPOSITORY:?GitHub repository is required}"
: "${GITHUB_RUN_ID:?GitHub run id is required}"
: "${RUNNER_TEMP:?Runner temp directory is required}"

[[ "$GITHUB_RUN_ID" =~ ^[1-9][0-9]*$ ]] \
  || { echo 'Current process-video run id is invalid' >&2; exit 70; }

readonly process_workflow_path='.github/workflows/process-video.yml'
readonly precanary_workflow='issue-881-authoritative-external-evidence.yml'
readonly precanary_workflow_path='.github/workflows/issue-881-authoritative-external-evidence.yml'
readonly max_wait_seconds="${PROCESS_VIDEO_PRECANARY_MAX_WAIT_SECONDS:-7500}"
readonly poll_seconds="${PROCESS_VIDEO_PRECANARY_POLL_SECONDS:-30}"

[[ "$max_wait_seconds" =~ ^[0-9]+$ && "$poll_seconds" =~ ^[1-9][0-9]*$ ]] \
  || { echo 'Process-video fence timing is invalid' >&2; exit 70; }
(( max_wait_seconds <= 7500 && poll_seconds <= 60 )) \
  || { echo 'Process-video fence timing exceeds its bounded contract' >&2; exit 70; }

verify_current_run(){
  local snapshot
  snapshot="$(gh api --method GET \
    "repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID")" \
    || { echo 'Current process-video run lookup failed' >&2; return 1; }
  jq -e --argjson run_id "$GITHUB_RUN_ID" --arg path "$process_workflow_path" '
    type == "object"
    and .id == $run_id
    and .path == $path
    and .event == "workflow_dispatch"
    and (.status == "requested" or .status == "waiting"
         or .status == "pending" or .status == "queued"
         or .status == "in_progress")
  ' <<<"$snapshot" >/dev/null \
    || { echo 'Current process-video run is not an active workflow witness' >&2; return 1; }
}

collect_precanary_sweep(){
  local direction="$1" output="$2" status snapshot loaded_total unique_total parts
  local -a statuses=()
  case "$direction" in
    forward) statuses=(requested waiting pending queued in_progress) ;;
    reverse) statuses=(in_progress queued pending waiting requested) ;;
    *) echo 'Process-video fence sweep direction is invalid' >&2; return 1 ;;
  esac
  parts="$(mktemp "$RUNNER_TEMP/process-video-precanary-$direction.XXXXXX")"
  : > "$parts"
  for status in "${statuses[@]}"; do
    snapshot="$(gh api --method GET \
      "repos/$GITHUB_REPOSITORY/actions/workflows/$precanary_workflow/runs?status=$status&per_page=100")" \
      || { rm -f "$parts"; echo "Pre-canary run query failed for status: $status" >&2; return 1; }
    jq -e --arg status "$status" --arg path "$precanary_workflow_path" '
      type == "object"
      and (.total_count | type) == "number"
      and (.total_count | floor) == .total_count
      and .total_count >= 0
      and .total_count <= 100
      and (.workflow_runs | type) == "array"
      and (.workflow_runs | length) == .total_count
      and all(.workflow_runs[];
        (.id | type) == "number"
        and (.id | floor) == .id
        and .id > 0
        and .path == $path
        and .status == $status)
    ' <<<"$snapshot" >/dev/null \
      || { rm -f "$parts"; echo "Pre-canary run snapshot is incomplete for status: $status" >&2; return 1; }
    loaded_total="$(jq '.workflow_runs | length' <<<"$snapshot")" || { rm -f "$parts"; return 1; }
    unique_total="$(jq '[.workflow_runs[].id] | unique | length' <<<"$snapshot")" \
      || { rm -f "$parts"; return 1; }
    [[ "$loaded_total" == "$unique_total" ]] \
      || { rm -f "$parts"; echo "Pre-canary run snapshot has duplicate ids for status: $status" >&2; return 1; }
    jq -c '.workflow_runs' <<<"$snapshot" >> "$parts" || { rm -f "$parts"; return 1; }
  done
  jq -cs 'add | unique_by(.id) | sort_by(.id)' "$parts" > "$output" \
    || { rm -f "$parts"; return 1; }
  rm -f "$parts"
  jq -e 'type == "array"' "$output" >/dev/null \
    || { echo 'Process-video fence sweep output is unsafe' >&2; return 1; }
}

deadline=$((SECONDS + max_wait_seconds))
forward="$RUNNER_TEMP/process-video-precanary-forward.json"
reverse="$RUNNER_TEMP/process-video-precanary-reverse.json"
combined="$RUNNER_TEMP/process-video-precanary-active.json"

while :; do
  verify_current_run
  collect_precanary_sweep forward "$forward"
  collect_precanary_sweep reverse "$reverse"
  jq -s 'add | unique_by(.id) | sort_by(.id)' "$forward" "$reverse" > "$combined"
  blocker_count="$(jq 'length' "$combined")"
  [[ "$blocker_count" =~ ^[0-9]+$ ]] \
    || { echo 'Process-video pre-canary blocker count is invalid' >&2; exit 1; }
  if (( blocker_count == 0 )); then
    verify_current_run
    echo 'PROCESS_VIDEO_PRECANARY_FENCE_PASS active_precanary=0 request_preserved=true'
    exit 0
  fi
  blocker_ids="$(jq -r '[.[].id | tostring] | join(",")' "$combined")"
  if (( SECONDS >= deadline )); then
    printf 'Process-video request remains preserved in run %s, but pre-canary did not become terminal; blockers=%s\n' \
      "$GITHUB_RUN_ID" "$blocker_ids" >&2
    exit 75
  fi
  printf 'PROCESS_VIDEO_PRECANARY_FENCE_WAIT blockers=%s\n' "$blocker_ids"
  sleep "$poll_seconds"
done
