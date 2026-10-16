#!/usr/bin/env bash
set -Eeuo pipefail

# Vercel Ignored Build Step: exit 0 skips the build; exit 1 runs it.
# .vercelignore excludes .git, so the gate uses only Vercel-provided metadata
# and separate exact, non-secret Preview and Production approvals.
ref="${VERCEL_GIT_COMMIT_REF:-}"
environment="${VERCEL_ENV:-}"
commit_sha="${VERCEL_GIT_COMMIT_SHA:-}"
approved_sha=''

if [[ "$environment" == "production" && "$ref" == "main" ]]; then
  approved_sha="${VERCEL_APPROVED_RUNTIME_SHA:-}"
elif [[ "$environment" == "preview" && -n "$ref" && "$ref" != "main" ]]; then
  approved_sha="${VERCEL_APPROVED_PREVIEW_SHA:-}"
else
  echo 'vercel-gate: no eligible release environment; skip'
  exit 0
fi

if [[ ! "$commit_sha" =~ ^[0-9a-f]{40}$ || ! "$approved_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'vercel-gate: missing or invalid exact approval; skip'
  exit 0
fi

if [[ "$commit_sha" != "$approved_sha" ]]; then
  echo 'vercel-gate: commit is not the approved release; skip'
  exit 0
fi

echo 'vercel-gate: exact release approved; build'
exit 1
