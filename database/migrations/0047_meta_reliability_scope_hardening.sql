\set ON_ERROR_STOP on
BEGIN;

-- Cross-school provenance guards for META reliability records.

CREATE OR REPLACE FUNCTION validate_correction_record_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_run_school uuid;
BEGIN
    IF NEW.analysis_run_id IS NOT NULL THEN
        SELECT school_id INTO v_run_school
          FROM analysis_run
         WHERE analysis_run_id=NEW.analysis_run_id;
        IF v_run_school IS NULL OR v_run_school <> NEW.school_id THEN
            RAISE EXCEPTION 'correction analysis run belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER correction_record_scope_guard
BEFORE INSERT OR UPDATE OF school_id, analysis_run_id
ON correction_record
FOR EACH ROW EXECUTE FUNCTION validate_correction_record_scope();

CREATE OR REPLACE FUNCTION validate_regression_execution_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_case_school uuid;
    v_run_school uuid;
    v_algorithm_school uuid;
BEGIN
    SELECT school_id INTO v_case_school
      FROM regression_case
     WHERE regression_case_id=NEW.regression_case_id;
    IF v_case_school IS NULL THEN
        RAISE EXCEPTION 'regression case is missing';
    END IF;

    IF NEW.analysis_run_id IS NOT NULL THEN
        SELECT school_id INTO v_run_school
          FROM analysis_run
         WHERE analysis_run_id=NEW.analysis_run_id;
        IF v_run_school IS NULL OR v_run_school <> v_case_school THEN
            RAISE EXCEPTION 'regression execution analysis run belongs to another school or is missing';
        END IF;
    END IF;

    IF NEW.algorithm_version_id IS NOT NULL THEN
        SELECT a.school_id INTO v_algorithm_school
          FROM algorithm_version av
          JOIN algorithm a ON a.algorithm_id=av.algorithm_id
         WHERE av.algorithm_version_id=NEW.algorithm_version_id;
        IF v_algorithm_school IS NULL OR v_algorithm_school <> v_case_school THEN
            RAISE EXCEPTION 'regression execution algorithm version belongs to another school or is missing';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
CREATE TRIGGER regression_execution_scope_guard
BEFORE INSERT OR UPDATE OF regression_case_id, analysis_run_id, algorithm_version_id
ON regression_execution
FOR EACH ROW EXECUTE FUNCTION validate_regression_execution_scope();

INSERT INTO schema_migration(migration_key)
VALUES ('0047_meta_reliability_scope_hardening')
ON CONFLICT DO NOTHING;

COMMIT;
