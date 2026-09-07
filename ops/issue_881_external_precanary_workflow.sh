#!/usr/bin/env bash

set -Eeuo pipefail
[[ "$SSH_KEY_FILE" == "$RUNNER_TEMP/id_oracle" \
  && -f "$SSH_KEY_FILE" && ! -L "$SSH_KEY_FILE" \
  && "$(stat -c '%a' "$SSH_KEY_FILE")" == 600 ]] \
  || { echo 'Exact Oracle SSH identity file is missing or unsafe' >&2; exit 1; }
evidence="$RUNNER_TEMP/issue-881-authoritative-external-evidence.txt"
session_log="$RUNNER_TEMP/precanary-session.log"
recovery_evidence=''
recovery_sha=''
recovery_remote_file=''
attester_pid=''
remote_attester_terminal=1
owner_release_file=''
owner_abort_file=''
database_gate_pid=''
database_gate_terminal=1
database_gate_ready_file="$RUNNER_TEMP/issue-881-owner-gate-ready.txt"
database_gate_control_file="$RUNNER_TEMP/issue-881-owner-gate-control.txt"
database_gate_output="$RUNNER_TEMP/issue-881-owner-gate-output.txt"
database_gate_marker_file="$RUNNER_TEMP/issue-881-db-enqueue-fence-marker.txt"

root_pr_number=991
prior_gate_pr_number=1070
protected_gate_paths=(
  '.dockerignore'
  '.github/workflows/issue-881-authoritative-external-evidence.yml'
  '.github/workflows/issue-881-contract-ci.yml'
  '.github/workflows/issue-881-current-main-authoritative-ci.yml'
  '.github/workflows/issue-881-precanary-evidence.yml'
  '.github/workflows/oracle-idle-guard-ci.yml'
  '.github/workflows/oracle-assistant-lab-oci-diagnostic.yml'
  '.github/workflows/oracle-assistant-lab-control-rollout.yml'
  '.github/workflows/oracle-assistant-lab-worker-rollout.yml'
  '.github/workflows/oracle-ben-runtime-rollout.yml'
  '.github/workflows/oracle-diana11-002-delivery.yml'
  '.github/workflows/oracle-diana11-002-job.yml'
  '.github/workflows/oracle-diana11-delivery.yml'
  '.github/workflows/oracle-instance-power.yml'
  '.github/workflows/oracle-operational-safety-gate.yml'
  '.github/workflows/oracle-operator-commands.yml'
  '.github/workflows/oracle-operator-v2.yml'
  '.github/workflows/oracle-operator-v3.yml'
  '.github/workflows/oracle-universal-video-activation.yml'
  '.github/workflows/oracle-universal-video-admin.yml'
  '.github/workflows/oracle-universal-video-container-evidence.yml'
  '.github/workflows/oracle-universal-video-container-promote.yml'
  '.github/workflows/oracle-universal-video-evidence-export.yml'
  '.github/workflows/oracle-universal-video-job.yml'
  '.github/workflows/oracle-universal-video-queue-credential-install.yml'
  '.github/workflows/database-production.yml'
  '.github/workflows/process-video.yml'
  '.github/workflows/video-job-monitor.yml'
  '.github/workflows/secret-gate.yml'
  '.github/workflows/universal-video-ci.yml'
  '.github/workflows/universal-video-engine-smoke.yml'
  'requirements-worker.txt'
  'ops/issue_881_external_precanary_workflow.sh'
  'ops/issue_881_precanary_one_shot.py'
  'ops/issue_881_precanary_queue_proof.py'
  'ops/verify_oci_instance_command_executions.py'
  'ops/install_universal_video_operator.sh'
  'ops/oracle_known_hosts_from_scan.sh'
  'ops/oracle_universal_video_run_command.sh'
  'ops/oracle_universal_video_precanary_attest.sh'
  'ops/oracle_universal_video_container_install.sh'
  'ops/oracle_universal_video_container_promote.sh'
  'ops/oracle_universal_video_prepromotion_preflight.sh'
  'ops/universal_video_operator.sh'
  'ops/validate_video_queue_dsn.py'
  'ops/validate_universal_video_promotion_evidence.py'
  'universal_video'
  'bridge_contracts'
  'bridge_vision'
  'database'
  ':(glob)bridge_*.py'
  'diana_longitudinal_quality_v2.py'
  'r29_identity_overlay_probe.py'
  'route_drive_job_outputs.py'
  ':(glob)run_*.py'
  'transcript_stage_checkpoint_v1.py'
  'deploy/oracle-universal-video/Dockerfile'
  'deploy/oracle-universal-video/universal-video-container-entrypoint.sh'
  'deploy/oracle-universal-video/universal-video-container.service'
)
required_workflows=(
  'Issue 881 Exact Canary Contract CI'
  'Issue 881 Exact Pre-Canary Evidence'
  'Issue 881 Current-Main Authoritative CI'
  'Secret gate'
)
root_required_workflows=(
  'Universal Video Analyzer CI'
  'Issue 881 Exact Canary Contract CI'
  'Issue 881 Exact Pre-Canary Evidence'
  'Oracle Universal Video Activation'
  'Migration Namespace Guard'
  'Secret gate'
  'Universal Video Engine Smoke'
  'Bridge School Database CI'
)

