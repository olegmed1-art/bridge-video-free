#!/usr/bin/env bash
set -Eeuo pipefail

# This is the only authorization boundary for an Oracle STOP consumer.
# It has no lifecycle side effects. The classifier must emit the one exact,
# non-empty proof below; every other result is fail-closed.
probe="${ORACLE_IDLE_PROBE:-/usr/local/sbin/oracle-idle-state}"
expected_idle=$'ORACLE_IDLE_REASON=all_required_sources_proved_idle\nORACLE_IDLE_STATE=IDLE'

set +e
output="$("$probe" 2>/dev/null)"
probe_rc=$?
set -e

if ((probe_rc == 0)) && [[ "$output" == "$expected_idle" ]]; then
  printf 'ORACLE_STOP_REASON=all_required_sources_proved_idle\n'
  printf 'ORACLE_STOP_ALLOWED=YES\n'
  exit 0
fi

reason='classifier_failed_or_malformed'
if ((probe_rc == 0)); then
  case "$output" in
    $'ORACLE_IDLE_REASON='?*$'\nORACLE_IDLE_STATE=BUSY') reason='classifier_busy' ;;
    $'ORACLE_IDLE_REASON='?*$'\nORACLE_IDLE_STATE=UNKNOWN') reason='classifier_unknown' ;;
    $'ORACLE_IDLE_REASON='?*$'\nORACLE_IDLE_STATE=IDLE') reason='idle_reason_not_exact' ;;
  esac
else
  reason='classifier_execution_failed'
fi

printf 'ORACLE_STOP_REASON=%s\n' "$reason"
printf 'ORACLE_STOP_ALLOWED=NO\n'
exit 1
