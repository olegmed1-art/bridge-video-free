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
# authoritative workload source below proves absence of work. Sources are
# evaluated independently; any UNKNOWN result has precedence over BUSY so a
# partially successful check can never be normalized to an actionable state.

LAB_DIR="${ASSISTANT_LAB_DIR:-/opt/bridge-school/assistant-lab}"
LAB_ENV="${ASSISTANT_LAB_ENV_FILE:-$LAB_DIR/assistant-lab.env}"
AUTOPILOT_ENV="${AUTOPILOT_ENV_FILE:-/opt/bridge-school/school-autopilot/autopilot-shadow.env}"
OBSERVER_DIR="${ASSISTANT_LAB_OBSERVER_DIR:-/opt/bridge-school/assistant-lab-observer}"
VIDEO_DIR="${UNIVERSAL_VIDEO_DIR:-/opt/bridge-school/universal-video}"
PYTHON="${ASSISTANT_LAB_PYTHON:-$LAB_DIR/.venv/bin/python}"
UV_SECRETS_ENV="${UNIVERSAL_VIDEO_SECRETS_ENV_FILE:-$VIDEO_DIR/universal-video-secrets.env}"
QUEUE_DSN_FILE="${BRIDGE_VIDEO_QUEUE_DSN_FILE:-}"
HOST_LEASE_FILE="${ORACLE_HOST_LEASE_FILE:-/run/bridge-school/oracle-host-lease}"
MAX_SOURCE_AGE_SECONDS="${ORACLE_IDLE_MAX_SOURCE_AGE_SECONDS:-60}"
MAX_FUTURE_SKEW_SECONDS="${ORACLE_IDLE_MAX_FUTURE_SKEW_SECONDS:-5}"
MAX_HOST_LEASE_REMAINING_SECONDS="${ORACLE_IDLE_MAX_HOST_LEASE_REMAINING_SECONDS:-86400}"
# Only active leaves belong here. Universal Video terminal receipts under
# done/failed/results/progress and Observer terminal jobs under done/failed are
# durable evidence, not active work. The container mounts VIDEO_DIR/spool at
# /var/lib/universal-video/spool, so the host proof must inspect the host path.
REQUIRED_LOCAL_SPOOLS="${ORACLE_IDLE_REQUIRED_LOCAL_SPOOLS:-$VIDEO_DIR/spool/inbox:$VIDEO_DIR/spool/running:$OBSERVER_DIR/jobs/pending:$OBSERVER_DIR/jobs/running}"
CANONICAL_IDLE_REASON='jobs=0,research=0,research_children=0,control=0,operator_lease=0,autopilot=0,video=0'

started_at_epoch="0"
db_stderr=""
db_idle_reason=""
declare -a busy_reasons=()
declare -a unknown_reasons=()

mark_busy() {
  busy_reasons+=("$1")
}

mark_unknown() {
  unknown_reasons+=("$1")
}

