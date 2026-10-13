-- Disposable integration-test instrumentation only. Never a production migration.
CREATE SCHEMA admission_test;
REVOKE ALL ON SCHEMA admission_test FROM PUBLIC;
CREATE TABLE admission_test.probe_control (
    backend_pid integer PRIMARY KEY,
    relation_name text NOT NULL,
    operation text NOT NULL,
    match_fields jsonb NOT NULL DEFAULT '{}',
    action text NOT NULL CHECK (action IN ('PAUSE','FAULT'))
);
CREATE FUNCTION admission_test.probe() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,admission_test AS $$
DECLARE v_control admission_test.probe_control%ROWTYPE; v_payload jsonb;
BEGIN
    v_payload := CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
    SELECT q_control.* INTO v_control FROM admission_test.probe_control AS q_control
      WHERE q_control.backend_pid=pg_backend_pid() AND q_control.relation_name=TG_TABLE_NAME
        AND q_control.operation=TG_OP;
    IF FOUND AND v_payload @> v_control.match_fields THEN
        IF v_control.action='FAULT' THEN
            RAISE EXCEPTION 'ADMISSION_TEST_INJECTED_FAULT' USING ERRCODE='P0001';
        END IF;
        -- Controller holds this test-only session lock. The production admission
        -- has no hook, custom setting, or special authorization path.
        PERFORM pg_advisory_xact_lock(60060,pg_backend_pid());
    END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
REVOKE ALL ON FUNCTION admission_test.probe() FROM PUBLIC;
CREATE TRIGGER test_pause_reservation AFTER INSERT ON video_queue.source_reservation
 FOR EACH ROW EXECUTE FUNCTION admission_test.probe();
CREATE TRIGGER test_pause_enqueue AFTER INSERT ON video_queue.job_event
 FOR EACH ROW EXECUTE FUNCTION admission_test.probe();
CREATE TRIGGER test_pause_binding BEFORE UPDATE ON video_queue.subset_admission
 FOR EACH ROW EXECUTE FUNCTION admission_test.probe();
