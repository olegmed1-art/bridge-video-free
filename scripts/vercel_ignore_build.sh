#!/usr/bin/env bash
set -Eeuo pipefail

# Vercel Ignored Build Step contract:
#   exit 0 => skip deployment
#   exit 1 => build/deploy
# Fail open (build) if the comparison base cannot be resolved.
base="${VERCEL_GIT_PREVIOUS_SHA:-}"
if [[ -z "$base" ]]; then
  if git rev-parse --verify HEAD^ >/dev/null 2>&1; then
    base="HEAD^"
  else
    echo "vercel-gate: no comparison base; build"
    exit 1
  fi
fi

if ! git rev-parse --verify "$base^{commit}" >/dev/null 2>&1; then
  echo "vercel-gate: comparison base unavailable; build"
  exit 1
fi

changed="$(git diff --name-only "$base" HEAD --)"
printf '%s\n' "$changed"

# Only the thin web/API runtime is deployable on Vercel. Compute, Research Lab,
# DDS3, BEN, video workers, database migrations, docs, tests, and operational
# workflows are intentionally not deployment triggers. The only Assistant Lab
# files allowed to trigger Vercel are the three thin web contract wrappers.
if printf '%s\n' "$changed" | grep -Eq '^(app\.py$|bridge_school_api/|bridge_contracts/|assistant_lab/(__init__|contract|dispatch)\.py$|pyproject\.toml$|\.python-version$|\.vercelignore$|vercel\.json$|scripts/vercel_ignore_build\.sh$)'; then
  echo "vercel-gate: web/API runtime changed; build"
  exit 1
fi

echo "vercel-gate: no web/API runtime change; skip"
exit 0