finish() {
  local observed_at_epoch="0"
  local state="UNKNOWN"
  local reason="unclassified"

  if [[ -n "$db_stderr" ]]; then
    rm -f "$db_stderr" 2>/dev/null || true
  fi

  if observed_at_epoch="$(date +%s 2>/dev/null)" && [[ "$observed_at_epoch" =~ ^[0-9]+$ ]]; then
    :
  else
    observed_at_epoch="0"
    mark_unknown "clock_unavailable"
  fi

  if (( ${#unknown_reasons[@]} > 0 )); then
    state="UNKNOWN"
    reason="${unknown_reasons[0]}"
  elif (( ${#busy_reasons[@]} > 0 )); then
    state="BUSY"
    reason="${busy_reasons[0]}"
  elif [[ -n "$db_idle_reason" ]]; then
    state="IDLE"
    reason="$db_idle_reason"
  else
    state="UNKNOWN"
    reason="database_idle_proof_missing"
  fi

  printf 'ORACLE_IDLE_CONTRACT_VERSION=2\n'
  printf 'ORACLE_IDLE_STARTED_AT_EPOCH=%s\n' "$started_at_epoch"
  printf 'ORACLE_IDLE_OBSERVED_AT_EPOCH=%s\n' "$observed_at_epoch"
  printf 'ORACLE_IDLE_REASON=%s\n' "$reason"
  printf 'ORACLE_IDLE_STATE=%s\n' "$state"
}
trap finish EXIT

started_at_epoch="$(date +%s 2>/dev/null || true)"
if [[ ! "$started_at_epoch" =~ ^[0-9]+$ ]]; then
  started_at_epoch="0"
  mark_unknown "clock_unavailable"
  exit 0
fi
if [[ ! "$MAX_SOURCE_AGE_SECONDS" =~ ^[1-9][0-9]{0,5}$ ]]; then
  mark_unknown "invalid_max_source_age"
  exit 0
fi
if [[ ! "$MAX_FUTURE_SKEW_SECONDS" =~ ^[0-9]{1,5}$ ]]; then
  mark_unknown "invalid_max_future_skew"
  exit 0
fi
if [[ ! "$MAX_HOST_LEASE_REMAINING_SECONDS" =~ ^[1-9][0-9]{0,6}$ ]]; then
  mark_unknown "invalid_max_host_lease_bound"
  exit 0
fi

python_available=1
if [[ ! -x "$PYTHON" ]]; then
  python_available=0
  mark_unknown "assistant_lab_python_missing"
fi

# The resident Assistant Lab daemon being active is expected and does not by
# itself make the host BUSY. Any absent, failed, or transitioning service state
# means its local runtime telemetry cannot be proved and is therefore UNKNOWN.
if ! command -v systemctl >/dev/null 2>&1; then
  mark_unknown "systemctl_missing"
else
  set +e
  worker_state="$(systemctl is-active assistant-lab.service 2>/dev/null)"
  worker_rc=$?
  set -e
  if (( worker_rc != 0 )) || [[ "$worker_state" != "active" ]]; then
    mark_unknown "assistant_lab_service_${worker_state:-unknown}"
  fi

  # DDS3 mass launch workflows return while their systemd jobs are still
  # running. The workflow concurrency fence therefore cannot represent the
  # full compute lifetime; the final STOP proof must read every supported mass
  # unit directly. Only an exact inactive result proves absence. Active work is
  # BUSY, while failed, transitioning, missing, or unreadable unit telemetry is
  # UNKNOWN and must never authorize STOP.
  for mass_unit in dds3-mass@10000.service dds3-mass@30000.service; do
    set +e
    mass_state="$(systemctl is-active "$mass_unit" 2>/dev/null)"
    mass_rc=$?
    set -e
    case "$mass_state:$mass_rc" in
      active:0) mark_busy "dds3_mass_service_active" ;;
      inactive:3) ;;
      *) mark_unknown "dds3_mass_service_${mass_state:-unknown}" ;;
    esac
  done
fi

# A local operator/maintenance lease is a bounded keep-alive signal. Absence is
# provable only when the containing directory itself is inspectable.
lease_parent="${HOST_LEASE_FILE%/*}"
[[ "$lease_parent" != "$HOST_LEASE_FILE" ]] || lease_parent='.'
if [[ ! -d "$lease_parent" || -L "$lease_parent" || ! -r "$lease_parent" || ! -x "$lease_parent" ]]; then
  mark_unknown "host_lease_directory_unavailable"
elif [[ -e "$HOST_LEASE_FILE" || -L "$HOST_LEASE_FILE" ]]; then
  if [[ ! -f "$HOST_LEASE_FILE" || -L "$HOST_LEASE_FILE" || ! -r "$HOST_LEASE_FILE" ]]; then
    mark_unknown "host_lease_unreadable"
  else
    set +e
    lease_line="$(cat "$HOST_LEASE_FILE" 2>/dev/null)"
    lease_read_rc=$?
    set -e
    if (( lease_read_rc != 0 )); then
      mark_unknown "host_lease_unreadable"
    elif [[ ! "$lease_line" =~ ^expires_at_epoch=([0-9]{10,})$ ]]; then
      mark_unknown "host_lease_invalid"
    else
      lease_expires="${BASH_REMATCH[1]}"
      now_epoch="$(date +%s 2>/dev/null || true)"
      if [[ ! "$now_epoch" =~ ^[0-9]+$ ]]; then
        mark_unknown "clock_unavailable"
      elif (( lease_expires <= now_epoch )); then
        mark_unknown "host_lease_stale"
      elif (( lease_expires - now_epoch > MAX_HOST_LEASE_REMAINING_SECONDS )); then
        mark_unknown "host_lease_unbounded"
      else
        mark_busy "host_lease_active"
      fi
    fi
  fi
fi

# Observer experiments are independent work. pgrep exit status 1 proves no
# match; every other error is UNKNOWN rather than a false empty result.
if ! command -v pgrep >/dev/null 2>&1; then
  mark_unknown "pgrep_missing"
else
  set +e
  pgrep -f '[a]ssistant_lab.*observer.*experiment|[o]racle_assistant_lab_observer.*run' >/dev/null 2>&1
  pgrep_rc=$?
  set -e
  case "$pgrep_rc" in
    0) mark_busy "observer_experiment_process" ;;
    1) ;;
    *) mark_unknown "process_telemetry_unavailable" ;;
  esac
