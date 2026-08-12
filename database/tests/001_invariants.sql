\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_student uuid;
    v_changeset uuid;
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

    -- Person x School -> one Student.
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
        1, v_changeset, uuidv7(), 'ci', 'probe-1', 'hash-a', '{}'::jsonb
    ) RETURNING event_id INTO v_event;

    -- Unpublished committed event must have no replay position.
    IF (SELECT event_position FROM domain_event WHERE event_id=v_event) IS NOT NULL THEN
        RAISE EXCEPTION 'event positioned before publication';
    END IF;

    v_pos := allocate_event_position(v_school::text, v_event);
    IF v_pos <> 1 THEN RAISE EXCEPTION 'first partition position expected 1, got %', v_pos; END IF;

    -- Same aggregate version cannot be silently written twice.
    failed := false;
    BEGIN
        INSERT INTO domain_event(
            school_id, partition_key, event_type, aggregate_id, aggregate_type,
            aggregate_version, changeset_id, correlation_id,
            idempotency_namespace, idempotency_key, payload_hash, payload
        ) VALUES (
            v_school, v_school::text, 'CIProbeAgain', v_student, 'Student',
            1, v_changeset, uuidv7(), 'ci', 'probe-2', 'hash-b', '{}'::jsonb
        );
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'aggregate version guard failed'; END IF;

    -- Exact duplicate ingestion is rejected by unique identity.
    INSERT INTO ingestion_run(school_id, source_system) VALUES (v_school, 'ci') RETURNING ingestion_run_id INTO STRICT v_changeset;
    INSERT INTO ingestion_item(ingestion_run_id,native_namespace,native_key,payload_hash,status)
    VALUES(v_changeset,'ci','same','hash','new');
    failed := false;
    BEGIN
        INSERT INTO ingestion_item(ingestion_run_id,native_namespace,native_key,payload_hash,status)
        VALUES(v_changeset,'ci','same','hash','duplicate');
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'ingestion idempotency invariant failed'; END IF;

    -- Same native key with changed payload is allowed as source correction.
    INSERT INTO ingestion_item(ingestion_run_id,native_namespace,native_key,payload_hash,status)
    VALUES(v_changeset,'ci','same','hash-corrected','updated');

    -- Derived dependency cannot self-reference.
    failed := false;
    BEGIN
        INSERT INTO dependency_edge(school_id,parent_entity_id,child_entity_id,dependency_type)
        VALUES(v_school,v_student,v_student,'derived_from');
    EXCEPTION WHEN check_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'dependency self-cycle guard failed'; END IF;
END $$;

-- Tests are deliberately rolled back; production data remains untouched.
ROLLBACK;
