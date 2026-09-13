\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_source uuid;
    v_deal uuid;
BEGIN
    SELECT school_id INTO v_school
      FROM school
     WHERE stable_name='Школа спортивного бриджа';

    IF v_school IS NULL THEN
        RAISE EXCEPTION 'canonical school missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema='public'
           AND table_name='deal'
           AND column_name='stable_key'
           AND is_nullable='NO'
    ) THEN
        RAISE EXCEPTION 'deal.stable_key must be present and NOT NULL';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid='deal'::regclass
           AND conname='deal_school_id_stable_key_key'
    ) THEN
        RAISE EXCEPTION 'deal stable identity uniqueness is missing';
    END IF;

    INSERT INTO source(
        school_id,source_type,title,canonical_locator,trust_class
    ) VALUES (
        v_school,'deal_identity_test','Deal identity test',
        'test:deal-identity/source-1','test'
    ) RETURNING source_id INTO v_source;

    INSERT INTO deal(
        school_id,stable_key,deal_fingerprint,source_id
    ) VALUES (
        v_school,'test:deal-identity/1','test-deal-identity-1',v_source
    ) RETURNING deal_id INTO v_deal;

    IF NOT EXISTS (
        SELECT 1 FROM deal
         WHERE deal_id=v_deal
           AND stable_key='test:deal-identity/1'
    ) THEN
        RAISE EXCEPTION 'deal stable identity was not retained';
    END IF;

    BEGIN
        INSERT INTO deal(
            school_id,stable_key,deal_fingerprint,source_id
        ) VALUES (
            v_school,'test:deal-identity/1','test-deal-identity-2',v_source
        );
        RAISE EXCEPTION 'duplicate deal stable identity was accepted';
    EXCEPTION
        WHEN unique_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO deal(
            school_id,stable_key,deal_fingerprint,source_id
        ) VALUES (
            v_school,'   ','test-deal-identity-3',v_source
        );
        RAISE EXCEPTION 'blank deal stable identity was accepted';
    EXCEPTION
        WHEN check_violation THEN NULL;
    END;
END $$;

ROLLBACK;
