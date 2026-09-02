#!/usr/bin/env bash
set -Eeuo pipefail

# Emit a complete, current fail-closed gate for one Universal Video PR.
# GitHub CLI handles pagination; callers may pin EXPECTED_HEAD_SHA when this is
# the last-second check immediately before an Oracle image build.

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPOSITORY:?REPOSITORY is required}"
PR_NUMBER="${UV_GATE_PR_NUMBER:-997}"
EXPECTED_HEAD_SHA="${EXPECTED_HEAD_SHA:-}"
[[ "$PR_NUMBER" =~ ^[0-9]+$ ]]
[[ -z "$EXPECTED_HEAD_SHA" || "$EXPECTED_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]

api="https://api.github.com/repos/$REPOSITORY"
auth=(-H "Authorization: Bearer $GH_TOKEN" -H 'Accept: application/vnd.github+json')
pr="$(curl -fsS --retry 2 --retry-all-errors --max-time 20 "${auth[@]}" "$api/pulls/$PR_NUMBER")"
head="$(jq -er '.head.sha' <<<"$pr")"
branch="$(jq -er '.head.ref' <<<"$pr")"
draft="$(jq -er '.draft' <<<"$pr")"
state="$(jq -er '.state' <<<"$pr")"
[[ "$head" =~ ^[0-9a-f]{40}$ && "$branch" =~ ^[A-Za-z0-9._/-]{1,240}$ ]]
[[ "$draft" == true && "$state" == open ]]

check_pages="$(gh api --paginate --slurp "repos/$REPOSITORY/commits/$head/check-runs?per_page=100")"
total="$(jq '[.[] | .check_runs[]] | length' <<<"$check_pages")"
reported_total="$(jq '[.[].total_count] | max // 0' <<<"$check_pages")"
pending="$(jq '[.[] | .check_runs[] | select(.status != "completed")] | length' <<<"$check_pages")"
bad="$(jq '[.[] | .check_runs[] | select(.status == "completed" and ((.conclusion // "") | IN("success","neutral","skipped") | not))] | length' <<<"$check_pages")"
ci=WAITING
(( total > 0 && total == reported_total && pending == 0 && bad == 0 )) && ci=PASS

thread_query=$(cat <<'GRAPHQL'
query($owner:String!,$repo:String!,$number:Int!,$cursor:String){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$number){
      reviewThreads(first:100,after:$cursor){
        nodes{isResolved}
        pageInfo{hasNextPage endCursor}
      }
    }
  }
}
GRAPHQL
)
owner="${REPOSITORY%%/*}"
repo="${REPOSITORY##*/}"
cursor=''
unresolved=0
while :; do
  args=(-f query="$thread_query" -F owner="$owner" -F repo="$repo" -F number="$PR_NUMBER")
  [[ -z "$cursor" ]] || args+=(-f cursor="$cursor")
  page="$(gh api graphql "${args[@]}")"
  count="$(jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)] | length' <<<"$page")"
  unresolved=$((unresolved + count))
  has_next="$(jq -er '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<<"$page")"
  [[ "$has_next" == true ]] || break
  cursor="$(jq -er '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<<"$page")"
  [[ -n "$cursor" && "$cursor" != null ]]
done

review_pages="$(gh api --paginate --slurp "repos/$REPOSITORY/pulls/$PR_NUMBER/reviews?per_page=100")"
trusted="$(jq --arg head "$head" '[.[][] | select(.commit_id == $head and (.user.login // "") == "chatgpt-codex-connector[bot]" and (.state == "COMMENTED" or .state == "APPROVED"))] | length' <<<"$review_pages")"
changes_requested="$(jq --arg head "$head" '[.[][] | select(.commit_id == $head and .state == "CHANGES_REQUESTED")] | length' <<<"$review_pages")"
review=WAITING
(( trusted > 0 && changes_requested == 0 && unresolved == 0 )) && review=PASS

if [[ -n "$EXPECTED_HEAD_SHA" && "$head" != "$EXPECTED_HEAD_SHA" ]]; then
  ci=STALE
  review=STALE
fi

printf 'head=%s\n' "$head"
printf 'branch=%s\n' "$branch"
printf 'ci=%s\n' "$ci"
printf 'check_runs=%s/%s\n' "$total" "$reported_total"
printf 'review=%s\n' "$review"
printf 'unresolved=%s\n' "$unresolved"
printf 'trusted_reviews=%s\n' "$trusted"
printf 'changes_requested=%s\n' "$changes_requested"
