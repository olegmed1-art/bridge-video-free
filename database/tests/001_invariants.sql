\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_student uuid;
    v_changeset uuid;
    v_ingestion uuid;
    v_event uuid;
    v_pos bigint;
    failed boolean;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN
        RAISE EXCEPTION 'school seed missing';
    END IF;

    INSERT INTO person(preferred_name) VALUES ('CI invariant probe') RETURNING person_id INTO v_person;
    INSERT INTO student(school_id, person_id) VALUES (v_school, v_person) RETURNING student_id INTO v_student;

    failed := false;
    BEGIN
        INSERT INTO student(school_id, person_id) VALUES (v_school, v_person);
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'student uniqueness invariant failed'; END IF;

    INSERT INTO changeset(school_id, status, committed_at)
    VALUES (v_school, 'committed', now()) RETURNING changeset_id INTO v_changeset;

    INSERT INTO domain_event(
        school_id, partition_key, event_type, aggregate_id, aggregate_type,
        aggregate_version, changeset_id, correlation_id,
        idempotency_namespace, idempotency_key, payload_hash, payload
    ) VALUES (
        v_school, v_school::text, 'CIProbe', v_student, 'Student',
        1, v_changeset, uuidv7(), 'ci', uuidv7()::text, 'hash-a', '{}'::jsonb
    ) RETURNING event_id INTO v_event;

    IF (SELECT event_position FROM domain_event WHERE event_id=v_event) IS NOT NULL THEN
        RAISE EXCEPTION 'event positioned before publication';
    END IF;

    v_pos := allocate_event_position(v_school::text, v_event);
    IF v_pos IS NULL OR v_pos <= 0 THEN RAISE EXCEPTION 'invalid event position %', v_pos; END IF;

    failed := false;
    BEGIN
        INSERT INTO domain_event(
            school_id, partition_key, event_type, aggregate_id, aggregate_type,
            aggregate_version, changeset_id, correlation_id,
            idempotency_namespace, idempotency_key, payload_hash, payload
        ) VALUES (
            v_school, v_school::text, 'CIProbeAgain', v_student, 'Student',
            1, v_changeset, uuidv7(), 'ci', uuidv7()::text, 'hash-b', '{}'::jsonb
        );
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'aggregate version guard failed'; END IF;

    INSERT INTO ingestion_run(school_id, source_system)
    VALUES (v_school, 'ci') RETURNING ingestion_run_id INTO v_ingestion;
    INSERT INTO ingestion_item(ingestion_run_id,native_namespace,native_key,payload_hash,status)
    VALUES(v_ingestion,'ci','same','hash','new');
    failed := false;
    BEGIN
        INSERT INTO ingestion_item(ingestion_run_id,native_namespace,native_key,payload_hash,status)
        VALUES(v_ingestion,'ci','same','hash','duplicate');
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'ingestion idempotency invariant failed'; END IF;

    INSERT INTO ingestion_item(ingestion_run_id,native_namespace,native_key,payload_hash,status)
    VALUES(v_ingestion,'ci','same','hash-corrected','updated');

    failed := false;
    BEGIN
        INSERT INTO dependency_edge(school_id,parent_entity_id,child_entity_id,dependency_type)
        VALUES(v_school,v_student,v_student,'derived_from');
    EXCEPTION WHEN check_violation OR raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'dependency self-cycle guard failed'; END IF;
END $$;

ROLLBACK;
