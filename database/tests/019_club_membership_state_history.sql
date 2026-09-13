\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_membership uuid;
    v_state text;
    v_event_count integer;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Membership History Test Person')
    RETURNING person_id INTO v_person;

    INSERT INTO club_membership(school_id,person_id,membership_type,status)
    VALUES (v_school,v_person,'history-test','active')
    RETURNING club_membership_id INTO v_membership;

    SELECT count(*) INTO v_event_count
      FROM club_membership_state_event
     WHERE club_membership_id=v_membership;
    IF v_event_count <> 1 THEN
        RAISE EXCEPTION 'membership insert expected one initial state event, got %', v_event_count;
    END IF;

    UPDATE club_membership SET status='paused' WHERE club_membership_id=v_membership;
    UPDATE club_membership SET status='active' WHERE club_membership_id=v_membership;
    UPDATE club_membership SET status='ended', valid_to=now()+interval '1 second'
     WHERE club_membership_id=v_membership;

    SELECT count(*) INTO v_event_count
      FROM club_membership_state_event
     WHERE club_membership_id=v_membership;
    IF v_event_count <> 4 THEN
        RAISE EXCEPTION 'membership lifecycle expected four state events, got %', v_event_count;
    END IF;

    SELECT state INTO v_state
      FROM club_membership_current_state
     WHERE club_membership_id=v_membership;
    IF v_state <> 'ended' THEN
        RAISE EXCEPTION 'membership current state expected ended, got %', v_state;
    END IF;

    -- A closed period allows a new period; an open period of the same type does not.
    INSERT INTO club_membership(school_id,person_id,membership_type,status)
    VALUES (v_school,v_person,'history-test','active');

    BEGIN
        INSERT INTO club_membership(school_id,person_id,membership_type,status)
        VALUES (v_school,v_person,'history-test','pending');
        RAISE EXCEPTION 'second open membership period unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='second open membership period unexpectedly accepted' THEN RAISE; END IF;
    END;
END $$;

DO $$
BEGIN
    IF has_table_privilege('bridge_school_app','club_membership_state_event','INSERT')
       OR has_table_privilege('bridge_school_app','club_membership_state_event','UPDATE')
       OR has_table_privilege('bridge_school_app','club_membership_state_event','DELETE')
       OR NOT has_column_privilege('bridge_school_app','club_membership','status','UPDATE')
       OR has_column_privilege('bridge_school_app','club_membership','person_id','UPDATE')
       OR has_sequence_privilege('bridge_school_app','club_membership_state_event_state_sequence_seq','USAGE') THEN
        RAISE EXCEPTION 'membership lifecycle permissions outside contract';
    END IF;

    IF has_function_privilege('bridge_school_app_principal','seed_club_membership_initial_state()','EXECUTE')
       OR has_function_privilege('bridge_school_app_principal','capture_club_membership_status_change()','EXECUTE')
       OR has_function_privilege('bridge_school_app_principal','validate_club_membership_state_event_scope()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute membership history helper directly';
    END IF;
END $$;

ROLLBACK;
