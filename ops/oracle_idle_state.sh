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
SYSTEM_PYTHON="${ORACLE_IDLE_SYSTEM_PYTHON:-/usr/bin/python3}"
QUEUE_DSN_FILE="${BRIDGE_VIDEO_QUEUE_DSN_FILE:-/opt/bridge-school/universal-video/secrets/video-queue-dsn}"
VIDEO_SPOOL_ROOT="${BRIDGE_VIDEO_SPOOL_ROOT:-/opt/bridge-school/universal-video/spool}"
HOST_LEASE_FILE="${ORACLE_HOST_LEASE_FILE:-/run/bridge-school/oracle-host-lease}"
MAX_OPERATOR_LEASE_SECONDS="${ORACLE_IDLE_MAX_OPERATOR_LEASE_SECONDS:-3600}"

state="UNKNOWN"
reason="unclassified"

finish() {
  printf 'ORACLE_IDLE_REASON=%s\n' "$reason"
  printf 'ORACLE_IDLE_STATE=%s\n' "$state"
}
trap finish EXIT

[[ "$MAX_OPERATOR_LEASE_SECONDS" =~ ^[0-9]+$ ]] && (( MAX_OPERATOR_LEASE_SECONDS >= 60 && MAX_OPERATOR_LEASE_SECONDS <= 86400 )) || {
  reason="operator_lease_policy_invalid"
  exit 0
}
[[ -r "$LAB_ENV" ]] || { reason="assistant_lab_env_unreadable"; exit 0; }
[[ -x "$PYTHON" ]] || { reason="assistant_lab_python_missing"; exit 0; }
[[ -x "$SYSTEM_PYTHON" ]] || { reason="system_python_missing"; exit 0; }

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
# line: expires_at_epoch=<unix-seconds>. A live lease inside the bounded policy
# horizon is BUSY. Expired, malformed, unreadable, or implausibly far-future
# lease telemetry is UNKNOWN, never IDLE.
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
  if (( lease_expires <= now_epoch )); then
    reason="host_lease_stale"
    exit 0
  fi
  if (( lease_expires - now_epoch > MAX_OPERATOR_LEASE_SECONDS )); then
    reason="host_lease_horizon_invalid"
    exit 0
  fi
  state="BUSY"
  reason="host_lease_active"
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
  if [[ -d "$spool" ]]; then
    set +e
    spool_probe="$(find "$spool" -type f -print -quit 2>/dev/null)"
    spool_rc=$?
    set -e
    if (( spool_rc != 0 )); then
      reason="spool_traversal_failed"
      exit 0
    fi
    if [[ -n "$spool_probe" ]]; then
      state="BUSY"
      reason="pending_spool:${spool}"
      exit 0
    fi
  fi
done

# Universal Video has a host-side spool independent of the Neon queue.  A
# bounded parser distinguishes active work from terminal progress receipts and
# treats malformed, symlinked or unreadable telemetry as UNKNOWN.
VIDEO_SPOOL_RESULT="$(BRIDGE_VIDEO_SPOOL_ROOT="$VIDEO_SPOOL_ROOT" "$SYSTEM_PYTHON" - <<'PY' 2>/dev/null || true
import json
import os
import stat
from pathlib import Path

root = Path(os.environ.get("BRIDGE_VIDEO_SPOOL_ROOT", ""))
active_progress = {"DOWNLOADING_FROM_DRIVE", "SOURCE_READY_ON_ORACLE", "PROCESSING"}
terminal_progress = {"RESULT_READY", "REVIEW", "FAILED"}

def fail(reason: str) -> None:
    print("UNKNOWN:" + reason)
    raise SystemExit

try:
    root_info = root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        fail("video_spool_unavailable")
    leaves = {}
    for name in ("inbox", "running", "progress"):
        path = root / name
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            fail("video_spool_" + name + "_unavailable")
        leaves[name] = path

    for name in ("inbox", "running"):
        try:
            entries = list(leaves[name].iterdir())
        except OSError:
            fail("video_spool_" + name + "_traversal_failed")
        for item in entries:
            if not item.name.endswith(".json"):
                continue
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                fail("video_spool_" + name + "_entry_invalid")
            print("BUSY:video_spool_" + name + "_pending")
            raise SystemExit

    try:
        progress_entries = list(leaves["progress"].iterdir())
    except OSError:
        fail("video_spool_progress_traversal_failed")
    for item in progress_entries:
        if not item.name.endswith(".json"):
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            fail("video_spool_progress_entry_invalid")
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            fail("video_spool_progress_invalid")
        if not isinstance(payload, dict) or payload.get("schema") != "universal-video-pipeline-progress-v1":
            fail("video_spool_progress_invalid")
        state = str(payload.get("state") or "")
        if state in active_progress:
            print("BUSY:video_spool_progress_active")
            raise SystemExit
        if state not in terminal_progress:
            fail("video_spool_progress_state_unknown")
    print("IDLE:video_spool_clear")
except FileNotFoundError:
    print("UNKNOWN:video_spool_unavailable")
except PermissionError:
    print("UNKNOWN:video_spool_permission_denied")
except OSError:
    print("UNKNOWN:video_spool_io_failed")
PY
)"
case "$VIDEO_SPOOL_RESULT" in
  IDLE:*) ;;
  BUSY:*) state="BUSY"; reason="${VIDEO_SPOOL_RESULT#BUSY:}"; exit 0 ;;
  UNKNOWN:*) reason="${VIDEO_SPOOL_RESULT#UNKNOWN:}"; exit 0 ;;
  *) reason="video_spool_classifier_invalid"; exit 0 ;;
esac

# Read only the exact DSN assignment; do not source the environment file as shell code.
dsn_line="$(grep -m1 '^ASSISTANT_LAB_DATABASE_URL=' "$LAB_ENV" 2>/dev/null || true)"
[[ -n "$dsn_line" ]] || { reason="assistant_lab_dsn_missing"; exit 0; }
export ASSISTANT_LAB_DATABASE_URL="${dsn_line#ASSISTANT_LAB_DATABASE_URL=}"
[[ -n "$ASSISTANT_LAB_DATABASE_URL" ]] || { reason="assistant_lab_dsn_empty"; exit 0; }

# Universal Video queue is a required live telemetry family for the stop proof.
# Missing/unreadable credentials or a failed live query are UNKNOWN. The live
# query itself avoids accepting a stale cached queue snapshot.
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
