#!/usr/bin/env bash
set -Eeuo pipefail

# Classify whether the existing Frankfurt Oracle VM is safe to stop.
# Exact output contract (five lines, one terminal decision):
#   ORACLE_IDLE_CONTRACT_VERSION=2
#   ORACLE_IDLE_STARTED_AT_EPOCH=<unix-seconds>
#   ORACLE_IDLE_OBSERVED_AT_EPOCH=<unix-seconds>
#   ORACLE_IDLE_REASON=<machine-readable-reason>
#   ORACLE_IDLE_STATE=IDLE|BUSY|UNKNOWN
#
# BUSY and UNKNOWN both forbid STOP. IDLE is meaningful only when every
# authoritative workload source below proves absence of work.

LAB_DIR="${ASSISTANT_LAB_DIR:-/opt/bridge-school/assistant-lab}"
LAB_ENV="${ASSISTANT_LAB_ENV_FILE:-$LAB_DIR/assistant-lab.env}"
AUTOPILOT_ENV="${AUTOPILOT_ENV_FILE:-/opt/bridge-school/school-autopilot/autopilot-shadow.env}"
OBSERVER_DIR="${ASSISTANT_LAB_OBSERVER_DIR:-/opt/bridge-school/assistant-lab-observer}"
VIDEO_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
PYTHON="${ASSISTANT_LAB_PYTHON:-$LAB_DIR/.venv/bin/python}"
QUEUE_DSN_FILE="${BRIDGE_VIDEO_QUEUE_DSN_FILE:-/opt/bridge-school/universal-video/secrets/video-queue-dsn}"
HOST_LEASE_FILE="${ORACLE_HOST_LEASE_FILE:-/run/bridge-school/oracle-host-lease}"
MAX_SOURCE_AGE_SECONDS="${ORACLE_IDLE_MAX_SOURCE_AGE_SECONDS:-60}"
MAX_FUTURE_SKEW_SECONDS="${ORACLE_IDLE_MAX_FUTURE_SKEW_SECONDS:-5}"
MAX_HOST_LEASE_REMAINING_SECONDS="${ORACLE_IDLE_MAX_HOST_LEASE_REMAINING_SECONDS:-86400}"
# Only active leaves belong here. Universal Video terminal receipts under
# done/failed/results/progress and Observer terminal jobs under done/failed are
# durable evidence, not active work. The container mounts VIDEO_DIR/spool at
# /var/lib/universal-video/spool, so the host proof must inspect the host path.
REQUIRED_LOCAL_SPOOLS="${ORACLE_IDLE_REQUIRED_LOCAL_SPOOLS:-$LAB_DIR/spool:$LAB_DIR/feedback-spool:$VIDEO_DIR/spool/inbox:$VIDEO_DIR/spool/running:$OBSERVER_DIR/jobs/pending:$OBSERVER_DIR/jobs/running}"

state="UNKNOWN"
reason="unclassified"
started_at_epoch="0"
db_stderr=""

finish() {
  if [[ -n "$db_stderr" ]]; then
    rm -f "$db_stderr" 2>/dev/null || true
  fi
  local observed_at_epoch="0"
  if observed_at_epoch="$(date +%s 2>/dev/null)" && [[ "$observed_at_epoch" =~ ^[0-9]+$ ]]; then
    :
  else
    observed_at_epoch="0"
    state="UNKNOWN"
    reason="clock_unavailable"
  fi
  printf 'ORACLE_IDLE_CONTRACT_VERSION=2\n'
  printf 'ORACLE_IDLE_STARTED_AT_EPOCH=%s\n' "$started_at_epoch"
  printf 'ORACLE_IDLE_OBSERVED_AT_EPOCH=%s\n' "$observed_at_epoch"
  printf 'ORACLE_IDLE_REASON=%s\n' "$reason"
  printf 'ORACLE_IDLE_STATE=%s\n' "$state"
}
trap finish EXIT

