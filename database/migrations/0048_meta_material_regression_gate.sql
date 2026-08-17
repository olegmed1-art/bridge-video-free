\set ON_ERROR_STOP on
BEGIN;

-- Material corrected failures must produce regression evidence, not merely a named test.
-- This is a META reliability rule and does not define any bridge teaching methodology.
CREATE OR REPLACE FUNCTION validate_correction_authority_and_resolution()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.correction_class='methodology' THEN
        NEW.protected_methodology := true;
        IF NEW.teacher_approval_state='not_required' THEN
            NEW.teacher_approval_state := 'pending';
        END IF;
    END IF;

    -- A caller may not downgrade a correction that is explicitly classified as material
    -- by disabling its regression requirement.
    IF NEW.material THEN
        NEW.regression_required := true;
    END IF;

    IF NEW.protected_methodology AND NEW.teacher_approval_state='not_required' THEN
        RAISE EXCEPTION 'protected methodology correction requires explicit teacher approval state';
    END IF;

    IF NEW.teacher_approval_state='approved'
       AND (NEW.approved_by_person_id IS NULL OR NEW.approved_at IS NULL) THEN
        RAISE EXCEPTION 'approved correction requires approving person and timestamp';
    END IF;

    IF NEW.status='resolved' THEN
        IF NEW.protected_methodology AND NEW.teacher_approval_state <> 'approved' THEN
            RAISE EXCEPTION 'protected methodology correction cannot resolve without teacher approval';
        END IF;

        IF NEW.regression_required AND NOT EXISTS (
            SELECT 1
              FROM regression_case rc
              JOIN regression_execution re
                ON re.regression_case_id=rc.regression_case_id
             WHERE rc.correction_record_id=NEW.correction_record_id
               AND rc.status IN ('candidate','active')
               AND re.result='pass'
        ) THEN
            RAISE EXCEPTION 'material correction cannot resolve without passed regression evidence';
        END IF;

        IF NEW.resolved_at IS NULL THEN
            NEW.resolved_at := now();
        END IF;
    ELSE
        NEW.resolved_at := NULL;
    END IF;

    RETURN NEW;
END;
$$;

INSERT INTO schema_migration(migration_key)
VALUES ('0048_meta_material_regression_gate')
ON CONFLICT DO NOTHING;

COMMIT;
