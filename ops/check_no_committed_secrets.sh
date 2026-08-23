#!/usr/bin/env bash
set -Eeuo pipefail

fail=0

printf 'Checking tracked filenames for secret material...\n'
while IFS= read -r path; do
  base="${path##*/}"
  case "$base" in
    .env|.env.*|*.env|*.pem|*.key|*.p12|*.pfx|id_rsa*|id_ed25519*|oci_api_key*|credentials*|secrets*)
      case "$base" in
        *.example|.env.example|.env.*.example) ;;
        *) printf 'FORBIDDEN_TRACKED_SECRET_FILENAME %s\n' "$path" >&2; fail=1 ;;
      esac
      ;;
  esac
done < <(git ls-files)

printf 'Checking tracked text for high-confidence secret signatures...\n'
# Only high-confidence patterns are used here to keep the gate deterministic.
patterns=(
  '-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----'
  'postgres(ql)?://[^[:space:]/:@]+:[^[:space:]@]+@'
  'ASSISTANT_LAB_DATABASE_URL=postgres(ql)?://[^[:space:]/:@]+:[^[:space:]@]+@'
  'BEGIN PRIVATE KEY'
)

for pattern in "${patterns[@]}"; do
  if git grep -nEI "$pattern" -- . \
      ':(exclude)ops/check_no_committed_secrets.sh' \
      ':(exclude)**/*.md' \
      ':(exclude)**/*.example' >/tmp/bridge-secret-scan.out 2>/dev/null; then
    printf 'HIGH_CONFIDENCE_SECRET_SIGNATURE pattern=%q\n' "$pattern" >&2
    cat /tmp/bridge-secret-scan.out >&2
    fail=1
  fi
done
rm -f /tmp/bridge-secret-scan.out

if [[ "$fail" -ne 0 ]]; then
  printf 'SECRET_GATE_FAIL\n' >&2
  exit 1
fi

printf 'SECRET_GATE_PASS\n'
