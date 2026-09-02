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
  if [[ "$output" =~ ^ORACLE_IDLE_REASON=[A-Za-z0-9_.,:;/=-]+$'\n'ORACLE_IDLE_STATE=BUSY$ ]]; then
    reason='classifier_busy'
  elif [[ "$output" =~ ^ORACLE_IDLE_REASON=[A-Za-z0-9_.,:;/=-]+$'\n'ORACLE_IDLE_STATE=UNKNOWN$ ]]; then
    reason='classifier_unknown'
  elif [[ "$output" =~ ^ORACLE_IDLE_REASON=[A-Za-z0-9_.,:;/=-]+$'\n'ORACLE_IDLE_STATE=IDLE$ ]]; then
    reason='idle_reason_not_exact'
  fi
else
  reason='classifier_execution_failed'
fi

printf 'ORACLE_STOP_REASON=%s\n' "$reason"
printf 'ORACLE_STOP_ALLOWED=NO\n'
exit 1
