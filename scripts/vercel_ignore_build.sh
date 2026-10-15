#!/usr/bin/env bash
set -Eeuo pipefail

# Vercel Ignored Build Step: exit 0 skips the build; exit 1 runs it.
# .vercelignore excludes .git, so the gate uses only Vercel-provided metadata
# and an exact, non-secret production release approval.
ref="${VERCEL_GIT_COMMIT_REF:-}"
environment="${VERCEL_ENV:-}"
commit_sha="${VERCEL_GIT_COMMIT_SHA:-}"
approved_sha="${VERCEL_APPROVED_RUNTIME_SHA:-}"

if [[ "$ref" != "main" || "$environment" != "production" ]]; then
  echo 'vercel-gate: not main production; skip'
  exit 0
fi

if [[ ! "$commit_sha" =~ ^[0-9a-f]{40}$ || ! "$approved_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'vercel-gate: missing or invalid exact runtime approval; skip'
  exit 0
fi

if [[ "$commit_sha" != "$approved_sha" ]]; then
  echo 'vercel-gate: commit is not the approved runtime release; skip'
  exit 0
fi

echo 'vercel-gate: exact runtime release approved; build'
exit 1