started_at_epoch="$(date +%s 2>/dev/null || true)"
[[ "$started_at_epoch" =~ ^[0-9]+$ ]] || { started_at_epoch="0"; reason="clock_unavailable"; exit 0; }
[[ "$MAX_SOURCE_AGE_SECONDS" =~ ^[1-9][0-9]{0,5}$ ]] || { reason="invalid_max_source_age"; exit 0; }
[[ "$MAX_FUTURE_SKEW_SECONDS" =~ ^[0-9]{1,5}$ ]] || { reason="invalid_max_future_skew"; exit 0; }
[[ "$MAX_HOST_LEASE_REMAINING_SECONDS" =~ ^[1-9][0-9]{0,6}$ ]] || { reason="invalid_max_host_lease_bound"; exit 0; }

[[ -r "$LAB_ENV" ]] || { reason="assistant_lab_env_unreadable"; exit 0; }
[[ -x "$PYTHON" ]] || { reason="assistant_lab_python_missing"; exit 0; }

# The resident daemon being active is expected and does not itself make the host
# BUSY. An absent, failed, or transitioning worker makes telemetry UNKNOWN.
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

# A local operator/maintenance lease is a bounded keep-alive signal. A live,
# bounded lease is BUSY. Malformed, expired-but-not-cleared, or effectively
# unbounded lease telemetry is UNKNOWN.
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
  if (( lease_expires - now_epoch > MAX_HOST_LEASE_REMAINING_SECONDS )); then
    reason="host_lease_unbounded"
    exit 0
  fi
  state="BUSY"
  reason="host_lease_active"
  exit 0
fi

# Observer experiments are independent keep-alive work.
if pgrep -f '[a]ssistant_lab.*observer.*experiment|[o]racle_assistant_lab_observer.*run' >/dev/null 2>&1; then
  state="BUSY"
  reason="observer_experiment_process"
  exit 0
fi

