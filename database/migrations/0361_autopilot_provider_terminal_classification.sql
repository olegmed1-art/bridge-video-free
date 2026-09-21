\set ON_ERROR_STOP on
BEGIN;

DO $precondition$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0360_autopilot_health_e2e_acl'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_PROVIDER_TERMINAL_CLASSIFICATION_REQUIRES_0360';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0361_autopilot_provider_terminal_classification'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0361_ALREADY_APPLIED';
    END IF;
END $precondition$;

CREATE TABLE autopilot.migration_0361_function_backup (
    function_key text PRIMARY KEY,
    function_definition text NOT NULL
        CHECK (length(function_definition) BETWEEN 100 AND 100000),
    backed_up_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO autopilot.migration_0361_function_backup(
    function_key,function_definition
)
SELECT 'autopilot.on_role_dispatch_provider_success()',
       pg_get_functiondef(
           'autopilot.on_role_dispatch_provider_success()'::regprocedure
       );

CREATE TABLE autopilot.migration_0361_provider_backup (
    provider_id text PRIMARY KEY,
    state text NOT NULL,
    consecutive_failures integer NOT NULL,
    opened_at timestamptz,
    hold_until timestamptz,
    last_failure_code text,
    last_success_at timestamptz,
    probe_in_flight boolean NOT NULL,
    updated_at timestamptz NOT NULL,
    backed_up_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO autopilot.migration_0361_provider_backup(
    provider_id,state,consecutive_failures,opened_at,hold_until,
    last_failure_code,last_success_at,probe_in_flight,updated_at
)
SELECT provider_id,state,consecutive_failures,opened_at,hold_until,
       last_failure_code,last_success_at,probe_in_flight,updated_at
  FROM autopilot.provider_circuit_state
 WHERE provider_id='CODEX';

CREATE OR REPLACE FUNCTION autopilot.on_role_dispatch_provider_success()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'pg_catalog','autopilot'
AS $function$
DECLARE
    result_code text;
    provider_failure boolean;
BEGIN
    IF NEW.status='CALLBACK_ACCEPTED'
       AND OLD.status IS DISTINCT FROM NEW.status THEN
        SELECT COALESCE(
                   NULLIF(task.safe_summary_json->>'result_code',''),
                   task.terminal_reason_code
               )
          INTO result_code
          FROM autopilot.task AS task
         WHERE task.task_id=NEW.task_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'AUTOPILOT_PROVIDER_TERMINAL_TASK_MISSING';
        END IF;

        provider_failure := result_code IN (
            'CODEX_ACK_DEADLINE_EXCEEDED',
            'CODEX_RESULT_DEADLINE_EXCEEDED',
            'CODEX_PROVIDER_GENERIC_FAILURE'
        );

        IF provider_failure THEN
            UPDATE autopilot.provider_circuit_state AS circuit
               SET consecutive_failures=LEAST(
                       circuit.consecutive_failures+1,1000
                   ),
                   state=CASE
                       WHEN circuit.consecutive_failures+1>=2 THEN 'OPEN'
                       ELSE 'CLOSED'
                   END,
                   opened_at=CASE
                       WHEN circuit.consecutive_failures+1>=2
                           THEN COALESCE(circuit.opened_at,clock_timestamp())
                       ELSE NULL
                   END,
                   hold_until=CASE
                       WHEN circuit.consecutive_failures+1>=2
                           THEN GREATEST(
                               COALESCE(
                                   circuit.hold_until,
                                   '-infinity'::timestamptz
                               ),
                               clock_timestamp()+interval '15 minutes'
                           )
                       ELSE NULL
                   END,
                   last_failure_code=result_code,
                   probe_in_flight=false,
                   updated_at=clock_timestamp()
             WHERE circuit.provider_id='CODEX';
        ELSE
            -- A valid callback with a repository/business result proves that
            -- the provider completed the request, even when the task is BLOCKED.
            UPDATE autopilot.provider_circuit_state
               SET state='CLOSED',
                   consecutive_failures=0,
                   opened_at=NULL,
                   hold_until=NULL,
                   last_failure_code=NULL,
                   last_success_at=COALESCE(
                       NEW.completed_at,clock_timestamp()
                   ),
                   probe_in_flight=false,
                   updated_at=clock_timestamp()
             WHERE provider_id='CODEX';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;

REVOKE ALL ON FUNCTION autopilot.on_role_dispatch_provider_success()
FROM PUBLIC;

-- Repair the false-positive success written by 0356 only when the current
-- last_success_at is itself a provider-failure callback. Other circuit state
-- (for example a timeout not represented by a callback) remains untouched.
WITH terminal_event AS (
    SELECT o.completed_at,
           COALESCE(
               NULLIF(t.safe_summary_json->>'result_code',''),
               t.terminal_reason_code
           ) AS result_code,
           COALESCE(
               NULLIF(t.safe_summary_json->>'result_code',''),
               t.terminal_reason_code
           ) IN (
               'CODEX_ACK_DEADLINE_EXCEEDED',
               'CODEX_RESULT_DEADLINE_EXCEEDED',
               'CODEX_PROVIDER_GENERIC_FAILURE'
           ) AS provider_failure
      FROM autopilot.role_dispatch_outbox o
      JOIN autopilot.task t USING(task_id)
     WHERE o.status='CALLBACK_ACCEPTED'
       AND o.completed_at IS NOT NULL
), last_real_success AS (
    SELECT max(terminal_event.completed_at) AS completed_at
      FROM terminal_event
     WHERE NOT provider_failure
), failure_stats AS (
    SELECT count(*)::integer AS failure_count,
           max(terminal_event.completed_at) AS last_failure_at
      FROM terminal_event
      CROSS JOIN last_real_success
     WHERE provider_failure
       AND terminal_event.completed_at>COALESCE(
           last_real_success.completed_at,'-infinity'::timestamptz
       )
), latest_failure AS (
    SELECT terminal_event.result_code
      FROM terminal_event
      CROSS JOIN last_real_success
     WHERE provider_failure
       AND terminal_event.completed_at>COALESCE(
           last_real_success.completed_at,'-infinity'::timestamptz
       )
     ORDER BY terminal_event.completed_at DESC
     LIMIT 1
)
UPDATE autopilot.provider_circuit_state AS circuit
   SET state=CASE
           WHEN failure_stats.failure_count>=2 THEN 'OPEN'
           ELSE 'CLOSED'
       END,
       consecutive_failures=failure_stats.failure_count,
       opened_at=CASE
           WHEN failure_stats.failure_count>=2
               THEN COALESCE(
                   failure_stats.last_failure_at,clock_timestamp()
               )
           ELSE NULL
       END,
       hold_until=CASE
           WHEN failure_stats.failure_count>=2
               THEN GREATEST(
                   COALESCE(
                       failure_stats.last_failure_at,
                       '-infinity'::timestamptz
                   )+interval '15 minutes',
                   clock_timestamp()+interval '5 minutes'
               )
           ELSE NULL
       END,
       last_failure_code=latest_failure.result_code,
       last_success_at=last_real_success.completed_at,
       probe_in_flight=false,
       updated_at=clock_timestamp()
  FROM last_real_success,failure_stats
  LEFT JOIN latest_failure ON true
 WHERE circuit.provider_id='CODEX'
   AND EXISTS (
       SELECT 1
         FROM terminal_event false_success
        WHERE false_success.provider_failure
          AND false_success.completed_at=circuit.last_success_at
   );

INSERT INTO public.schema_migration(migration_key)
VALUES('0361_autopilot_provider_terminal_classification');

COMMIT;
