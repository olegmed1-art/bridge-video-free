#!/usr/bin/env bash
set -Eeuo pipefail

# Pure authorization boundary for a future STOP consumer. This script contains
# no stop/reboot/cloud mutation. Only one well-formed IDLE result is admissible.
PROBE="${ORACLE_IDLE_PROBE:-/usr/local/sbin/oracle-idle-state}"

set +e
probe_output="$("$PROBE" 2>/dev/null)"
probe_rc=$?
set -e
state_count="$(printf '%s\n' "$probe_output" | grep -Ec '^ORACLE_IDLE_STATE=(IDLE|BUSY|UNKNOWN)$' || true)"
state="$(printf '%s\n' "$probe_output" | sed -n 's/^ORACLE_IDLE_STATE=//p')"
reason_count="$(printf '%s\n' "$probe_output" | grep -Ec '^ORACLE_IDLE_REASON=' || true)"
line_count="$(printf '%s\n' "$probe_output" | grep -Ec '.*' || true)"

if ((probe_rc == 0)) && [[ "$state_count" == "1" && "$reason_count" == "1" && "$line_count" == "2" && "$state" == "IDLE" ]]; then
  printf 'ORACLE_STOP_REASON=all_required_sources_proved_idle\n'
  printf 'ORACLE_STOP_ALLOWED=YES\n'
  exit 0
fi

case "$state" in
  BUSY) reason="oracle_busy" ;;
  UNKNOWN) reason="oracle_state_unknown" ;;
  *) reason="invalid_or_failed_idle_probe" ;;
esac
printf 'ORACLE_STOP_REASON=%s\n' "$reason"
printf 'ORACLE_STOP_ALLOWED=NO\n'
exit 1
