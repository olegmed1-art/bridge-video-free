\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_run uuid;
    v_checkpoint uuid;
    v_correction uuid;
    v_method_correction uuid;
    v_regression uuid;
    v_source uuid;
    v_recovery uuid;
    v_count integer;
    v_latest_sequence bigint;
    v_latest_checkpoint jsonb;
    v_base_checkpoint jsonb;
    v_approval_state text;
    v_protected boolean;
BEGIN
    SELECT school_id INTO v_school
      FROM school
     WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN
        RAISE EXCEPTION 'canonical school missing';
    END IF;

    INSERT INTO person(preferred_name)
    VALUES ('META Reliability Test Approver')
    RETURNING person_id INTO v_person;

    -- Generic run checkpoint history keeps a durable sequence and synchronizes the
    -- current checkpoint snapshot on the canonical run table.
    INSERT INTO analysis_run(
        school_id, algorithm_key, algorithm_version, run_status
    ) VALUES (
        v_school, 'meta-reliability-test', 'v1', 'running'
    ) RETURNING analysis_run_id INTO v_run;

    v_checkpoint := record_run_checkpoint(
        'analysis', v_run, 'ingest', 'started',
        '{"offset":10}'::jsonb, NULL, '{"source":"test"}'::jsonb
    );
    IF v_checkpoint IS NULL THEN
        RAISE EXCEPTION 'record_run_checkpoint returned null id';
    END IF;

    PERFORM record_run_checkpoint(
        'analysis', v_run, 'analyze', 'progress',
        '{"offset":20,"phase":"analysis"}'::jsonb, NULL, '{}'::jsonb
    );

    SELECT sequence_no, checkpoint
      INTO v_latest_sequence, v_latest_checkpoint
      FROM latest_run_checkpoint
     WHERE run_type='analysis' AND run_id=v_run;
    IF v_latest_sequence <> 2 OR v_latest_checkpoint <> '{"offset":20,"phase":"analysis"}'::jsonb THEN
        RAISE EXCEPTION 'latest checkpoint projection is inconsistent';
    END IF;

    SELECT checkpoint INTO v_base_checkpoint
      FROM analysis_run
     WHERE analysis_run_id=v_run;
    IF v_base_checkpoint <> v_latest_checkpoint THEN
        RAISE EXCEPTION 'analysis_run checkpoint snapshot is not synchronized';
    END IF;

    -- A material correction cannot be resolved until it has a regression case.
    INSERT INTO correction_record(
        school_id, target_entity_id, target_entity_type,
        correction_class, summary, analysis_run_id
    ) VALUES (
        v_school, v_run, 'analysis_run',
        'technical', 'Test correction requiring regression', v_run
    ) RETURNING correction_record_id INTO v_correction;

    BEGIN
        UPDATE correction_record
           SET status='resolved', resolution_notes='should fail before regression'
         WHERE correction_record_id=v_correction;
        RAISE EXCEPTION 'material correction resolved without regression';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='material correction resolved without regression' THEN RAISE; END IF;
    END;

    INSERT INTO regression_case(
        school_id, correction_record_id, stable_key,
        target_component, test_reference, expected_contract
    ) VALUES (
        v_school, v_correction, 'meta-reliability-test-correction',
        'meta_reliability_core', 'database/tests/033_meta_reliability_core.sql',
        '{"expected":"no recurrence"}'::jsonb
    ) RETURNING regression_case_id INTO v_regression;

    INSERT INTO regression_execution(
        regression_case_id, analysis_run_id, result, observed_contract
    ) VALUES (
        v_regression, v_run, 'pass', '{"observed":"no recurrence"}'::jsonb
    );

    UPDATE correction_record
       SET status='resolved', resolution_notes='regression attached'
     WHERE correction_record_id=v_correction;

    IF NOT EXISTS (
        SELECT 1 FROM correction_record
         WHERE correction_record_id=v_correction
           AND status='resolved' AND resolved_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'corrected failure did not reach resolved state after regression';
    END IF;

    -- Methodology corrections are always protected and default to pending teacher
    -- approval. A regression alone is insufficient to resolve them.
    INSERT INTO correction_record(
        school_id, target_entity_id, target_entity_type,
        correction_class, summary
    ) VALUES (
        v_school, uuidv7(), 'methodology_rule',
        'methodology', 'Protected methodology correction test'
    ) RETURNING correction_record_id, protected_methodology, teacher_approval_state
      INTO v_method_correction, v_protected, v_approval_state;

    IF NOT v_protected OR v_approval_state <> 'pending' THEN
        RAISE EXCEPTION 'methodology correction was not automatically protected/pending';
    END IF;

    INSERT INTO regression_case(
        school_id, correction_record_id, stable_key,
        target_component, test_reference, expected_contract
    ) VALUES (
        v_school, v_method_correction, 'meta-methodology-protection-test',
        'protected_methodology', 'database/tests/033_meta_reliability_core.sql',
        '{"teacher_approval_required":true}'::jsonb
    );

    BEGIN
        UPDATE correction_record
           SET status='resolved', resolution_notes='should fail without approval'
         WHERE correction_record_id=v_method_correction;
        RAISE EXCEPTION 'protected methodology correction resolved without teacher approval';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='protected methodology correction resolved without teacher approval' THEN RAISE; END IF;
    END;

    UPDATE correction_record
       SET teacher_approval_state='approved',
           approved_by_person_id=v_person,
           approved_at=now()
     WHERE correction_record_id=v_method_correction;
    UPDATE correction_record
       SET status='resolved', resolution_notes='approved and regression-covered'
     WHERE correction_record_id=v_method_correction;

    IF NOT EXISTS (
        SELECT 1 FROM correction_record
         WHERE correction_record_id=v_method_correction
           AND status='resolved'
           AND protected_methodology
           AND teacher_approval_state='approved'
    ) THEN
        RAISE EXCEPTION 'approved methodology correction did not resolve';
    END IF;

    -- Structured source rights/access evidence is append-only and source-scoped.
    INSERT INTO source(school_id, source_type, title, trust_class)
    VALUES (v_school, 'test', 'META Rights Test Source', 'test')
    RETURNING source_id INTO v_source;

    INSERT INTO source_rights_snapshot(
        school_id, source_id, rights_state, rights_basis,
        allowed_uses, acl_snapshot, authority_class, provenance
    ) VALUES (
        v_school, v_source, 'unknown', 'source metadata only',
        '["internal_review"]'::jsonb,
        '{"visibility":"private"}'::jsonb,
        'source_metadata',
        '{"test":true}'::jsonb
    );

    SELECT count(*) INTO v_count
      FROM source_rights_snapshot
     WHERE source_id=v_source;
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'source rights snapshot was not recorded';
    END IF;

    -- Recovery records separate the checkpoint identity from append-only verification.
    INSERT INTO recovery_checkpoint(
        school_id, checkpoint_type, provider, external_ref, source_fingerprint, notes
    ) VALUES (
        v_school, 'branch', 'neon', 'test-recovery-branch',
        '{"migration":"0019"}'::jsonb, 'transactional test only'
    ) RETURNING recovery_checkpoint_id INTO v_recovery;

    INSERT INTO recovery_verification(
        recovery_checkpoint_id, verification_type, result,
        observed_fingerprint, restore_target_ref
    ) VALUES (
        v_recovery, 'branch_compare', 'success',
        '{"migration":"0019"}'::jsonb, 'test-target'
    );

    IF NOT EXISTS (
        SELECT 1 FROM recovery_verification
         WHERE recovery_checkpoint_id=v_recovery AND result='success'
    ) THEN
        RAISE EXCEPTION 'recovery verification evidence was not recorded';
    END IF;
END $$;

DO $$
BEGIN
    -- Worker can call the guarded checkpoint API but cannot rewrite checkpoint history.
    IF NOT has_function_privilege(
            'bridge_school_worker',
            'record_run_checkpoint(text,uuid,text,text,jsonb,text,jsonb)',
            'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'worker lacks guarded checkpoint capability';
    END IF;
    IF has_table_privilege('bridge_school_worker','run_checkpoint_event','INSERT')
       OR has_table_privilege('bridge_school_worker','run_checkpoint_event','UPDATE')
       OR has_table_privilege('bridge_school_worker','run_checkpoint_event','DELETE') THEN
        RAISE EXCEPTION 'worker can bypass append-only checkpoint API';
    END IF;

    -- Worker can create candidate corrections/regressions but cannot forge teacher approval.
    IF NOT has_column_privilege('bridge_school_worker','correction_record','summary','INSERT')
       OR NOT has_column_privilege('bridge_school_worker','correction_record','status','UPDATE') THEN
        RAISE EXCEPTION 'worker lacks expected correction candidate capability';
    END IF;
    IF has_column_privilege('bridge_school_worker','correction_record','teacher_approval_state','INSERT')
       OR has_column_privilege('bridge_school_worker','correction_record','teacher_approval_state','UPDATE')
       OR has_column_privilege('bridge_school_worker','correction_record','approved_by_person_id','UPDATE') THEN
        RAISE EXCEPTION 'worker can forge protected methodology approval';
    END IF;

    IF NOT has_column_privilege('bridge_school_worker','regression_case','stable_key','INSERT')
       OR NOT has_column_privilege('bridge_school_worker','regression_execution','result','INSERT') THEN
        RAISE EXCEPTION 'worker lacks regression evidence capability';
    END IF;
    IF has_table_privilege('bridge_school_worker','regression_execution','UPDATE')
       OR has_table_privilege('bridge_school_worker','regression_execution','DELETE') THEN
        RAISE EXCEPTION 'worker can rewrite regression execution history';
    END IF;

    -- ACL snapshots are write-only source observations for workers; recovery state is
    -- owner-operated and invisible to member/auth-gateway runtimes.
    IF NOT has_column_privilege('bridge_school_worker','source_rights_snapshot','source_id','INSERT')
       OR has_table_privilege('bridge_school_worker','source_rights_snapshot','SELECT')
       OR has_table_privilege('bridge_school_worker','source_rights_snapshot','UPDATE')
       OR has_table_privilege('bridge_school_worker','source_rights_snapshot','DELETE') THEN
        RAISE EXCEPTION 'source rights runtime boundary is incorrect';
    END IF;

    IF has_table_privilege('bridge_school_member','correction_record','SELECT')
       OR has_table_privilege('bridge_school_member','run_checkpoint_event','SELECT')
       OR has_table_privilege('bridge_school_member','recovery_checkpoint','SELECT')
       OR has_table_privilege('bridge_school_auth_gateway','source_rights_snapshot','SELECT') THEN
        RAISE EXCEPTION 'META internal reliability state leaked to member/auth runtime';
    END IF;
END $$;

ROLLBACK;