verify_live_gate(){
  local main_sha gate_commit associated_json pr_count pr_number pr_json live_head live_state reviewed_sha reviewed_ref runs_endpoint root_pr_json root_merge_sha root_reviewed_sha prior_gate_pr_json prior_gate_merge_sha gate_merge_sha review_count codex_comments_json codex_clean_count reviews_json approval_count threads_json blocker_count runs_json root_runs_json workflow_name latest_state
  main_sha="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq '.object.sha')"
  [[ "$main_sha" == "$EXACT_SHA" ]] \
    || { echo 'Exact SHA is not immutable current main' >&2; return 1; }
  gate_commit="$(git log -1 --format=%H "$EXACT_SHA" -- "${protected_gate_paths[@]}")"
  [[ "$gate_commit" =~ ^[0-9a-f]{40}$ ]] \
    || { echo 'Protected pre-canary gate commit is unavailable' >&2; return 1; }
  associated_json="$(gh api -H 'Accept: application/vnd.github+json' \
    "repos/$GITHUB_REPOSITORY/commits/$gate_commit/pulls")"
  pr_count="$(jq --arg commit "$gate_commit" \
    '[.[] | select(.merged_at != null and .merge_commit_sha == $commit)] | length' \
    <<<"$associated_json")"
  [[ "$pr_count" == 1 ]] \
    || { echo 'Protected gate commit is not bound to one merged PR' >&2; return 1; }
  pr_number="$(jq -r --arg commit "$gate_commit" \
    '.[] | select(.merged_at != null and .merge_commit_sha == $commit) | .number' \
    <<<"$associated_json")"
  pr_json="$(gh api "repos/$GITHUB_REPOSITORY/pulls/$pr_number")"
  live_state="$(jq -r '.state' <<<"$pr_json")"
  [[ "$live_state" == 'closed' && "$(jq -r '.merged' <<<"$pr_json")" == true ]] \
    || { echo 'Current protected pre-canary gate PR is not merged' >&2; return 1; }
  gate_merge_sha="$(jq -r '.merge_commit_sha' <<<"$pr_json")"
  [[ "$gate_merge_sha" == "$gate_commit" ]] \
    || { echo 'Protected gate PR merge identity changed unexpectedly' >&2; return 1; }
  live_head="$(jq -r '.head.sha' <<<"$pr_json")"
  reviewed_sha="$live_head"
  [[ "$reviewed_sha" =~ ^[0-9a-f]{40}$ ]]
  reviewed_ref="refs/remotes/origin/issue881-reviewed-$pr_number"
  git fetch --no-tags --force origin \
    "pull/$pr_number/head:$reviewed_ref" >/dev/null 2>&1
  [[ "$(git rev-parse "$reviewed_ref")" == "$reviewed_sha" ]] \
    || { echo 'Reviewed gate head cannot be fetched exactly' >&2; return 1; }
  git diff --quiet "$reviewed_sha" "$gate_commit" -- "${protected_gate_paths[@]}" \
    || { echo 'Merged protected gate files differ from the reviewed head' >&2; return 1; }
  [[ "$(git log -1 --format=%H "$EXACT_SHA" -- "${protected_gate_paths[@]}")" == "$gate_commit" ]] \
    || { echo 'Protected gate files changed after the reviewed merge' >&2; return 1; }
  runs_endpoint="repos/$GITHUB_REPOSITORY/actions/runs?head_sha=$reviewed_sha&event=pull_request&per_page=100"
  root_pr_json="$(gh api "repos/$GITHUB_REPOSITORY/pulls/$root_pr_number")"
  [[ "$(jq -r '.merged' <<<"$root_pr_json")" == true ]] \
    || { echo 'Root Autopilot PR #991 is not merged' >&2; return 1; }
  root_merge_sha="$(jq -r '.merge_commit_sha' <<<"$root_pr_json")"
  [[ "$root_merge_sha" =~ ^[0-9a-f]{40}$ ]]
  prior_gate_pr_json="$(gh api "repos/$GITHUB_REPOSITORY/pulls/$prior_gate_pr_number")"
  [[ "$(jq -r '.merged' <<<"$prior_gate_pr_json")" == true ]] \
    || { echo 'Prior canary gate PR #1070 is not merged' >&2; return 1; }
  prior_gate_merge_sha="$(jq -r '.merge_commit_sha' <<<"$prior_gate_pr_json")"
  [[ "$prior_gate_merge_sha" =~ ^[0-9a-f]{40}$ ]]
  git merge-base --is-ancestor "$root_merge_sha" "$prior_gate_merge_sha" \
    || { echo 'Root Autopilot merge is not an ancestor of the prior gate merge' >&2; return 1; }
  git merge-base --is-ancestor "$prior_gate_merge_sha" "$gate_merge_sha" \
    || { echo 'Prior gate merge is not an ancestor of the reviewed canary gate merge' >&2; return 1; }
  git merge-base --is-ancestor "$gate_merge_sha" "$EXACT_SHA" \
    || { echo 'Reviewed canary gate merge is not an ancestor of exact current main' >&2; return 1; }
  root_reviewed_sha="$(jq -r '.head.sha' <<<"$root_pr_json")"
  [[ "$root_reviewed_sha" =~ ^[0-9a-f]{40}$ ]]

  reviews_json="$(gh api --paginate --slurp "repos/$GITHUB_REPOSITORY/pulls/$pr_number/reviews?per_page=100")"
  review_count="$(jq --arg sha "$reviewed_sha" \
    '[.[].[] | select(.commit_id == $sha and (.user.login | startswith("chatgpt-codex-connector")) and (.state == "COMMENTED" or .state == "APPROVED"))] | length' \
    <<<"$reviews_json")"
  codex_comments_json="$(gh api --paginate --slurp \
    "repos/$GITHUB_REPOSITORY/issues/$pr_number/comments?per_page=100")"
  codex_clean_count="$(jq --arg sha "$reviewed_sha" \
    '[.[].[] | select(.user.login == "chatgpt-codex-connector[bot]") | select(.body | contains("Codex Review: Didn\u0027t find any major issues.")) | select(.body | contains("**Reviewed commit:** `" + $sha + "`"))] | length' \
    <<<"$codex_comments_json")"
  (( review_count > 0 || codex_clean_count > 0 )) \
    || { echo 'Exact head has neither a Codex review object nor an exact clean Codex bot receipt' >&2; return 1; }
  threads_json="$(gh api graphql \
    -f owner="${GITHUB_REPOSITORY%%/*}" \
    -f name="${GITHUB_REPOSITORY#*/}" \
    -F number="$pr_number" \
    -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100){pageInfo{hasNextPage} nodes{isResolved isOutdated comments(first:100){nodes{author{login}}}}}}}}')"
  [[ "$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<<"$threads_json")" == false ]] \
    || { echo 'Review thread set exceeds the bounded verification page' >&2; return 1; }
  blocker_count="$(jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select((.isResolved | not) and (.isOutdated | not)) | select(any(.comments.nodes[]; (.author.login // "") | startswith("chatgpt-codex-connector")))] | length' <<<"$threads_json")"
  [[ "$blocker_count" == 0 ]] \
    || { echo "Independent review still has $blocker_count unresolved current threads" >&2; return 1; }

  runs_json="$(gh api --paginate --slurp "$runs_endpoint")"
  for workflow_name in "${required_workflows[@]}"; do
    latest_state="$(jq -r --arg name "$workflow_name" \
      '[.[].workflow_runs[] | select(.name == $name)] | if length == 0 then "MISSING" else (max_by([.run_number, .run_attempt]) | (.status + ":" + (.conclusion // ""))) end' \
      <<<"$runs_json")"
    [[ "$latest_state" == 'completed:success' ]] \
      || { echo "Latest exact-head workflow attempt is not green: $workflow_name ($latest_state)" >&2; return 1; }
  done
  root_runs_json="$(gh api --paginate --slurp \
    "repos/$GITHUB_REPOSITORY/actions/runs?head_sha=$root_reviewed_sha&event=pull_request&per_page=100")"
  for workflow_name in "${root_required_workflows[@]}"; do
    latest_state="$(jq -r --arg name "$workflow_name" \
      '[.[].workflow_runs[] | select(.name == $name)] | if length == 0 then "MISSING" else (max_by([.run_number, .run_attempt]) | (.status + ":" + (.conclusion // ""))) end' \
      <<<"$root_runs_json")"
    [[ "$latest_state" == 'completed:success' ]] \
      || { echo "Latest root exact-head workflow attempt is not green: $workflow_name ($latest_state)" >&2; return 1; }
  done
  reviews_json="$(gh api --paginate --slurp "repos/$GITHUB_REPOSITORY/pulls/$pr_number/reviews?per_page=100")"
  approval_count="$(jq --arg sha "$reviewed_sha" --arg owner "$GITHUB_REPOSITORY_OWNER" \
    '[.[].[] | select(.commit_id == $sha and .user.login != $owner)] | group_by(.user.login) | map(max_by(.submitted_at)) | map(select(.state == "APPROVED")) | length' \
    <<<"$reviews_json")"
  (( approval_count > 0 )) \
    || { echo 'Reviewed head has no current independent approval at final reconciliation' >&2; return 1; }
  [[ "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq '.object.sha')" == "$EXACT_SHA" ]] \
    || { echo 'Main changed while live review and CI gates were evaluated' >&2; return 1; }
}

