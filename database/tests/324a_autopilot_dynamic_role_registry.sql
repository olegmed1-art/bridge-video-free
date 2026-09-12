\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    qa_item uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0324a_autopilot_dynamic_role_registry'
    ) OR NOT autopilot.role_is_enabled('QA')
      OR NOT autopilot.role_is_enabled('AUTOPILOT')
      OR autopilot.role_is_enabled('NOT_REGISTERED') THEN
        RAISE EXCEPTION 'AUTOPILOT_DYNAMIC_ROLE_REGISTRY_INVALID';
    END IF;

    SELECT work_item_id INTO qa_item
      FROM autopilot.register_universal_work_item(
          'sql-dynamic-role-324a', 'QA', 'REGISTRY_CONTRACT_TEST',
          'Verify a registered non-legacy role without external mutation.',
          1150, 0, '{"media":false}'::jsonb, NULL,
          'database-test', 'SQL_TEST'
      );
    IF qa_item IS NULL OR (
        SELECT count(*) FROM autopilot.project_work_item
         WHERE work_key='sql-dynamic-role-324a'
    ) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_DYNAMIC_ROLE_REGISTRATION_FAILED';
    END IF;

    BEGIN
        PERFORM * FROM autopilot.register_universal_work_item(
            'sql-unknown-role-324a', 'NOT_REGISTERED', 'REGISTRY_CONTRACT_TEST',
            'This must fail closed.', 1150, 0, '{}'::jsonb, NULL,
            'database-test', 'SQL_TEST'
        );
        RAISE EXCEPTION 'AUTOPILOT_UNKNOWN_ROLE_ACCEPTED';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'AUTOPILOT_UNIVERSAL_WORK_ITEM_INVALID' THEN RAISE; END IF;
    END;
END $$;

ROLLBACK;
