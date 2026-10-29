#!/usr/bin/env bash
set -Eeuo pipefail

# All key tools below are synthetic PATH shims. No SSH connection or real key.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scanner="${SCANNER_UNDER_TEST:-$repo_root/ops/oracle_known_hosts_from_scan.sh}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"
export REAL_AWK="$(command -v awk)"

cat > "$work/bin/ssh-keyscan" <<'SH'
#!/usr/bin/env bash
if [[ "$SCENARIO" == scan_no_output ]]; then
  printf '%s\n' SYNTHETIC_SECRET_NEVER_PRINT >&2
  exit 255
fi
printf '%s\n' 'example.invalid ssh-ed25519 AAAAsynthetic'
if [[ "$SCENARIO" == final_partial || "$SCENARIO" == candidate_late_fail ]]; then
  printf '%s\n' 'example.invalid ssh-rsa BBBBsynthetic'
fi
SH
cat > "$work/bin/ssh-keygen" <<'SH'
#!/usr/bin/env bash
if [[ "$2" == "$TEST_OUTPUT" ]]; then
  case "$SCENARIO" in
    final_fail)
      printf '%s\n' SYNTHETIC_SECRET_NEVER_PRINT >&2
      exit 255 ;;
    final_partial_fail)
      printf '%s\n' '256 SHA256:AAAA synthetic (ED25519)'
      printf '%s\n' SYNTHETIC_SECRET_NEVER_PRINT >&2
      exit 255 ;;
    final_empty) exit 0 ;;
    final_mismatch) printf '%s\n' '256 SHA256:BBBB synthetic (ED25519)'; exit 0 ;;
    final_partial) printf '%s\n' '256 SHA256:AAAA synthetic (ED25519)'; exit 0 ;;
    final_extra)
      printf '%s\n' '256 SHA256:AAAA synthetic (ED25519)' '256 SHA256:AAAA synthetic (RSA)'
      exit 0 ;;
  esac
elif [[ "$SCENARIO" == candidate_fail || "$SCENARIO" == candidate_fail_other || "$SCENARIO" == candidate_partial_fail ]]; then
  if [[ "$SCENARIO" == candidate_partial_fail ]]; then
    printf '%s\n' '256 SHA256:AAAA synthetic (ED25519)'
  fi
  printf '%s\n' SYNTHETIC_SECRET_NEVER_PRINT >&2
  if [[ "$SCENARIO" == candidate_fail_other ]]; then exit 7; else exit 255; fi
elif [[ "$SCENARIO" == candidate_late_fail ]] && grep -qF 'ssh-rsa' "$2"; then
  printf '%s\n' SYNTHETIC_SECRET_NEVER_PRINT >&2
  exit 255
elif [[ "$SCENARIO" == candidate_mismatch ]]; then
  printf '%s\n' '256 SHA256:BBBB synthetic (ED25519)'
  exit 0
fi
printf '%s\n' '256 SHA256:AAAA synthetic (ED25519)'
SH
cat > "$work/bin/awk" <<'SH'
#!/usr/bin/env bash
if [[ "$SCENARIO" == candidate_awk_fail ]]; then
  printf '%s\n' SYNTHETIC_SECRET_NEVER_PRINT >&2
  exit 7
fi
exec "$REAL_AWK" "$@"
SH
cat > "$work/bin/sleep" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$work/bin/ssh-keyscan" "$work/bin/ssh-keygen" "$work/bin/awk" "$work/bin/sleep"
export PATH="$work/bin:$PATH"
export TEST_OUTPUT="$work/known_hosts"

for scenario in normal candidate_fail candidate_fail_other candidate_partial_fail candidate_late_fail candidate_awk_fail candidate_mismatch final_fail final_partial_fail final_empty final_mismatch final_partial final_extra scan_no_output; do
  : > "$work/stdout"
  : > "$work/stderr"
  rm -f "$TEST_OUTPUT"
  if SCENARIO="$scenario" bash "$scanner" example.invalid SHA256:AAAA "$TEST_OUTPUT" > "$work/stdout" 2> "$work/stderr"; then
    status=0
  else
    status=$?
  fi
  case "$scenario" in
    normal)
      [[ "$status" == 0 && -s "$TEST_OUTPUT" ]]
      grep -qxF 'example.invalid ssh-ed25519 AAAAsynthetic' "$TEST_OUTPUT" ;;
    candidate_fail|candidate_fail_other|candidate_partial_fail|candidate_late_fail|candidate_awk_fail)
      [[ "$status" == 6 && ! -s "$TEST_OUTPUT" ]]
      grep -qxF 'Oracle SSH candidate fingerprint stage failed' "$work/stderr" ;;
    candidate_mismatch) [[ "$status" == 4 && ! -s "$TEST_OUTPUT" ]] ;;
    final_fail|final_partial_fail|final_empty|final_partial|final_extra)
      if [[ "$status" != 5 ]]; then
        printf 'EXPECTED_FAIL_CLOSED scenario=%s observed_status=%s\n' "$scenario" "$status" >&2
        exit 1
      fi ;;
    final_mismatch) [[ "$status" == 5 ]] ;;
    scan_no_output) [[ "$status" == 3 ]] ;;
  esac
  if grep -qF SYNTHETIC_SECRET_NEVER_PRINT "$work/stdout" "$work/stderr"; then
    echo "synthetic secret leaked in $scenario" >&2
    exit 1
  fi
  printf 'PASS %s status=%s\n' "$scenario" "$status"
done
