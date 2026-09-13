\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_run uuid;
    v_correction uuid;
    v_regression uuid;
    v_required boolean;
BEGIN
    SELECT school_id INTO v_school
      FROM school
     WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN
        RAISE EXCEPTION 'canonical school missing';
    END IF;

    INSERT INTO analysis_run(
        school_id, algorithm_key, algorithm_version, run_status
    ) VALUES (
        v_school, 'meta-regression-gate-test', 'v1', 'success'
    ) RETURNING analysis_run_id INTO v_run;

    -- An explicitly material failure cannot opt out of regression evidence.
    INSERT INTO correction_record(
        school_id, target_entity_id, target_entity_type,
        correction_class, summary, material, regression_required,
        analysis_run_id
    ) VALUES (
        v_school, v_run, 'analysis_run',
        'technical', 'Material regression gate test', true, false,
        v_run
    ) RETURNING correction_record_id, regression_required
      INTO v_correction, v_required;

    IF NOT v_required THEN
        RAISE EXCEPTION 'material correction was allowed to disable regression requirement';
    END IF;

    INSERT INTO regression_case(
        school_id, correction_record_id, stable_key,
        target_component, test_reference, expected_contract
    ) VALUES (
        v_school, v_correction, 'meta-material-regression-gate-test',
        'meta_reliability_core', 'database/tests/034_meta_material_regression_gate.sql',
        '{"expected":"pass required before resolution"}'::jsonb
    ) RETURNING regression_case_id INTO v_regression;

    -- Defining a regression case is insufficient; it must actually pass.
    BEGIN
        UPDATE correction_record
           SET status='resolved', resolution_notes='should fail without execution'
         WHERE correction_record_id=v_correction;
        RAISE EXCEPTION 'material correction resolved with unexecuted regression case';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='material correction resolved with unexecuted regression case' THEN RAISE; END IF;
    END;

    INSERT INTO regression_execution(
        regression_case_id, analysis_run_id, result,
        observed_contract
    ) VALUES (
        v_regression, v_run, 'fail', '{"observed":"regression still failing"}'::jsonb
    );

    BEGIN
        UPDATE correction_record
           SET status='resolved', resolution_notes='should fail after failed regression'
         WHERE correction_record_id=v_correction;
        RAISE EXCEPTION 'material correction resolved after failed regression';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='material correction resolved after failed regression' THEN RAISE; END IF;
    END;

    INSERT INTO regression_execution(
        regression_case_id, analysis_run_id, result,
        observed_contract
    ) VALUES (
        v_regression, v_run, 'pass', '{"observed":"regression fixed"}'::jsonb
    );

    UPDATE correction_record
       SET status='resolved', resolution_notes='passed regression evidence exists'
     WHERE correction_record_id=v_correction;

    IF NOT EXISTS (
        SELECT 1
          FROM correction_record
         WHERE correction_record_id=v_correction
           AND status='resolved'
           AND resolved_at IS NOT NULL
           AND regression_required
    ) THEN
        RAISE EXCEPTION 'material correction did not resolve after passed regression evidence';
    END IF;
END $$;

ROLLBACK;
