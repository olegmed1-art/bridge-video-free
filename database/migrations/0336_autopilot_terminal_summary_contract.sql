\set ON_ERROR_STOP on
BEGIN;

-- Producer-only repair. No callback, identity, head, mode or deadline gate changes.
-- The existing webhook publisher copies this canonical task_spec_json verbatim.
CREATE TABLE IF NOT EXISTS autopilot.migration_0336_function_backup (
    function_key text PRIMARY KEY,
    function_definition text NOT NULL
        CHECK (length(function_definition) BETWEEN 100 AND 100000)
);
REVOKE ALL ON TABLE autopilot.migration_0336_function_backup
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

DO $repair$
DECLARE
    original text;
    patched text;
    needle text := '''assignment_schema'',''SLAVIK_DISPATCH_ASSIGNMENT_V1'',';
    contract jsonb := '{"instruction":"Use the sentence matching the actual terminal status as summary. Put diagnostic details before the result envelope; preserve status and result_code.","SUCCEEDED":"Task completed. See execution details above.","BLOCKED":"Task blocked. See execution details above."}'::jsonb;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.schema_migration
                   WHERE migration_key='0333_autopilot_dispatch_assignment_coherence') THEN
        RAISE EXCEPTION 'SUMMARY_CONTRACT_REQUIRES_0333';
    END IF;
    original:=pg_get_functiondef('autopilot.get_dispatch_assignment(uuid)'::regprocedure);
    IF encode(public.digest(original,'sha256'),'hex')
       <> '21684c89b169715e926be57bb4136a44199ca4296e0097627c7ad2c03699c42e'
       OR (length(original)-length(replace(original,needle,'')))/length(needle)<>2
       OR position('terminal_summary_contract' IN original)>0 THEN
        RAISE EXCEPTION 'SUMMARY_CONTRACT_SOURCE_DRIFT';
    END IF;
    -- Inject into BOTH the emitted JSON and its existing 4096-byte admission check.
    patched:=replace(original,needle,
        '''terminal_summary_contract'','||quote_literal(contract::text)||'::jsonb, '||needle);
    INSERT INTO autopilot.migration_0336_function_backup VALUES
        ('before',original),('after',patched)
    ON CONFLICT (function_key) DO NOTHING;
    IF (SELECT function_definition FROM autopilot.migration_0336_function_backup
        WHERE function_key='before') IS DISTINCT FROM original
       OR (SELECT function_definition FROM autopilot.migration_0336_function_backup
           WHERE function_key='after') IS DISTINCT FROM patched THEN
        RAISE EXCEPTION 'SUMMARY_CONTRACT_BACKUP_CONFLICT';
    END IF;
    EXECUTE patched;
END $repair$;
INSERT INTO public.schema_migration(migration_key)
VALUES ('0336_autopilot_terminal_summary_contract');
COMMIT;