fi

# Every configured local spool is authoritative. Continue checking all paths so
# a known BUSY spool cannot mask a missing/unreadable sibling source.
if [[ -z "$REQUIRED_LOCAL_SPOOLS" ]]; then
  mark_unknown "local_spool_set_missing"
else
  IFS=':' read -r -a spool_paths <<< "$REQUIRED_LOCAL_SPOOLS"
  if (( ${#spool_paths[@]} == 0 )); then
    mark_unknown "local_spool_set_missing"
  else
    for spool in "${spool_paths[@]}"; do
      if [[ -z "$spool" ]]; then
        mark_unknown "local_spool_path_empty"
        continue
      fi
      if [[ ! -d "$spool" || -L "$spool" || ! -r "$spool" || ! -x "$spool" ]]; then
        mark_unknown "local_spool_unavailable"
        continue
      fi
      spool_work=""
      set +e
      spool_work="$(find "$spool" -mindepth 1 -print -quit 2>/dev/null)"
      spool_rc=$?
      set -e
      if (( spool_rc != 0 )); then
        mark_unknown "local_spool_scan_failed"
      elif [[ -n "$spool_work" ]]; then
        mark_busy "local_spool_has_work"
      fi
    done
  fi
fi

read_single_assignment() {
  local path="$1"
  local key="$2"
  local source="$3"
  local destination="$4"
  local matches_text=""
  local grep_rc=0
  local value=""
  local first=""
  local last=""
  local -a matches=()

  printf -v "$destination" '%s' ''
  if [[ ! -f "$path" || -L "$path" || ! -r "$path" ]]; then
    mark_unknown "${source}_env_unavailable"
    return 1
  fi

  set +e
  matches_text="$(grep -E "^${key}=" "$path" 2>/dev/null)"
  grep_rc=$?
  set -e
  if (( grep_rc == 1 )); then
    mark_unknown "${source}_dsn_missing"
    return 1
  elif (( grep_rc != 0 )); then
    mark_unknown "${source}_env_unreadable"
    return 1
  fi

  mapfile -t matches <<< "$matches_text"
  if (( ${#matches[@]} != 1 )); then
    mark_unknown "${source}_dsn_ambiguous"
    return 1
  fi
  value="${matches[0]#${key}=}"
  first="${value:0:1}"
  last="${value: -1}"
  if [[ "$first" == \" || "$first" == \' || "$last" == \" || "$last" == \' ]]; then
    if [[ "$first" == "$last" ]] && { [[ "$first" == \" ]] || [[ "$first" == \' ]]; } && (( ${#value} >= 2 )); then
      value="${value:1:${#value}-2}"
    else
      mark_unknown "${source}_dsn_quote_invalid"
      return 1
    fi
  fi
  if [[ -z "$value" || "$value" == *$'\r'* || "$value" == *$'\n'* ]]; then
    mark_unknown "${source}_dsn_empty_or_invalid"
    return 1
  fi
  printf -v "$destination" '%s' "$value"
  return 0
}

assistant_dsn=""
autopilot_dsn=""
queue_dsn=""
read_single_assignment "$LAB_ENV" 'ASSISTANT_LAB_DATABASE_URL' 'assistant_lab' assistant_dsn || true
read_single_assignment "$AUTOPILOT_ENV" 'AUTOPILOT_DATABASE_URL' 'autopilot' autopilot_dsn || true

if [[ -z "$QUEUE_DSN_FILE" ]]; then
  read_single_assignment "$UV_SECRETS_ENV" 'BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE' 'video_queue_file' QUEUE_DSN_FILE || true
fi
if [[ -z "$QUEUE_DSN_FILE" || "$QUEUE_DSN_FILE" != /* || "$QUEUE_DSN_FILE" == *'..'* ]]; then
  mark_unknown "video_queue_dsn_path_invalid"
elif [[ ! -f "$QUEUE_DSN_FILE" || -L "$QUEUE_DSN_FILE" || ! -r "$QUEUE_DSN_FILE" ]]; then
  mark_unknown "video_queue_dsn_unavailable"
else
  queue_lines=()
  set +e
  mapfile -t queue_lines < "$QUEUE_DSN_FILE"
  queue_read_rc=$?
  set -e
  if (( queue_read_rc != 0 )); then
    mark_unknown "video_queue_dsn_unreadable"
  elif (( ${#queue_lines[@]} != 1 )); then
    mark_unknown "video_queue_dsn_ambiguous"
  elif [[ -z "${queue_lines[0]}" || "${queue_lines[0]}" == *$'\r'* ]]; then
    mark_unknown "video_queue_dsn_empty_or_invalid"
  else
    queue_dsn="${queue_lines[0]}"
  fi
fi

# Query every live database even when another database is unavailable. The
# helper aggregates source outcomes with UNKNOWN precedence, proving that a
# partial success or a known BUSY source never hides unavailable telemetry.
if (( python_available == 1 )) \
  && [[ -n "$assistant_dsn" ]] \
  && [[ -n "$autopilot_dsn" ]] \
  && [[ -n "$queue_dsn" ]]; then
  export ASSISTANT_LAB_DATABASE_URL="$assistant_dsn"
  export AUTOPILOT_DATABASE_URL="$autopilot_dsn"
  export BRIDGE_VIDEO_QUEUE_DATABASE_URL="$queue_dsn"
  export ORACLE_IDLE_MAX_SOURCE_AGE_SECONDS="$MAX_SOURCE_AGE_SECONDS"
  export ORACLE_IDLE_MAX_FUTURE_SKEW_SECONDS="$MAX_FUTURE_SKEW_SECONDS"

  db_stderr="$(mktemp 2>/dev/null || true)"
  if [[ -z "$db_stderr" ]]; then
    mark_unknown "database_stderr_capture_failed"
  else
    set +e
    DB_RESULT="$("$PYTHON" - <<'PY' 2>"$db_stderr"
import os
import time
from typing import Any

CANONICAL_IDLE_REASON = (
    "jobs=0,research=0,research_children=0,control=0,"
    "operator_lease=0,autopilot=0,video=0"
)
unknown: list[str] = []
counts: dict[str, int] = {
    "jobs": 0,
    "research": 0,
    "research_children": 0,
    "control": 0,
    "operator_lease": 0,
    "stale_operator_lease": 0,
    "autopilot": 0,
    "video": 0,
}

try:
    import psycopg
except Exception:
    print("UNKNOWN:psycopg_unavailable")
    raise SystemExit

try:
    max_age = int(os.environ["ORACLE_IDLE_MAX_SOURCE_AGE_SECONDS"])
    max_future = int(os.environ["ORACLE_IDLE_MAX_FUTURE_SKEW_SECONDS"])
except Exception:
    print("UNKNOWN:invalid_freshness_policy")
    raise SystemExit


def mark_unknown(reason: str) -> None:
    if reason not in unknown:
        unknown.append(reason)


def freshness_problem(value: Any, source: str) -> str | None:
    try:
        observed_at = int(value)
    except (TypeError, ValueError):
        return f"{source}_timestamp_invalid"
    now = int(time.time())
    if observed_at <= 0:
        return f"{source}_timestamp_invalid"
    if now - observed_at > max_age:
        return f"{source}_telemetry_stale"
    if observed_at - now > max_future:
        return f"{source}_clock_in_future"
    return None


def connect(dsn: str, application_name: str):
    return psycopg.connect(
        dsn,
        connect_timeout=5,
        application_name=application_name,
        options="-c statement_timeout=5000 -c lock_timeout=1000 -c default_transaction_read_only=on",
    )


assistant_dsn = os.environ.get("ASSISTANT_LAB_DATABASE_URL", "")
autopilot_dsn = os.environ.get("AUTOPILOT_DATABASE_URL", "")
video_dsn = os.environ.get("BRIDGE_VIDEO_QUEUE_DATABASE_URL", "")

try:
    with connect(assistant_dsn, "oracle-idle-state") as conn:
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
        mark_unknown("assistant_lab_snapshot_missing")
    else:
        try:
            values = [int(value) for value in row]
            schema_version, assistant_observed, *assistant_counts = values
        except (TypeError, ValueError):
            mark_unknown("assistant_lab_snapshot_invalid")
        else:
            if schema_version != 2:
                mark_unknown("assistant_lab_snapshot_version")
            problem = freshness_problem(assistant_observed, "assistant_lab")
            if problem:
                mark_unknown(problem)
            if any(value < 0 for value in assistant_counts):
                mark_unknown("assistant_lab_counts_invalid")
            else:
                (
                    counts["jobs"],
                    counts["research"],
                    counts["research_children"],
                    counts["control"],
                    counts["operator_lease"],
                    counts["stale_operator_lease"],
                ) = assistant_counts
except Exception:
    mark_unknown("assistant_lab_telemetry_unavailable")

try:
    with connect(autopilot_dsn, "oracle-idle-autopilot") as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    count(*)::bigint,
                    extract(epoch FROM current_timestamp)::bigint
                FROM autopilot.task_status
                WHERE status NOT IN (
                    'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
                    'DONE', 'CANCELLED'
                )
                """
            )
            row = cur.fetchone()
    if row is None or len(row) != 2:
        mark_unknown("autopilot_snapshot_missing")
    else:
        try:
            active_autopilot, autopilot_observed = map(int, row)
        except (TypeError, ValueError):
            mark_unknown("autopilot_snapshot_invalid")
        else:
            if active_autopilot < 0:
                mark_unknown("autopilot_count_invalid")
            else:
                counts["autopilot"] = active_autopilot
            problem = freshness_problem(autopilot_observed, "autopilot")
            if problem:
                mark_unknown(problem)
except Exception:
    mark_unknown("autopilot_telemetry_unavailable")

try:
    with connect(video_dsn, "oracle-idle-video-queue") as conn:
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
        mark_unknown("video_queue_snapshot_missing")
    else:
        try:
            video_jobs, video_observed = map(int, row)
        except (TypeError, ValueError):
            mark_unknown("video_queue_snapshot_invalid")
        else:
            if video_jobs < 0:
                mark_unknown("video_queue_count_invalid")
            else:
                counts["video"] = video_jobs
            problem = freshness_problem(video_observed, "video_queue")
            if problem:
                mark_unknown(problem)
except Exception:
    mark_unknown("video_queue_telemetry_unavailable")

if counts["stale_operator_lease"] > 0:
    mark_unknown("operator_maintenance_lease_stale")

if unknown:
    print(f"UNKNOWN:{unknown[0]}")
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
else:
    print(f"IDLE:{CANONICAL_IDLE_REASON}")
PY
)"
    db_rc=$?
    set -e

    if (( db_rc != 0 )); then
      mark_unknown "database_classifier_failed"
    elif [[ -s "$db_stderr" ]]; then
      mark_unknown "database_classifier_stderr"
    elif [[ "$DB_RESULT" =~ ^(IDLE|BUSY|UNKNOWN):([A-Za-z0-9_,.=+-]+)$ ]]; then
      db_state="${BASH_REMATCH[1]}"
      db_reason="${BASH_REMATCH[2]}"
      case "$db_state" in
        IDLE)
          db_idle_reason="$db_reason"
          ;;
        BUSY)
          mark_busy "$db_reason"
          ;;
        UNKNOWN)
          mark_unknown "$db_reason"
          ;;
      esac
    else
      mark_unknown "invalid_database_classifier_output"
    fi
  fi

  unset ASSISTANT_LAB_DATABASE_URL
  unset AUTOPILOT_DATABASE_URL
  unset BRIDGE_VIDEO_QUEUE_DATABASE_URL
  unset ORACLE_IDLE_MAX_SOURCE_AGE_SECONDS
  unset ORACLE_IDLE_MAX_FUTURE_SKEW_SECONDS
fi

exit 0