# Every configured local spool is authoritative. A missing, symlinked,
# unreadable, or unsearchable spool is UNKNOWN; it is never an implicit empty
# queue. Any work file makes the host BUSY.
[[ -n "$REQUIRED_LOCAL_SPOOLS" ]] || { reason="local_spool_set_missing"; exit 0; }
IFS=':' read -r -a spool_paths <<< "$REQUIRED_LOCAL_SPOOLS"
(( ${#spool_paths[@]} > 0 )) || { reason="local_spool_set_missing"; exit 0; }
for spool in "${spool_paths[@]}"; do
  [[ -n "$spool" ]] || { reason="local_spool_path_empty"; exit 0; }
  if [[ ! -d "$spool" || -L "$spool" || ! -r "$spool" || ! -x "$spool" ]]; then
    reason="local_spool_unavailable"
    exit 0
  fi
  spool_work=""
  if ! spool_work="$(find "$spool" -mindepth 1 -print -quit 2>/dev/null)"; then
    reason="local_spool_scan_failed"
    exit 0
  fi
  if [[ -n "$spool_work" ]]; then
    state="BUSY"
    reason="local_spool_has_work"
    exit 0
  fi
done

# Read only the exact DSN assignment; do not source the environment as shell.
dsn_line="$(grep -m1 '^ASSISTANT_LAB_DATABASE_URL=' "$LAB_ENV" 2>/dev/null || true)"
[[ -n "$dsn_line" ]] || { reason="assistant_lab_dsn_missing"; exit 0; }
export ASSISTANT_LAB_DATABASE_URL="${dsn_line#ASSISTANT_LAB_DATABASE_URL=}"
[[ -n "$ASSISTANT_LAB_DATABASE_URL" ]] || { reason="assistant_lab_dsn_empty"; exit 0; }

# Autopilot task state is a distinct authoritative workload family. Read the
# dedicated runtime DSN without sourcing the root-owned environment file.
if [[ ! -f "$AUTOPILOT_ENV" || -L "$AUTOPILOT_ENV" || ! -r "$AUTOPILOT_ENV" ]]; then
  unset ASSISTANT_LAB_DATABASE_URL
  reason="autopilot_env_unavailable"
  exit 0
fi
autopilot_dsn_line="$(grep -m1 '^AUTOPILOT_DATABASE_URL=' "$AUTOPILOT_ENV" 2>/dev/null || true)"
if [[ -z "$autopilot_dsn_line" ]]; then
  unset ASSISTANT_LAB_DATABASE_URL
  reason="autopilot_dsn_missing"
  exit 0
fi
export AUTOPILOT_DATABASE_URL="${autopilot_dsn_line#AUTOPILOT_DATABASE_URL=}"
if [[ -z "$AUTOPILOT_DATABASE_URL" ]]; then
  unset ASSISTANT_LAB_DATABASE_URL
  unset AUTOPILOT_DATABASE_URL
  reason="autopilot_dsn_empty"
  exit 0
fi

# Universal Video is an authoritative workload family. Missing, unreadable,
# symlinked, or empty connection telemetry is UNKNOWN.
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
export ORACLE_IDLE_MAX_SOURCE_AGE_SECONDS="$MAX_SOURCE_AGE_SECONDS"
export ORACLE_IDLE_MAX_FUTURE_SKEW_SECONDS="$MAX_FUTURE_SKEW_SECONDS"

db_stderr="$(mktemp 2>/dev/null || true)"
[[ -n "$db_stderr" ]] || { reason="database_stderr_capture_failed"; exit 0; }
set +e
DB_RESULT="$("$PYTHON" - <<'PY' 2>"$db_stderr"
import os
import time

try:
    import psycopg
except Exception:
    print("UNKNOWN:psycopg_unavailable")
    raise SystemExit

assistant_dsn = os.environ.get("ASSISTANT_LAB_DATABASE_URL", "")
autopilot_dsn = os.environ.get("AUTOPILOT_DATABASE_URL", "")
video_dsn = os.environ.get("BRIDGE_VIDEO_QUEUE_DATABASE_URL", "")
try:
    max_age = int(os.environ["ORACLE_IDLE_MAX_SOURCE_AGE_SECONDS"])
    max_future = int(os.environ["ORACLE_IDLE_MAX_FUTURE_SKEW_SECONDS"])
except Exception:
    print("UNKNOWN:invalid_freshness_policy")
    raise SystemExit

if not assistant_dsn:
    print("UNKNOWN:assistant_lab_dsn_missing")
    raise SystemExit
if not autopilot_dsn:
    print("UNKNOWN:autopilot_dsn_missing")
    raise SystemExit
if not video_dsn:
    print("UNKNOWN:video_queue_dsn_missing")
    raise SystemExit


def freshness_problem(observed_at: int, source: str) -> str | None:
    now = int(time.time())
    if observed_at <= 0:
        return f"{source}_timestamp_invalid"
    if now - observed_at > max_age:
        return f"{source}_telemetry_stale"
    if observed_at - now > max_future:
        return f"{source}_clock_in_future"
    return None


try:
    with psycopg.connect(
        assistant_dsn,
        connect_timeout=8,
        application_name="oracle-idle-state",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    schema_version,
                    observed_at_epoch,
                    active_jobs,
                    active_research_jobs,
                    active_research_child_jobs,
                    active_control_commands,
                    active_operator_maintenance_leases,
                    stale_operator_maintenance_leases
                FROM assistant_lab.oracle_idle_snapshot()
                """
            )
            row = cur.fetchone()
            if row is None or len(row) != 8:
                print("UNKNOWN:assistant_lab_snapshot_missing")
                raise SystemExit
            (
                schema_version,
                assistant_observed,
                active_jobs,
                active_research,
                active_research_children,
                active_control,
                active_operator_lease,
                stale_operator_lease,
            ) = map(int, row)
            if schema_version != 2:
                print("UNKNOWN:assistant_lab_snapshot_version")
                raise SystemExit

    problem = freshness_problem(assistant_observed, "assistant_lab")
    if problem:
        print(f"UNKNOWN:{problem}")
        raise SystemExit

    with psycopg.connect(
        autopilot_dsn,
        connect_timeout=8,
        application_name="oracle-idle-autopilot",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    count(*)::bigint,
                    extract(epoch FROM current_timestamp)::bigint
                FROM autopilot.task_status
                WHERE status IN ('READY', 'RUNNING', 'WAITING_EXTERNAL')
                """
            )
            row = cur.fetchone()
            if row is None or len(row) != 2:
                print("UNKNOWN:autopilot_snapshot_missing")
                raise SystemExit
            active_autopilot, autopilot_observed = map(int, row)

    problem = freshness_problem(autopilot_observed, "autopilot")
    if problem:
        print(f"UNKNOWN:{problem}")
        raise SystemExit

    with psycopg.connect(
        video_dsn,
        connect_timeout=8,
        application_name="oracle-idle-video-queue",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    count(*)::bigint,
                    extract(epoch FROM current_timestamp)::bigint
                FROM video_queue.job_status
                WHERE status IN ('PENDING_CANARY', 'QUEUED', 'LEASED')
                """
            )
            row = cur.fetchone()
            if row is None or len(row) != 2:
                print("UNKNOWN:video_queue_snapshot_missing")
                raise SystemExit
            video_jobs, video_observed = map(int, row)

    problem = freshness_problem(video_observed, "video_queue")
    if problem:
        print(f"UNKNOWN:{problem}")
        raise SystemExit

    counts = {
        "jobs": active_jobs,
        "research": active_research,
        "research_children": active_research_children,
        "control": active_control,
        "operator_lease": active_operator_lease,
        "stale_operator_lease": stale_operator_lease,
        "autopilot": active_autopilot,
        "video": video_jobs,
    }
    if min(counts.values()) < 0:
        print("UNKNOWN:invalid_idle_snapshot")
    elif any(
        counts[name]
        for name in (
            "jobs",
            "research",
            "research_children",
            "control",
            "operator_lease",
            "autopilot",
            "video",
        )
    ):
        print(
            "BUSY:"
            + ",".join(
                f"{name}={counts[name]}"
                for name in (
                    "jobs",
                    "research",
                    "research_children",
                    "control",
                    "operator_lease",
                    "autopilot",
                    "video",
                )
            )
        )
    elif counts["stale_operator_lease"]:
        print("UNKNOWN:operator_maintenance_lease_stale")
    else:
        print(
            "IDLE:"
            + ",".join(
                f"{name}=0"
                for name in (
                    "jobs",
                    "research",
                    "research_children",
                    "control",
                    "operator_lease",
                    "autopilot",
                    "video",
                )
            )
        )
except Exception:
    # Driver errors may contain credentials, so expose only a stable reason.
    print("UNKNOWN:database_check_failed")
PY
)"
db_rc=$?
set -e
unset ASSISTANT_LAB_DATABASE_URL
unset AUTOPILOT_DATABASE_URL
unset BRIDGE_VIDEO_QUEUE_DATABASE_URL
unset ORACLE_IDLE_MAX_SOURCE_AGE_SECONDS
unset ORACLE_IDLE_MAX_FUTURE_SKEW_SECONDS

if (( db_rc != 0 )); then
  state="UNKNOWN"
  reason="database_classifier_failed"
elif [[ -s "$db_stderr" ]]; then
  state="UNKNOWN"
  reason="database_classifier_stderr"
elif [[ "$DB_RESULT" =~ ^(IDLE|BUSY|UNKNOWN):([A-Za-z0-9_,.=+-]+)$ ]]; then
  state="${BASH_REMATCH[1]}"
  reason="${BASH_REMATCH[2]}"
else
  state="UNKNOWN"
  reason="invalid_database_classifier_output"
fi

exit 0