verify_one_shot_gate(){
  local current_marker initial_marker
  [[ "$GITHUB_RUN_ATTEMPT" == 1 ]] \
    || { echo 'Workflow reruns are forbidden' >&2; return 1; }
  gh api "repos/$GITHUB_REPOSITORY/issues/comments/$APPROVAL_RECEIPT_ID" \
    > "$RUNNER_TEMP/precanary-approval-comment-final.json"
  gh api --paginate --slurp \
    "repos/$GITHUB_REPOSITORY/actions/runs?event=workflow_dispatch&per_page=100" \
    > "$RUNNER_TEMP/precanary-workflow-runs-final.json"
  current_marker="$(python3 ops/issue_881_precanary_one_shot.py verify \
    --comment-json "$RUNNER_TEMP/precanary-approval-comment-final.json" \
    --runs-json "$RUNNER_TEMP/precanary-workflow-runs-final.json" \
    --exact-sha "$EXACT_SHA" \
    --approval-nonce "$APPROVAL_NONCE" \
    --recover-container-from-run "$RECOVER_CONTAINER_FROM_RUN" \
    --receipt-id "$APPROVAL_RECEIPT_ID" \
    --current-run-id "$GITHUB_RUN_ID" \
    --current-run-attempt "$GITHUB_RUN_ATTEMPT")" || return 1
  initial_marker="$(cat "$RUNNER_TEMP/precanary-one-shot-marker.txt")"
  [[ "$current_marker" == "$initial_marker" ]] \
    || { echo 'One-shot receipt changed after initial validation' >&2; return 1; }
}

verify_exact_current_main(){
  local current_main
  current_main="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq '.object.sha')"
  [[ "$current_main" == "$EXACT_SHA" ]] \
    || { echo 'Main changed at the final pre-canary mutation boundary' >&2; return 1; }
  printf 'UNIVERSAL_VIDEO_PRECANARY_FINAL_MAIN exact_sha=%s result=PASS\n' "$EXACT_SHA"
}

collect_active_workflow_run_sweep(){
  local direction="$1" destination="$2" parts status snapshot
  local reported_total loaded_total unique_total valid_shape
  local -a statuses=()
  case "$direction" in
    forward) statuses=(requested waiting pending queued in_progress) ;;
    reverse) statuses=(in_progress queued pending waiting requested) ;;
    *) echo 'Active workflow sweep direction is invalid' >&2; return 1 ;;
  esac
  parts="${destination}.parts"
  rm -f -- "$destination" "$parts"
  (umask 077; : > "$parts")

  for status in "${statuses[@]}"; do
    # Active populations must fit in one bounded response.  Never walk the
    # completed run history: if an active state ever exceeds the API page,
    # completeness is uncertain and the pre-canary stops fail-closed.
    snapshot="$(gh api \
      "repos/$GITHUB_REPOSITORY/actions/runs?status=$status&per_page=100")" || {
      echo "Active workflow query failed for status: $status" >&2
      return 1
    }
    valid_shape="$(jq --arg status "$status" \
      'try (
        type == "object"
        and (.total_count | type) == "number"
        and .total_count >= 0
        and .total_count == (.total_count | floor)
        and (.workflow_runs | type) == "array"
        and all(.workflow_runs[];
          (.id | type) == "number"
          and .id > 0
          and .id == (.id | floor)
          and (.path | type) == "string"
          and (.path | length) > 0
          and .status == $status)
      ) catch false' <<<"$snapshot")" || return 1
    reported_total="$(jq -r '.total_count' <<<"$snapshot")" || return 1
    loaded_total="$(jq '.workflow_runs | length' <<<"$snapshot")" || return 1
    unique_total="$(jq '[.workflow_runs[].id] | unique | length' \
      <<<"$snapshot")" || return 1
    [[ "$valid_shape" == true \
      && "$reported_total" =~ ^[0-9]+$ \
      && "$reported_total" == "$loaded_total" \
      && "$reported_total" -le 100 \
      && "$loaded_total" == "$unique_total" ]] || {
      echo "Active workflow snapshot is incomplete for status: $status" >&2
      return 1
    }
    jq -c '.workflow_runs' <<<"$snapshot" >> "$parts" || return 1
  done

  (umask 077; jq -cs 'add | unique_by(.id)' "$parts" > "$destination") \
    || return 1
  rm -f -- "$parts"
  [[ -f "$destination" && ! -L "$destination" \
    && "$(stat -c '%a:%h' "$destination")" == '600:1' ]] || {
    echo 'Active workflow sweep output is unsafe' >&2
    return 1
  }
}

