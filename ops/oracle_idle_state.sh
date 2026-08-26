#!/usr/bin/env bash
set -Eeuo pipefail

# Classify whether the existing Frankfurt Oracle VM is safe to stop.
# Output contract (exactly one terminal marker):
#   ORACLE_IDLE_STATE=IDLE
#   ORACLE_IDLE_STATE=BUSY
#   ORACLE_IDLE_STATE=UNKNOWN
# Any inability to prove the required inputs is UNKNOWN (fail closed).

LAB_DIR="${ASSISTANT_LAB_DIR:-/opt/bridge-school/assistant-lab}"
LAB_ENV="${ASSISTANT_LAB_ENV_FILE:-$LAB_DIR/assistant-lab.env}"
PYTHON="${ASSISTANT_LAB_PYTHON:-$LAB_DIR/.venv/bin/python}"

state="UNKNOWN"
reason="unclassified"

finish() {
  printf 'ORACLE_IDLE_REASON=%s\n' "$reason"
  printf 'ORACLE_IDLE_STATE=%s\n' "$state"
}
trap finish EXIT

[[ -r "$LAB_ENV" ]] || { reason="assistant_lab_env_unreadable"; exit 0; }
[[ -x "$PYTHON" ]] || { reason="assistant_lab_python_missing"; exit 0; }

# The resident daemon being active is expected and does not itself make the host busy.
# A failed/activating worker, however, makes the state unknown because queue ownership
# cannot be trusted while lifecycle is unstable.
if command -v systemctl >/dev/null 2>&1; then
  worker_state="$(systemctl is-active assistant-lab.service 2>/dev/null || true)"
  case "$worker_state" in
    active) ;;
    inactive|failed|activating|deactivating|reloading|"")
      reason="assistant_lab_service_${worker_state:-unknown}"
      exit 0
      ;;
    *) reason="assistant_lab_service_unknown_${worker_state}"; exit 0 ;;
  esac
else
  reason="systemctl_missing"
  exit 0
fi

# A running observer experiment must keep the machine alive. The observer service may
# be resident, so inspect child processes rather than treating the service itself as busy.
if pgrep -f '[a]ssistant_lab.*observer.*experiment|[o]racle_assistant_lab_observer.*run' >/dev/null 2>&1; then
  state="BUSY"
  reason="observer_experiment_process"
  exit 0
fi

# Known durable-delivery/spool roots. A non-empty root is conservatively BUSY.
for spool in \
  /opt/bridge-school/assistant-lab/spool \
  /opt/bridge-school/assistant-lab/feedback-spool \
  /var/lib/bridge-school/uv-spool \
  /var/lib/bridge-school/feedback-spool
do
  if [[ -d "$spool" ]] && find "$spool" -type f -print -quit 2>/dev/null | grep -q .; then
    state="BUSY"
    reason="pending_spool:${spool}"
    exit 0
  fi
done

# Read the root-owned DSN without echoing it. The DB query is deliberately conservative:
# any schema/privilege/connectivity problem returns UNKNOWN instead of permitting stop.
set -a
# shellcheck disable=SC1090
source "$LAB_ENV"
set +a
[[ -n "${ASSISTANT_LAB_DATABASE_URL:-}" ]] || { reason="assistant_lab_dsn_missing"; exit 0; }

DB_RESULT="$("$PYTHON" - <<'PY' 2>/dev/null || true
import os
try:
    import psycopg
except Exception:
    print("UNKNOWN:psycopg_unavailable")
    raise SystemExit

dsn = os.environ.get("ASSISTANT_LAB_DATABASE_URL", "")
if not dsn:
    print("UNKNOWN:dsn_missing")
    raise SystemExit

try:
    with psycopg.connect(dsn, connect_timeout=8, application_name="oracle-idle-state") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM assistant_lab.job WHERE status IN ('QUEUED','RUNNING')")
            active_jobs = int(cur.fetchone()[0])

            # ResearchJob is an independent orchestration envelope. It must also be
            # quiescent. Lack of SELECT privilege is intentionally fail-closed.
            cur.execute("SELECT count(*) FROM assistant_lab.research_job WHERE stage IN ('QUEUED','ACCEPTED','RUNNING','CHECKPOINTED','VALIDATING')")
            active_research = int(cur.fetchone()[0])

            # Control commands are Oracle work too. Different schema revisions used
            # different lifecycle spellings, so discover the status column contract
            # without assuming a particular case convention.
            cur.execute("SELECT to_regclass('assistant_lab.control_command')")
            control_table = cur.fetchone()[0]
            active_control = 0
            if control_table is not None:
                cur.execute("SELECT count(*) FROM assistant_lab.control_command WHERE upper(status::text) IN ('QUEUED','RUNNING','PENDING')")
                active_control = int(cur.fetchone()[0])

    if active_jobs or active_research or active_control:
        print(f"BUSY:jobs={active_jobs},research={active_research},control={active_control}")
    else:
        print("IDLE:jobs=0,research=0,control=0")
except Exception as exc:
    # Never print the exception because driver errors may contain connection details.
    print("UNKNOWN:database_check_failed")
PY
)"

case "$DB_RESULT" in
  IDLE:*) state="IDLE"; reason="${DB_RESULT#IDLE:}" ;;
  BUSY:*) state="BUSY"; reason="${DB_RESULT#BUSY:}" ;;
  UNKNOWN:*) state="UNKNOWN"; reason="${DB_RESULT#UNKNOWN:}" ;;
  *) state="UNKNOWN"; reason="invalid_database_classifier_output" ;;
esac

exit 0
