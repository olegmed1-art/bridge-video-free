\set ON_ERROR_STOP on
BEGIN;

DO $rollback$
DECLARE
    function_sql text;
    chat_join text := $join$      JOIN autopilot.role_chat_registry AS chat
        ON chat.role_id = item.role AND chat.enabled
$join$;
    chat_wait text := $wait$                   WHEN EXISTS (
                       SELECT 1
                         FROM autopilot.project_work_item AS item
                        WHERE item.state IN ('READY', 'BLOCKED')
                          AND item.not_before <= now()
                          AND (item.probe_lease_until IS NULL OR item.probe_lease_until < now())
                          AND NOT EXISTS (
                              SELECT 1
                                FROM autopilot.role_chat_registry AS chat
                               WHERE chat.role_id = item.role AND chat.enabled
                          )
                   ) THEN 'WAITING_FOR_CHAT_TARGET'
$wait$;
BEGIN
    SELECT pg_get_functiondef(
        'autopilot.claim_project_work_probe(text,integer)'::regprocedure
    ) INTO function_sql;
    IF function_sql IS NULL
       OR strpos(function_sql, chat_join) = 0
       OR strpos(function_sql, chat_wait) = 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_CHAT_TARGET_GATE_DEFINITION_UNEXPECTED';
    END IF;
    function_sql := replace(function_sql, chat_join, '');
    function_sql := replace(function_sql, chat_wait, '');
    EXECUTE function_sql;
END
$rollback$;

COMMENT ON FUNCTION autopilot.claim_project_work_probe(text,integer) IS
'Claims one durable eligible lane and records precise waiting reasons instead of conflating cooldowns and dependencies with idle.';

DELETE FROM public.schema_migration
WHERE migration_key = '0328_autopilot_chat_target_gate';

COMMIT;