verify_no_competing_infrastructure_runs(){
  local forward_runs reverse_runs current_forward current_reverse competing_count
  local path_pattern='^\.github/workflows/(oracle-|issue-881-|autopilot-|database-production\.yml$|database-worker-runtime-smoke\.yml$|process-video\.yml$|video-job-monitor\.yml$|bridge-ai-|research-job-|dds3-runtime-container-proof\.yml$|dds3-production-health-monitor\.yml$|dds-training-|dds-main-)'
  forward_runs="$RUNNER_TEMP/precanary-active-runs-forward.json"
  reverse_runs="$RUNNER_TEMP/precanary-active-runs-reverse.json"

  collect_active_workflow_run_sweep forward "$forward_runs" || return 1
  collect_active_workflow_run_sweep reverse "$reverse_runs" || return 1

  # The current run is a fail-closed API witness: both independently collected
  # active sweeps must see it exactly once.  This prevents an empty, stale, or
  # permission-filtered response from being accepted as infrastructure-idle.
  current_forward="$(jq --argjson current "$GITHUB_RUN_ID" \
    '[.[] | select(.id == $current)] | length' "$forward_runs")"
  current_reverse="$(jq --argjson current "$GITHUB_RUN_ID" \
    '[.[] | select(.id == $current)] | length' "$reverse_runs")"
  [[ "$current_forward" == 1 && "$current_reverse" == 1 ]] || {
    echo 'Current pre-canary run is missing from an active workflow sweep' >&2
    return 1
  }

  # Union both opposite-order sweeps.  The lifecycle-ordered first sweep closes
  # the queued -> in_progress transition gap, while the reverse sweep provides
  # a second independent read.  Host mutators cannot advance behind this check:
  # they share this run's non-cancelling Actions fence, and already-launched OCI
  # commands are reconciled separately at the final mutation boundary.
  competing_count="$(jq -s \
    --argjson current "$GITHUB_RUN_ID" \
    --arg pattern "$path_pattern" \
    '[.[][]
      | select(.id != $current)
      | select((.path // "") | test($pattern))]
     | unique_by(.id) | length' "$forward_runs" "$reverse_runs")"
  [[ "$competing_count" == 0 ]] || {
    jq -sr \
      --argjson current "$GITHUB_RUN_ID" \
      --arg pattern "$path_pattern" \
      '[.[][]
        | select(.id != $current)
        | select((.path // "") | test($pattern))
        | {id, path, status}] | unique_by(.id)' \
      "$forward_runs" "$reverse_runs" >&2
    echo 'A competing infrastructure workflow is active or queued' >&2
    return 1
  }
  echo 'UNIVERSAL_VIDEO_PRECANARY_INFRASTRUCTURE_EXCLUSIVE other_active=0 other_queued=0 result=PASS'
}

verify_no_active_instance_agent_commands(){
  local config="$RUNNER_TEMP/oci/config" compartment executions_file examined
  [[ -f "$config" && ! -L "$config" && "$(stat -c '%a' "$config")" == 600 ]] \
    || { echo 'Exact OCI configuration is missing or unsafe' >&2; return 1; }
  compartment="$(oci --config-file "$config" compute instance get \
    --instance-id "$INSTANCE_ID" --query 'data."compartment-id"' --raw-output)" \
    || return 1
  [[ "$compartment" =~ ^ocid1\.compartment\. ]] \
    || { echo 'Oracle compartment identity is invalid' >&2; return 1; }
  executions_file="$RUNNER_TEMP/precanary-oci-instance-command-executions.json"
  # This OCI API is scoped by the exact instance ID. Do not filter by display
  # name: any Run Command can outlive a cancelled Actions run and overlap the
  # resident lifecycle window.
  oci --config-file "$config" instance-agent command-execution list \
    --compartment-id "$compartment" --instance-id "$INSTANCE_ID" \
    --all --output json > "$executions_file" \
    || return 1
  examined="$(python3 ops/verify_oci_instance_command_executions.py \
    --executions-json "$executions_file" --instance-id "$INSTANCE_ID")" \
    || return 1
  [[ "$examined" =~ ^[0-9]+$ ]] \
    || { echo 'OCI Run Command execution count is invalid' >&2; return 1; }
  printf 'UNIVERSAL_VIDEO_PRECANARY_OCI_INSTANCE_COMMAND_EXCLUSIVE examined_instance_executions=%s active_remote_commands=0 result=PASS\n' \
    "$examined"
}

verify_final_mutation_boundary(){
  local current_infrastructure_marker current_oci_command_marker
  # Receipt validation performs its own paginated Actions read. Take
  # the complete infrastructure snapshot only after that read, then
  # make the exact-main query the final subcheck in this one bounded
  # reconciliation immediately before the host attester.
  current_infrastructure_marker="$(verify_no_competing_infrastructure_runs)" \
    || return 1
  [[ "$current_infrastructure_marker" == \
    "$(cat "$RUNNER_TEMP/precanary-infrastructure-marker.txt")" ]] \
    || { echo 'Infrastructure exclusivity changed before host mutation' >&2; return 1; }
  current_oci_command_marker="$(verify_no_active_instance_agent_commands)" \
    || return 1
  printf '%s\n' "$current_oci_command_marker" \
    | tee "$RUNNER_TEMP/precanary-oci-instance-command-marker.txt"
  verify_exact_current_main
  printf '%s\n' "$current_infrastructure_marker"
}

# Initial reconciliation rejects historical, blocked, or stale-green
# heads before any recovery artifact is accepted.
verify_no_competing_infrastructure_runs \
  | tee "$RUNNER_TEMP/precanary-infrastructure-marker.txt"
verify_live_gate

if [[ -n "$RECOVER_CONTAINER_FROM_RUN" ]]; then
  prior_run="$RUNNER_TEMP/prior-run.json"
  gh api "repos/$GITHUB_REPOSITORY/actions/runs/$RECOVER_CONTAINER_FROM_RUN" > "$prior_run"
  prior_head="$(jq -r '.head_sha' "$prior_run")"
  [[ "$prior_head" =~ ^[0-9a-f]{40}$ ]]
  [[ "$(jq -r '.path' "$prior_run")" == '.github/workflows/issue-881-authoritative-external-evidence.yml' ]]
  [[ "$(jq -r '.event' "$prior_run")" == workflow_dispatch ]]
  [[ "$(jq -r '.conclusion' "$prior_run")" == failure ]]
  mapfile -t artifact_ids < <(gh api --paginate \
    "repos/$GITHUB_REPOSITORY/actions/runs/$RECOVER_CONTAINER_FROM_RUN/artifacts?per_page=100" \
    --jq ".artifacts[] | select(.expired == false and .name == \"issue-881-authoritative-external-$prior_head\") | .id")
  [[ "${#artifact_ids[@]}" -eq 1 ]]
  artifact_zip="$RUNNER_TEMP/prior-recovery.zip"
  gh api "repos/$GITHUB_REPOSITORY/actions/artifacts/${artifact_ids[0]}/zip" > "$artifact_zip"
  recovery_evidence="$RUNNER_TEMP/prior-recovery-evidence.txt"
  python - "$artifact_zip" "$recovery_evidence" <<'PY'
import sys, zipfile
source, target = sys.argv[1:]
with zipfile.ZipFile(source) as archive:
    files = [item for item in archive.infolist() if not item.is_dir()]
    if len(files) != 1 or files[0].file_size > 1_000_000:
        raise SystemExit("unsafe prior-run artifact")
    data = archive.read(files[0])
open(target, "xb").write(data)
PY
  grep -Fx "runtime_sha=$prior_head" "$recovery_evidence" >/dev/null
  grep -Eq '^UNIVERSAL_VIDEO_PRECANARY_WINDOW .*container_service_before=active .*restore_on_exit=true$' "$recovery_evidence"
  grep -Eq '^UNIVERSAL_VIDEO_PRECANARY_RESTORE_FAILED .*container_service=(inactive|failed)$' "$recovery_evidence"
  grep -Fx 'real_media_canary_run=false' "$recovery_evidence" >/dev/null
  recovery_sha="$(sha256sum "$recovery_evidence" | awk '{print $1}')"
fi

# Revalidate the still-fresh one-shot receipt after any bounded
# evidence download and before the first SSH/SCP/root host action.
# The reviewed known-hosts helper is invoked only after the live
# protected-path provenance gate above has passed.
verify_one_shot_gate
known="$RUNNER_TEMP/known_hosts"
ops/oracle_known_hosts_from_scan.sh "$ORACLE_HOST" "$HOST_FINGERPRINT" "$known"
ssh -i "$SSH_KEY_FILE" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known" \
  -o ConnectTimeout=15 "$ORACLE_USER@$ORACLE_HOST" 'sudo -n true'

remote_stage="/tmp/uv-issue881-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
remote_root="/root/uv-issue881-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
s=(ssh -i "$SSH_KEY_FILE" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known" \
  -o ConnectTimeout=15 "$ORACLE_USER@$ORACLE_HOST")
c=(scp -i "$SSH_KEY_FILE" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known" \
  -o ConnectTimeout=15)

abort_remote_attester(){
  local attempt
  if [[ "$attester_pid" =~ ^[1-9][0-9]*$ ]]; then
    if kill -0 "$attester_pid" >/dev/null 2>&1; then
      # A local runner error must not merely sever SSH while the root cleanup
      # is waiting for owner proof. Stage an explicit invalidation control so
      # the host converges to its fail-closed restore path first.
      if [[ "$owner_abort_file" =~ ^/root/uv-issue881-[1-9][0-9]{7,19}-1/owner-abort$ ]]; then
        "${s[@]}" "set -e; \
          if sudo -n test ! -e '$owner_abort_file' && sudo -n test ! -L '$owner_abort_file'; then \
            printf 'ABORT\\n' > '$remote_stage/owner-abort'; \
            sudo -n install -o root -g root -m 0600 '$remote_stage/owner-abort' '$owner_abort_file'; \
            rm -f '$remote_stage/owner-abort'; \
          fi" >/dev/null 2>&1 || true
      fi
      for attempt in {1..120}; do
        kill -0 "$attester_pid" >/dev/null 2>&1 || break
        sleep 1
      done
      if kill -0 "$attester_pid" >/dev/null 2>&1; then
        remote_attester_terminal=0
        kill -TERM "$attester_pid" >/dev/null 2>&1 || true
        sleep 2
        kill -KILL "$attester_pid" >/dev/null 2>&1 || true
      fi
    fi
    wait "$attester_pid" >/dev/null 2>&1 || true
    attester_pid=''
  fi
  [[ "$remote_attester_terminal" == 1 ]]
}

write_database_gate_signal(){
  local signal="$1"
  [[ "$signal" == RELEASE || "$signal" == ABORT ]] || return 1
  [[ ! -e "$database_gate_control_file" && ! -L "$database_gate_control_file" ]] \
    || return 1
  DATABASE_GATE_CONTROL_FILE="$database_gate_control_file" \
    DATABASE_GATE_SIGNAL="$signal" python3 - <<'PY'
import os

path = os.environ["DATABASE_GATE_CONTROL_FILE"]
signal = os.environ["DATABASE_GATE_SIGNAL"]
if signal not in {"RELEASE", "ABORT"}:
    raise SystemExit(1)
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(path, flags, 0o600)
with os.fdopen(descriptor, "wb") as output:
    output.write((signal + "\n").encode("ascii"))
    output.flush()
    os.fsync(output.fileno())
PY
}

abort_database_gate(){
  local attempt
  if [[ "$database_gate_pid" =~ ^[1-9][0-9]*$ ]]; then
    if kill -0 "$database_gate_pid" >/dev/null 2>&1; then
      write_database_gate_signal ABORT >/dev/null 2>&1 || true
      for attempt in {1..150}; do
        kill -0 "$database_gate_pid" >/dev/null 2>&1 || break
        sleep 0.2
      done
      if kill -0 "$database_gate_pid" >/dev/null 2>&1; then
        database_gate_terminal=0
        kill -TERM "$database_gate_pid" >/dev/null 2>&1 || true
      fi
    fi
    wait "$database_gate_pid" >/dev/null 2>&1 || true
    database_gate_pid=''
  fi
  [[ "$database_gate_terminal" == 1 ]]
}

release_database_gate(){
  local rc marker
  [[ "$database_gate_pid" =~ ^[1-9][0-9]*$ ]] \
    || { echo 'Production enqueue fence process is unavailable' >&2; return 1; }
  kill -0 "$database_gate_pid" >/dev/null 2>&1 \
    || { echo 'Production enqueue fence exited before release' >&2; return 1; }
  write_database_gate_signal RELEASE || return 1
  set +e
  wait "$database_gate_pid"
  rc=$?
  set -e
  database_gate_pid=''
  (( rc == 0 )) || return "$rc"
  marker="$(cat "$database_gate_output")" || return 1
  [[ "$marker" == \
    'UNIVERSAL_VIDEO_PRECANARY_DB_ENQUEUE_FENCE tables=batch,job,job_event lock=SHARE owner_release=observed final_snapshot=unchanged result=PASS' ]] \
    || { echo 'Production enqueue fence receipt is invalid' >&2; return 1; }
  (umask 077; printf '%s\n' "$marker" > "$database_gate_marker_file")
  printf '%s\n' "$marker"
}

cleanup_remote(){
  trap - EXIT
  if abort_remote_attester; then
    "${s[@]}" "sudo -n rm -rf '$remote_root'; rm -rf '$remote_stage'" >/dev/null 2>&1 || true
  fi
  abort_database_gate >/dev/null 2>&1 || true
}
trap cleanup_remote EXIT

bounded_failure(){
  rc="${1:-$?}"
  trap - ERR
  abort_remote_attester || rc=1
  abort_database_gate || rc=1
  {
    echo "runtime_sha=$EXACT_SHA"
    echo "step_exit=$rc"
    cat "$RUNNER_TEMP/precanary-one-shot-marker.txt" 2>/dev/null || true
    cat "$RUNNER_TEMP/issue-881-owner-before-marker.txt" 2>/dev/null || true
    cat "$RUNNER_TEMP/precanary-infrastructure-marker.txt" 2>/dev/null || true
    cat "$RUNNER_TEMP/precanary-oci-instance-command-marker.txt" 2>/dev/null || true
    grep -E '^UNIVERSAL_VIDEO_PRECANARY_DB_ENQUEUE_FENCE ' \
      "$database_gate_output" 2>/dev/null || true
    grep -E '^UNIVERSAL_VIDEO_(PRECANARY_(WINDOW|RECOVERY|RUNTIME|STATE|ATTEST_PASS|FENCED_START|POSTRESTORE_(RUNTIME|OWNER)|OWNER_RELEASE|RESTORE(_PASS|_FAILED)?)|SOURCE_CHECKOUT|CONTAINER_(RESOURCE|STORAGE|CLEANUP|INSTALL_PASS)) |^\{"error_code":"UV_[A-Z0-9_]+","status":"FAILED"\}$|^ERROR:' \
      "$session_log" 2>/dev/null || true
    echo 'real_media_canary_run=false'
    echo 'source_media_downloaded=false'
    echo 'drive_write_performed=false'
    echo 'automatic_batch_release=false'
    echo 'canonical_promotion_allowed=false'
    echo 'publication_state=NOT_PUBLISHED'
    echo "remote_attester_terminal=$remote_attester_terminal"
    echo "database_gate_terminal=$database_gate_terminal"
  } > "$evidence"
  cat "$evidence" >> "$GITHUB_STEP_SUMMARY"
  exit "$rc"
}
trap bounded_failure ERR

"${s[@]}" "umask 077; rm -rf '$remote_stage'; mkdir -m 0700 '$remote_stage'"
"${c[@]}" "$RUNNER_TEMP/prepare.sh" "$ORACLE_USER@$ORACLE_HOST:$remote_stage/prepare.sh"
"${c[@]}" "$RUNNER_TEMP/attest.sh" "$ORACLE_USER@$ORACLE_HOST:$remote_stage/attest.sh"
"${c[@]}" ops/issue_881_precanary_queue_proof.py \
  "$ORACLE_USER@$ORACLE_HOST:$remote_stage/queue-proof.py"
if [[ -n "$recovery_evidence" ]]; then
  recovery_remote_file="$remote_root/recovery-evidence.txt"
  "${c[@]}" "$recovery_evidence" "$ORACLE_USER@$ORACLE_HOST:$remote_stage/recovery-evidence.txt"
fi
prepare_sha="$(sha256sum "$RUNNER_TEMP/prepare.sh" | awk '{print $1}')"
attest_sha="$(sha256sum "$RUNNER_TEMP/attest.sh" | awk '{print $1}')"
queue_proof_sha="$(sha256sum ops/issue_881_precanary_queue_proof.py | awk '{print $1}')"
recovery_install=''
if [[ -n "$recovery_evidence" ]]; then
  recovery_install="test \"\$(sha256sum '$remote_stage/recovery-evidence.txt' | awk '{print \$1}')\" = '$recovery_sha'; sudo -n install -o root -g root -m 0400 '$remote_stage/recovery-evidence.txt' '$remote_root/recovery-evidence.txt';"
fi
"${s[@]}" "set -e; \
  test \"\$(sha256sum '$remote_stage/prepare.sh' | awk '{print \$1}')\" = '$prepare_sha'; \
  test \"\$(sha256sum '$remote_stage/attest.sh' | awk '{print \$1}')\" = '$attest_sha'; \
  test \"\$(sha256sum '$remote_stage/queue-proof.py' | awk '{print \$1}')\" = '$queue_proof_sha'; \
  sudo -n install -d -o root -g root -m 0700 '$remote_root'; \
  sudo -n install -o root -g root -m 0700 '$remote_stage/prepare.sh' '$remote_root/prepare.sh'; \
  sudo -n install -o root -g root -m 0700 '$remote_stage/attest.sh' '$remote_root/attest.sh'; \
  sudo -n install -o root -g root -m 0600 '$remote_stage/queue-proof.py' '$remote_root/queue-proof.py'; \
  $recovery_install"

# Staging can outlive the evidence used by the earlier gate. Check
# the live head, review threads, and CI again as the final action
# before invoking the attestation that may quiesce resident services
# or replace protected runtime files.
verify_live_gate
verify_one_shot_gate
verify_final_mutation_boundary

owner_release_file="$remote_root/owner-release"
owner_abort_file="$remote_root/owner-abort"
owner_release_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
[[ "$owner_release_token" =~ ^[0-9a-f]{64}$ ]]
owner_release_token_sha="$(printf '%s' "$owner_release_token" | sha256sum | awk '{print $1}')"
[[ "$owner_release_token_sha" =~ ^[0-9a-f]{64}$ ]]

"${s[@]}" "sudo -n env \
  UNIVERSAL_VIDEO_EXPECTED_SHA='$EXACT_SHA' \
  UNIVERSAL_VIDEO_PREPARE_SCRIPT='$remote_root/prepare.sh' \
  UNIVERSAL_VIDEO_PRECANARY_BUILD_IMAGE=1 \
  UNIVERSAL_VIDEO_CONTAINER_MIN_FREE_KB=5242880 \
  UNIVERSAL_VIDEO_QUEUE_PROOF_SCRIPT='$remote_root/queue-proof.py' \
  UNIVERSAL_VIDEO_QUEUE_PROOF_SHA256='$queue_proof_sha' \
  UNIVERSAL_VIDEO_OWNER_RELEASE_FILE='$owner_release_file' \
  UNIVERSAL_VIDEO_OWNER_ABORT_FILE='$owner_abort_file' \
  UNIVERSAL_VIDEO_OWNER_RELEASE_TOKEN_SHA256='$owner_release_token_sha' \
  UNIVERSAL_VIDEO_OWNER_RELEASE_TIMEOUT_SECONDS=600 \
  UNIVERSAL_VIDEO_RECOVER_CONTAINER_ACTIVE_FROM_RUN='$RECOVER_CONTAINER_FROM_RUN' \
  UNIVERSAL_VIDEO_RECOVERY_EVIDENCE_FILE='$recovery_remote_file' \
  UNIVERSAL_VIDEO_RECOVERY_EVIDENCE_SHA256='$recovery_sha' \
  UNIVERSAL_VIDEO_CANARY_FILE_ID='198-2v3JBlNQobdsPYQQWzrrCqQ1zBZOI' \
  UNIVERSAL_VIDEO_CANARY_NAME='Диана 13.mp4' \
  UNIVERSAL_VIDEO_CANARY_MIME='video/mp4' \
  UNIVERSAL_VIDEO_CANARY_SIZE='696237577' \
  UNIVERSAL_VIDEO_CANARY_PARENT='1Fr-H2NgBKEpp3q_H4FzNmQwCV6bj2x6b' \
  bash '$remote_root/attest.sh'" > "$session_log" 2>&1 &
attester_pid=$!

# The recreated worker remains blocked on the host workload fence while this
# runner performs the independent owner read. Only the exact successful owner
# marker and an unlogged one-use token can authorize the remote unlock.
runtime_proof_ready=0
runtime_wait_deadline=$((SECONDS + 6300))
while (( SECONDS < runtime_wait_deadline )); do
  runtime_marker_count="$(grep -Ec '^UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_RUNTIME container_id=[0-9a-f]{64} previous_container_id=([0-9a-f]{64}|absent) recreated=true worker_fenced=true project=misty-poetry-18012774 branch=br-wispy-lab-b1rq54of database=neondb principal=bridge_school_worker_principal schema=true function=true claimable=0 leased=0 result=PASS$' "$session_log" 2>/dev/null || true)"
  [[ "$runtime_marker_count" =~ ^[0-9]+$ ]] || bounded_failure 1
  if [[ "$runtime_marker_count" == 1 ]]; then
    runtime_proof_ready=1
    break
  fi
  (( runtime_marker_count == 0 )) || bounded_failure 1
  if ! kill -0 "$attester_pid" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if [[ "$runtime_proof_ready" != 1 ]]; then
  session_rc=1
  if ! kill -0 "$attester_pid" >/dev/null 2>&1; then
    set +e
    wait "$attester_pid"
    session_rc=$?
    set -e
    attester_pid=''
    (( session_rc != 0 )) || session_rc=1
  fi
  bounded_failure "$session_rc"
fi

verify_final_mutation_boundary
python3 ops/issue_881_precanary_queue_proof.py owner-release-gate \
  --baseline "$RUNNER_TEMP/issue-881-owner-baseline.json" \
  --ready-file "$database_gate_ready_file" \
  --control-file "$database_gate_control_file" \
  --timeout-seconds 600 > "$database_gate_output" 2>&1 &
database_gate_pid=$!
database_gate_ready=0
database_gate_deadline=$((SECONDS + 30))
while (( SECONDS < database_gate_deadline )); do
  if [[ -e "$database_gate_ready_file" || -L "$database_gate_ready_file" ]]; then
    database_gate_ready=1
    break
  fi
  if ! kill -0 "$database_gate_pid" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done
[[ "$database_gate_ready" == 1 ]] || bounded_failure 1
[[ -f "$database_gate_ready_file" && ! -L "$database_gate_ready_file" \
  && "$(stat -c '%u:%a:%h' "$database_gate_ready_file")" == "$(id -u):600:1" ]] \
  || bounded_failure 1
kill -0 "$database_gate_pid" >/dev/null 2>&1 || bounded_failure 1
postrestore_owner_marker="$(cat "$database_gate_ready_file")"
[[ "$postrestore_owner_marker" == \
  'UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_OWNER project=misty-poetry-18012774 branch=br-wispy-lab-b1rq54of database=neondb principal=neondb_owner schema=true function=true batches=0 jobs=0 events=0 max_event_id=NULL sequence_last_value=1 sequence_is_called=false claimable=0 leased=0 unchanged=true result=PASS' ]]
(umask 077; printf '%s\n' "$postrestore_owner_marker" \
  > "$RUNNER_TEMP/issue-881-owner-after-marker.txt")
verify_exact_current_main

owner_release_local="$RUNNER_TEMP/precanary-owner-release.txt"
(umask 077; printf '%s\n%s\n' "$owner_release_token" "$postrestore_owner_marker" \
  > "$owner_release_local")
owner_release_payload_sha="$(sha256sum "$owner_release_local" | awk '{print $1}')"
[[ "$owner_release_payload_sha" =~ ^[0-9a-f]{64}$ ]]
"${c[@]}" "$owner_release_local" \
  "$ORACLE_USER@$ORACLE_HOST:$remote_stage/owner-release"
"${s[@]}" "set -e; \
  test \"\$(sha256sum '$remote_stage/owner-release' | awk '{print \$1}')\" = '$owner_release_payload_sha'; \
  sudo -n test ! -e '$owner_release_file'; \
  sudo -n install -o root -g root -m 0600 '$remote_stage/owner-release' '$owner_release_file'; \
  rm -f '$remote_stage/owner-release'"

set +e
wait "$attester_pid"
session_rc=$?
set -e
attester_pid=''
if (( session_rc != 0 )); then
  bounded_failure "$session_rc"
fi

grep -E "^UNIVERSAL_VIDEO_CONTAINER_INSTALL_PASS commit=$EXACT_SHA image_digest=sha256:[0-9a-f]{64} activated=0$" "$session_log"
grep -E "^UNIVERSAL_VIDEO_PRECANARY_ATTEST_PASS commit=$EXACT_SHA image_digest=sha256:[0-9a-f]{64} video_job_submitted=false drive_write_performed=false canonical_promotion_allowed=false publication_state=NOT_PUBLISHED$" "$session_log"
grep -E '^UNIVERSAL_VIDEO_PRECANARY_FENCED_START service=universal-video-container\.service worker_pid=[1-9][0-9]* stable_seconds=([3-9]|[12][0-9]|30) workload_fence=exclusive result=PASS$' "$session_log"
grep -E '^UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_RUNTIME container_id=[0-9a-f]{64} previous_container_id=([0-9a-f]{64}|absent) recreated=true worker_fenced=true project=misty-poetry-18012774 branch=br-wispy-lab-b1rq54of database=neondb principal=bridge_school_worker_principal schema=true function=true claimable=0 leased=0 result=PASS$' "$session_log"
grep -Fx "$postrestore_owner_marker" "$session_log"
grep -Fx 'UNIVERSAL_VIDEO_PRECANARY_OWNER_RELEASE worker_fenced=true owner_snapshot=unchanged result=PASS' "$session_log"
grep -E '^UNIVERSAL_VIDEO_PRECANARY_RESTORE_PASS source_service_before=(active|inactive) source_service_observed=(active|inactive) source_service=\1 container_service_before=(active|inactive) container_service_observed=(active|inactive) container_target=\3 container_service=\3 prior_container_recovery=[01]$' "$session_log"
installed_digest="$(sed -nE "s/^UNIVERSAL_VIDEO_CONTAINER_INSTALL_PASS commit=$EXACT_SHA image_digest=(sha256:[0-9a-f]{64}) activated=0$/\\1/p" "$session_log")"
attested_digest="$(sed -nE "s/^UNIVERSAL_VIDEO_PRECANARY_ATTEST_PASS commit=$EXACT_SHA image_digest=(sha256:[0-9a-f]{64}) video_job_submitted=false drive_write_performed=false canonical_promotion_allowed=false publication_state=NOT_PUBLISHED$/\\1/p" "$session_log")"
[[ "$installed_digest" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$attested_digest" == "$installed_digest" ]]
grep -F '"gate":"IMPORT_CLOSURE"' "$session_log"
grep -F '"gate":"SYNTHETIC_RESULT_CONTRACT"' "$session_log"
grep -F '"gate":"SOURCE_IDENTITY_METADATA_ONLY"' "$session_log"
grep -F '"file_id":"198-2v3JBlNQobdsPYQQWzrrCqQ1zBZOI"' "$session_log"
grep -F '"checksum_type":"md5"' "$session_log"
grep -F '"checksum_value":"b6ebeec5be5909d00d3902c92c380b07"' "$session_log"
grep -F '"source_media_downloaded":false' "$session_log"
grep -E '^UNIVERSAL_VIDEO_PRECANARY_WINDOW .*workload_fence=exclusive .*services_quiescent=true .*restore_on_exit=true$' "$session_log"
release_database_gate

trap - ERR
{
  echo "runtime_sha=$EXACT_SHA"
  cat "$RUNNER_TEMP/precanary-one-shot-marker.txt"
  cat "$RUNNER_TEMP/issue-881-owner-before-marker.txt"
  cat "$RUNNER_TEMP/precanary-infrastructure-marker.txt"
  cat "$RUNNER_TEMP/precanary-oci-instance-command-marker.txt"
  grep -E '^UNIVERSAL_VIDEO_(PRECANARY_(WINDOW|RECOVERY|RUNTIME|STATE|ATTEST_PASS|FENCED_START|POSTRESTORE_(RUNTIME|OWNER)|OWNER_RELEASE|RESTORE_PASS)|SOURCE_CHECKOUT|CONTAINER_(RESOURCE|CLEANUP|INSTALL_PASS)) ' "$session_log"
  cat "$database_gate_marker_file"
  grep -E '"gate":"(IMPORT_CLOSURE|SYNTHETIC_RESULT_CONTRACT|SOURCE_IDENTITY_METADATA_ONLY)"' "$session_log"
  echo "image_digest=$installed_digest"
  echo 'real_media_canary_run=false'
  echo 'source_media_downloaded=false'
  echo 'drive_write_performed=false'
  echo 'automatic_batch_release=false'
  echo 'canonical_promotion_allowed=false'
  echo 'publication_state=NOT_PUBLISHED'
} > "$evidence"
cat "$evidence" >> "$GITHUB_STEP_SUMMARY"
