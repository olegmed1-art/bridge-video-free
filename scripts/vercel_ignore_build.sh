#!/usr/bin/env bash
set -Eeuo pipefail

# Vercel Ignored Build Step contract:
#   exit 0 => skip deployment
#   exit 1 => build/deploy
#
# Cost/reliability boundary: Git-connected preview/feature branches must never
# consume a Vercel build. Vercel evaluates this script from the branch being
# deployed, so make the branch decision before touching Git history: .vercelignore
# may remove .git metadata and VERCEL_GIT_PREVIOUS_SHA can be unavailable.
ref="${VERCEL_GIT_COMMIT_REF:-}"
if [[ "$ref" != "main" ]]; then
  echo "vercel-gate: non-main ref '${ref:-unknown}'; skip"
  exit 0
fi

# On main, deploy only when the thin web/API runtime changed. Fail open for main
# if the comparison base cannot be resolved so a legitimate production change
# is not accidentally suppressed.
base="${VERCEL_GIT_PREVIOUS_SHA:-}"
if [[ -z "$base" ]]; then
  if git rev-parse --verify HEAD^ >/dev/null 2>&1; then
    base="HEAD^"
  else
    echo "vercel-gate: main has no comparison base; build"
    exit 1
  fi
fi

if ! git rev-parse --verify "$base^{commit}" >/dev/null 2>&1; then
  echo "vercel-gate: main comparison base unavailable; build"
  exit 1
fi

changed="$(git diff --name-only "$base" HEAD --)"
printf '%s\n' "$changed"

# Only the thin web/API runtime is deployable on Vercel. Compute, Research Lab,
# DDS3, BEN, video workers, database migrations, docs, tests, and operational
# workflows are intentionally not deployment triggers. The only Assistant Lab
# files allowed to trigger Vercel are the three thin web contract wrappers.
if printf '%s\n' "$changed" | grep -Eq '^(app\.py$|bridge_school_api/|bridge_contracts/|assistant_lab/(__init__|contract|dispatch)\.py$|pyproject\.toml$|\.python-version$|\.vercelignore$|vercel\.json$|scripts/vercel_ignore_build\.sh$)'; then
  echo "vercel-gate: main web/API runtime changed; build"
  exit 1
fi

echo "vercel-gate: main has no web/API runtime change; skip"
exit 0
