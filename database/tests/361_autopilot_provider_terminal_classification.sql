\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='15s';
SET LOCAL lock_timeout='2s';

CREATE TEMP TABLE task (
    task_id uuid PRIMARY KEY,
    status text NOT NULL,
    terminal_reason_code text,
    safe_summary_json jsonb NOT NULL
) ON COMMIT DROP;

CREATE TEMP TABLE provider_circuit_state (
    provider_id text PRIMARY KEY,
    state text NOT NULL,
    consecutive_failures integer NOT NULL,
    opened_at timestamptz,
    hold_until timestamptz,
    last_failure_code text,
    last_success_at timestamptz,
    probe_in_flight boolean NOT NULL,
    updated_at timestamptz NOT NULL
) ON COMMIT DROP;

CREATE TEMP TABLE role_dispatch_outbox (
    dispatch_id uuid PRIMARY KEY,
    task_id uuid NOT NULL,
    status text NOT NULL,
    completed_at timestamptz
) ON COMMIT DROP;

DO $instrument$
DECLARE
    definition text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0361_autopilot_provider_terminal_classification'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0361_MIGRATION_MISSING';
    END IF;

    definition:=pg_get_functiondef(
        'autopilot.on_role_dispatch_provider_success()'::regprocedure
    );
    IF strpos(definition,'CODEX_PROVIDER_GENERIC_FAILURE')=0
       OR strpos(definition,'provider_failure')=0 THEN
        RAISE EXCEPTION 'PROVIDER_TERMINAL_CLASSIFICATION_NOT_INSTALLED';
    END IF;

    definition:=replace(
        replace(definition,'autopilot.','pg_temp.'),
        '''autopilot''','''pg_temp'''
    );
    IF strpos(definition,'autopilot.')>0 THEN
        RAISE EXCEPTION 'PROVIDER_TERMINAL_FIXTURE_NOT_ISOLATED';
    END IF;
    EXECUTE definition;
END $instrument$;

CREATE TRIGGER provider_terminal_fixture
AFTER UPDATE OF status ON pg_temp.role_dispatch_outbox
FOR EACH ROW
WHEN (
    NEW.status='CALLBACK_ACCEPTED'
    AND OLD.status IS DISTINCT FROM NEW.status
)
EXECUTE FUNCTION pg_temp.on_role_dispatch_provider_success();

INSERT INTO pg_temp.provider_circuit_state
VALUES (
    'CODEX','OPEN',2,now()-interval '1 minute',
    now()+interval '14 minutes','CODEX_PROVIDER_GENERIC_FAILURE',
    now()-interval '2 hours',true,now()
);

INSERT INTO pg_temp.task
VALUES
(
    md5('business-blocked')::uuid,'FAILED_CLOSED',
    'CURRENT_MAIN_NOT_ANCESTOR',
    '{"result_code":"CURRENT_MAIN_NOT_ANCESTOR"}'::jsonb
),
(
    md5('provider-one')::uuid,'FAILED_CLOSED',
    'CODEX_PROVIDER_GENERIC_FAILURE',
    '{"result_code":"CODEX_PROVIDER_GENERIC_FAILURE"}'::jsonb
),
(
    md5('provider-two')::uuid,'FAILED_CLOSED',
    'CODEX_RESULT_DEADLINE_EXCEEDED',
    '{"result_code":"CODEX_RESULT_DEADLINE_EXCEEDED"}'::jsonb
),
(
    md5('provider-success')::uuid,'DONE',
    'CODEX_CLOUD_RESULT_RETAINED',
    '{"result_code":"AUDIT_CLEAN"}'::jsonb
);

INSERT INTO pg_temp.role_dispatch_outbox
VALUES
(
    md5('outbox-business')::uuid,md5('business-blocked')::uuid,
    'SENT',clock_timestamp()
),
(
    md5('outbox-one')::uuid,md5('provider-one')::uuid,
    'SENT',clock_timestamp()
),
(
    md5('outbox-two')::uuid,md5('provider-two')::uuid,
    'SENT',clock_timestamp()
),
(
    md5('outbox-success')::uuid,md5('provider-success')::uuid,
    'SENT',clock_timestamp()
);

UPDATE pg_temp.role_dispatch_outbox
   SET status='CALLBACK_ACCEPTED'
 WHERE dispatch_id=md5('outbox-business')::uuid;

DO $assert_business$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_temp.provider_circuit_state
         WHERE provider_id='CODEX'
           AND state='CLOSED'
           AND consecutive_failures=0
           AND last_failure_code IS NULL
           AND last_success_at=(
               SELECT completed_at
                 FROM pg_temp.role_dispatch_outbox
                WHERE dispatch_id=md5('outbox-business')::uuid
           )
    ) THEN
        RAISE EXCEPTION
            'BUSINESS_TERMINAL_DID_NOT_CLOSE_PROVIDER_CIRCUIT';
    END IF;
END $assert_business$;

UPDATE pg_temp.role_dispatch_outbox
   SET status='CALLBACK_ACCEPTED'
 WHERE dispatch_id=md5('outbox-one')::uuid;

DO $assert_first$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_temp.provider_circuit_state
         WHERE provider_id='CODEX'
           AND state='CLOSED'
           AND consecutive_failures=1
           AND last_failure_code='CODEX_PROVIDER_GENERIC_FAILURE'
           AND last_success_at=(
               SELECT completed_at
                 FROM pg_temp.role_dispatch_outbox
                WHERE dispatch_id=md5('outbox-business')::uuid
           )
    ) THEN
        RAISE EXCEPTION 'FIRST_PROVIDER_FAILURE_CLASSIFICATION_INVALID';
    END IF;
END $assert_first$;

UPDATE pg_temp.role_dispatch_outbox
   SET status='CALLBACK_ACCEPTED'
 WHERE dispatch_id=md5('outbox-two')::uuid;

DO $assert_second$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_temp.provider_circuit_state
         WHERE provider_id='CODEX'
           AND state='OPEN'
           AND consecutive_failures=2
           AND last_failure_code='CODEX_RESULT_DEADLINE_EXCEEDED'
           AND opened_at IS NOT NULL
           AND hold_until>now()
    ) THEN
        RAISE EXCEPTION
            'SECOND_PROVIDER_FAILURE_DID_NOT_OPEN_CIRCUIT';
    END IF;
END $assert_second$;

UPDATE pg_temp.role_dispatch_outbox
   SET status='CALLBACK_ACCEPTED'
 WHERE dispatch_id=md5('outbox-success')::uuid;

DO $assert_success$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_temp.provider_circuit_state
         WHERE provider_id='CODEX'
           AND state='CLOSED'
           AND consecutive_failures=0
           AND opened_at IS NULL
           AND hold_until IS NULL
           AND last_failure_code IS NULL
           AND probe_in_flight=false
           AND last_success_at=(
               SELECT completed_at
                 FROM pg_temp.role_dispatch_outbox
                WHERE dispatch_id=md5('outbox-success')::uuid
           )
    ) THEN
        RAISE EXCEPTION 'SUCCESS_DID_NOT_RESET_PROVIDER_CIRCUIT';
    END IF;
END $assert_success$;

SELECT 4 AS cases,0 AS failures;
ROLLBACK;
