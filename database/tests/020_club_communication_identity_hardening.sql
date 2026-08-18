\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_other_person uuid;
    v_campaign uuid;
    v_recipient uuid;
    v_communication uuid;
    v_task uuid;
    v_locked_at timestamptz;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'canonical school missing'; END IF;

    INSERT INTO person(preferred_name) VALUES ('Communication Identity Person') RETURNING person_id INTO v_person;
    INSERT INTO person(preferred_name) VALUES ('Communication Identity Other') RETURNING person_id INTO v_other_person;

    INSERT INTO club_communication(school_id,communication_type,subject,primary_person_id)
    VALUES (v_school,'service','Original subject',v_person)
    RETURNING communication_id INTO v_communication;

    -- Member/context identity is not runtime-editable; DB owner test demonstrates the
    -- column-level runtime contract below. Closed status requires a timestamp.
    BEGIN
        UPDATE club_communication SET status='closed' WHERE communication_id=v_communication;
        RAISE EXCEPTION 'closed communication without timestamp unexpectedly accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='closed communication without timestamp unexpectedly accepted' THEN RAISE; END IF;
    END;
    UPDATE club_communication
       SET subject='Edited subject', status='closed', closed_at=now()
     WHERE communication_id=v_communication;

    INSERT INTO communication_campaign(
        school_id,name,communication_type,audience_definition,template_payload,status,created_by_person_id
    ) VALUES (
        v_school,'Draft campaign','service','{"group":"A"}'::jsonb,
        '{"body":"v1"}'::jsonb,'draft',v_person
    ) RETURNING campaign_id INTO v_campaign;

    UPDATE communication_campaign
       SET template_payload='{"body":"v2"}'::jsonb,
           audience_definition='{"group":"B"}'::jsonb,
           status='scheduled',
           scheduled_at=now()+interval '1 day'
     WHERE campaign_id=v_campaign;

    SELECT content_locked_at INTO v_locked_at
      FROM communication_campaign WHERE campaign_id=v_campaign;
    IF v_locked_at IS NULL THEN
        RAISE EXCEPTION 'campaign content was not locked on leaving draft';
    END IF;

    -- Returning the status to draft does not unlock historical campaign content.
    UPDATE communication_campaign SET status='draft' WHERE campaign_id=v_campaign;
    BEGIN
        UPDATE communication_campaign
           SET template_payload='{"body":"rewritten"}'::jsonb
         WHERE campaign_id=v_campaign;
        RAISE EXCEPTION 'locked campaign content unexpectedly rewritten';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='locked campaign content unexpectedly rewritten' THEN RAISE; END IF;
    END;

    INSERT INTO campaign_recipient(campaign_id,person_id,status,selection_reason)
    VALUES (v_campaign,v_person,'selected','{"rule":"test"}'::jsonb)
    RETURNING campaign_recipient_id INTO v_recipient;
    UPDATE campaign_recipient SET communication_id=v_communication,status='queued'
     WHERE campaign_recipient_id=v_recipient;

    BEGIN
        UPDATE campaign_recipient SET person_id=v_other_person
         WHERE campaign_recipient_id=v_recipient;
        RAISE EXCEPTION 'campaign recipient person unexpectedly rewritten';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='campaign recipient person unexpectedly rewritten' THEN RAISE; END IF;
    END;

    BEGIN
        UPDATE campaign_recipient SET communication_id=NULL
         WHERE campaign_recipient_id=v_recipient;
        RAISE EXCEPTION 'campaign recipient communication link unexpectedly rewritten';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM='campaign recipient communication link unexpectedly rewritten' THEN RAISE; END IF;
    END;

    INSERT INTO admin_task(
        school_id,title,subject_person_id,task_type,created_by_person_id,priority
    ) VALUES (
        v_school,'Identity task',v_person,'service',v_person,'normal'
    ) RETURNING admin_task_id INTO v_task;

    -- Owner can mutate for test setup, but runtime column privileges below prohibit
    -- rewriting task subject/origin while allowing normal management fields.
    UPDATE admin_task SET title='Renamed task',assigned_to_person_id=v_other_person,priority='high'
     WHERE admin_task_id=v_task;
END $$;

DO $$
BEGIN
    IF has_table_privilege('bridge_school_app','club_communication','UPDATE')
       OR has_column_privilege('bridge_school_app','club_communication','primary_person_id','UPDATE')
       OR NOT has_column_privilege('bridge_school_app','club_communication','status','UPDATE')
       OR NOT has_column_privilege('bridge_school_app','club_communication','closed_at','UPDATE') THEN
        RAISE EXCEPTION 'communication update permissions outside contract';
    END IF;

    IF has_table_privilege('bridge_school_app','communication_campaign','UPDATE')
       OR has_column_privilege('bridge_school_app','communication_campaign','school_id','UPDATE')
       OR has_column_privilege('bridge_school_app','communication_campaign','communication_type','UPDATE')
       OR NOT has_column_privilege('bridge_school_app','communication_campaign','template_payload','UPDATE')
       OR NOT has_column_privilege('bridge_school_app','communication_campaign','status','UPDATE') THEN
        RAISE EXCEPTION 'campaign update permissions outside contract';
    END IF;

    IF has_table_privilege('bridge_school_app','campaign_recipient','UPDATE')
       OR has_column_privilege('bridge_school_app','campaign_recipient','person_id','UPDATE')
       OR NOT has_column_privilege('bridge_school_app','campaign_recipient','status','UPDATE')
       OR NOT has_column_privilege('bridge_school_app','campaign_recipient','communication_id','UPDATE') THEN
        RAISE EXCEPTION 'campaign recipient permissions outside contract';
    END IF;

    IF has_table_privilege('bridge_school_app','admin_task','UPDATE')
       OR has_column_privilege('bridge_school_app','admin_task','subject_person_id','UPDATE')
       OR has_column_privilege('bridge_school_app','admin_task','created_by_person_id','UPDATE')
       OR NOT has_column_privilege('bridge_school_app','admin_task','assigned_to_person_id','UPDATE')
       OR NOT has_column_privilege('bridge_school_app','admin_task','priority','UPDATE') THEN
        RAISE EXCEPTION 'admin task update permissions outside contract';
    END IF;

    IF has_function_privilege('bridge_school_app_principal','lock_communication_campaign_content()','EXECUTE')
       OR has_function_privilege('bridge_school_app_principal','validate_campaign_recipient_update()','EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute communication hardening helper directly';
    END IF;
END $$;

ROLLBACK;
