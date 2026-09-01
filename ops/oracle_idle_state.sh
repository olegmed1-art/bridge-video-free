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
QUEUE_DSN_FILE="${BRIDGE_VIDEO_QUEUE_DSN_FILE:-/opt/bridge-school/universal-video/secrets/video-queue-dsn}"
HOST_LEASE_FILE="${ORACLE_HOST_LEASE_FILE:-/run/bridge-school/oracle-host-lease}"

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
# An unhealthy/transitioning worker makes the state unknowable, so STOP stays blocked.
if command -v systemctl >/dev/null 2>&1; then
  worker_state="$(systemctl is-active assistant-lab.service 2>/dev/null || true)"
  case "$worker_state" in
    active) ;;
    *) reason="assistant_lab_service_${worker_state:-unknown}"; exit 0 ;;
  esac
else
  reason="systemctl_missing"
  exit 0
fi

# A bounded operator may own the host with a local lease. The lease file has one
# line: expires_at_epoch=<unix-seconds>. A live lease is BUSY. A malformed or
# expired-but-not-cleared lease is UNKNOWN, never IDLE, so stale lease telemetry
# cannot accidentally authorize STOP.
if [[ -e "$HOST_LEASE_FILE" || -L "$HOST_LEASE_FILE" ]]; then
  if [[ ! -f "$HOST_LEASE_FILE" || -L "$HOST_LEASE_FILE" || ! -r "$HOST_LEASE_FILE" ]]; then
    reason="host_lease_unreadable"
    exit 0
  fi
  lease_line="$(cat "$HOST_LEASE_FILE" 2>/dev/null || true)"
  if [[ ! "$lease_line" =~ ^expires_at_epoch=([0-9]{10,})$ ]]; then
    reason="host_lease_invalid"
    exit 0
  fi
  lease_expires="${BASH_REMATCH[1]}"
  now_epoch="$(date +%s 2>/dev/null || true)"
  [[ "$now_epoch" =~ ^[0-9]+$ ]] || { reason="clock_unavailable"; exit 0; }
  if (( lease_expires > now_epoch )); then
    state="BUSY"
    reason="host_lease_active"
    exit 0
  fi
  reason="host_lease_stale"
  exit 0
fi

# Observer experiments and durable-delivery work are independent keep-alive reasons.
if pgrep -f '[a]ssistant_lab.*observer.*experiment|[o]racle_assistant_lab_observer.*run' >/dev/null 2>&1; then
  state="BUSY"
  reason="observer_experiment_process"
  exit 0
fi

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

# Read only the exact DSN assignment; do not source the environment file as shell code.
dsn_line="$(grep -m1 '^ASSISTANT_LAB_DATABASE_URL=' "$LAB_ENV" 2>/dev/null || true)"
[[ -n "$dsn_line" ]] || { reason="assistant_lab_dsn_missing"; exit 0; }
export ASSISTANT_LAB_DATABASE_URL="${dsn_line#ASSISTANT_LAB_DATABASE_URL=}"
[[ -n "$ASSISTANT_LAB_DATABASE_URL" ]] || { reason="assistant_lab_dsn_empty"; exit 0; }

# Universal Video is a required workload family for the stop proof. Missing,
# unreadable, symlinked, or empty telemetry is UNKNOWN rather than an implicit
# zero-job result.
if [[ ! -f "$QUEUE_DSN_FILE" || -L "$QUEUE_DSN_FILE" || ! -r "$QUEUE_DSN_FILE" ]]; then
  unset ASSISTANT_LAB_DATABASE_URL
  reason="video_queue_dsn_unavailable"
  exit 0
fi
export BRIDGE_VIDEO_QUEUE_DATABASE_URL="$(tr -d '\n\r' < "$QUEUE_DSN_FILE")"
if [[ -z "$BRIDGE_VIDEO_QUEUE_DATABASE_URL" ]]; then
  unset ASSISTANT_LAB_DATABASE_URL
  unset BRIDGE_VIDEO_QUEUE_DATABASE_URL
  reason="video_queue_dsn_empty"
  exit 0
fi

DB_RESULT="$("$PYTHON" - <<'PY' 2>/dev/null || true
import os
try:
    import psycopg
except Exception:
    print("UNKNOWN:psycopg_unavailable")
    raise SystemExit

dsn = os.environ.get("ASSISTANT_LAB_DATABASE_URL", "")
queue_dsn = os.environ.get("BRIDGE_VIDEO_QUEUE_DATABASE_URL", "")
if not dsn:
    print("UNKNOWN:dsn_missing")
    raise SystemExit
if not queue_dsn:
    print("UNKNOWN:video_queue_dsn_missing")
    raise SystemExit

try:
    with psycopg.connect(dsn, connect_timeout=8, application_name="oracle-idle-state") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT active_jobs, active_research_jobs, active_control_commands FROM assistant_lab.oracle_idle_snapshot()")
            row = cur.fetchone()
            if row is None or len(row) != 3:
                print("UNKNOWN:idle_snapshot_missing")
                raise SystemExit
            active_jobs, active_research, active_control = map(int, row)
    with psycopg.connect(queue_dsn, connect_timeout=8, application_name="oracle-idle-video-queue") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM video_queue.job_status WHERE status IN ('PENDING_CANARY','QUEUED','LEASED')")
            video_jobs = int(cur.fetchone()[0])
    if min(active_jobs, active_research, active_control, video_jobs) < 0:
        print("UNKNOWN:invalid_idle_snapshot")
    elif active_jobs or active_research or active_control or video_jobs:
        print(f"BUSY:jobs={active_jobs},research={active_research},control={active_control},video={video_jobs}")
    else:
        print("IDLE:jobs=0,research=0,control=0,video=0")
except Exception:
    # Do not expose driver errors because they may contain connection details.
    print("UNKNOWN:database_check_failed")
PY
)"
unset ASSISTANT_LAB_DATABASE_URL
unset BRIDGE_VIDEO_QUEUE_DATABASE_URL

case "$DB_RESULT" in
  IDLE:*) state="IDLE"; reason="${DB_RESULT#IDLE:}" ;;
  BUSY:*) state="BUSY"; reason="${DB_RESULT#BUSY:}" ;;
  UNKNOWN:*) state="UNKNOWN"; reason="${DB_RESULT#UNKNOWN:}" ;;
  *) state="UNKNOWN"; reason="invalid_database_classifier_output" ;;
esac

exit 0
