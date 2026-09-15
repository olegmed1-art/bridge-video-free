#!/usr/bin/env bash
set -euo pipefail

database_url=${1:?usage: 331_autopilot_six_worker_capacity_concurrency.sh DATABASE_URL}
test_tmp_dir=$(mktemp -d)
trap 'rm -rf "$test_tmp_dir"' EXIT

psql "$database_url" -X -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE
    i integer;
BEGIN
    IF EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE goal_type IN (
             'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
         )
           AND status IN (
             'NEW', 'VALIDATING', 'READY', 'RUNNING',
             'WAITING_EXTERNAL', 'EVALUATING'
           )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CONCURRENCY_TEST_REQUIRES_IDLE_BASELINE';
    END IF;

    FOR i IN 1..7 LOOP
        PERFORM * FROM autopilot.register_universal_work_item(
            'sql-six-worker-concurrent-' || i,
            'AUTOPILOT',
            'SIX_WORKER_CONCURRENCY_TEST',
            'Prove concurrent admission remains bounded at six.',
            1700 + i,
            CASE WHEN i = 1 THEN 0 ELSE 10 END,
            jsonb_build_object('fixture', i),
            NULL,
            'database-test',
            'SQL_CONCURRENCY_TEST'
        );
    END LOOP;
END $$;
SQL

run_worker() {
    local worker=$1
    local probe
    local work_item_id
    local lease_epoch

    probe=$(psql "$database_url" -XAt -F '|' -v ON_ERROR_STOP=1 -c \
        "SELECT work_item_id, lease_epoch FROM autopilot.claim_project_work_probe('sql-six-worker-concurrent-${worker}', 60);")
    if [[ -z "$probe" ]]; then
        return 0
    fi
    IFS='|' read -r work_item_id lease_epoch <<<"$probe"

    # Let all claim transactions commit so the database observes overlapping
    # reservations before any worker converts its lease into a task.
    sleep 2
    psql "$database_url" -XAt -v ON_ERROR_STOP=1 -c \
        "SELECT task_id FROM autopilot.materialize_project_work_probe('${work_item_id}'::uuid, 'sql-six-worker-concurrent-${worker}', ${lease_epoch}, true, repeat('${worker}', 40));" \
        >/dev/null
}

pids=()
for worker in 1 2 3 4 5 6 7; do
    run_worker "$worker" >"$test_tmp_dir/worker-${worker}.log" 2>&1 &
    pids+=("$!")
done

worker_failed=0
for index in "${!pids[@]}"; do
    if ! wait "${pids[$index]}"; then
        worker=$((index + 1))
        cat "$test_tmp_dir/worker-${worker}.log" >&2
        worker_failed=1
    fi
done
test "$worker_failed" -eq 0

psql "$database_url" -X -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE
    capacity record;
BEGIN
    SELECT * INTO capacity FROM autopilot.role_worker_capacity_snapshot();
    IF capacity.active_workers <> 6
       OR capacity.active_normal_workers <> 5
       OR capacity.probe_reservations <> 0
       OR capacity.available_workers <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_CONCURRENT_CAPACITY_RESULT_INVALID';
    END IF;
    IF (SELECT count(*) FROM autopilot.project_work_item
         WHERE work_key LIKE 'sql-six-worker-concurrent-%'
           AND state = 'ACTIVE') <> 6 THEN
        RAISE EXCEPTION 'AUTOPILOT_CONCURRENT_ACTIVE_WORK_COUNT_INVALID';
    END IF;
    IF (SELECT count(*) FROM autopilot.project_work_item
         WHERE work_key LIKE 'sql-six-worker-concurrent-%'
           AND state = 'READY'
           AND priority <> 0) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_CONCURRENT_P0_RESERVE_RESULT_INVALID';
    END IF;
END $$;
SQL
