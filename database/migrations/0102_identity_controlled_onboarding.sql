\set ON_ERROR_STOP on
BEGIN;

-- Controlled onboarding policy:
-- a newly materialized school Person is atomically created as
--   1) an active Student,
--   2) an active standard ClubMembership,
--   3) a member/portal user through an active school-scoped `member` role.
-- These three dimensions can later be enabled/disabled independently without
-- deleting the Person or historical records. AuthIdentity remains a separately
-- verified external-login binding and is never fabricated by onboarding.

DO $$
DECLARE r record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_identity_admin') THEN
        CREATE ROLE bridge_school_identity_admin NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
    SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
      INTO r FROM pg_roles WHERE rolname='bridge_school_identity_admin';
    IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
        RAISE EXCEPTION 'identity admin capability has unsafe role attributes';
    END IF;
END $$;

COMMENT ON ROLE bridge_school_identity_admin IS
    'Dormant trusted capability for reviewed person onboarding, identity-import apply, and independent ecosystem access changes. NOLOGIN by default.';
REVOKE CREATE ON SCHEMA public FROM bridge_school_identity_admin;
GRANT USAGE ON SCHEMA public TO bridge_school_identity_admin;
GRANT USAGE ON SCHEMA identity_staging TO bridge_school_identity_admin;

-- Ordinary interactive/runtime code may edit an existing Person but may no longer
-- create a Person directly. New school people must use the controlled onboarding
-- boundary so Student + ClubMembership + portal membership are atomic.
REVOKE INSERT ON TABLE person FROM bridge_school_app,bridge_school_worker;

CREATE TABLE person_ecosystem_access_event (
    person_ecosystem_access_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    student_enabled boolean NOT NULL,
    membership_enabled boolean NOT NULL,
    portal_enabled boolean NOT NULL,
    reason text NOT NULL,
    actor_person_id uuid REFERENCES person(person_id),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    event_sequence bigint GENERATED ALWAYS AS IDENTITY
        (SEQUENCE NAME person_ecosystem_access_event_sequence_seq),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (btrim(reason) <> '')
);
CREATE UNIQUE INDEX person_ecosystem_access_event_sequence_uk
    ON person_ecosystem_access_event(school_id,person_id,event_sequence);
CREATE INDEX person_ecosystem_access_event_current_idx
    ON person_ecosystem_access_event(school_id,person_id,event_sequence DESC);

CREATE OR REPLACE FUNCTION reject_person_ecosystem_access_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'person ecosystem access history is append-only';
END;
$$;
CREATE TRIGGER person_ecosystem_access_event_immutable
BEFORE UPDATE OR DELETE ON person_ecosystem_access_event
FOR EACH ROW EXECUTE FUNCTION reject_person_ecosystem_access_event_mutation();

CREATE VIEW person_ecosystem_access_current AS
SELECT DISTINCT ON (e.school_id,e.person_id)
    e.school_id,e.person_id,e.student_enabled,e.membership_enabled,e.portal_enabled,
    e.reason,e.actor_person_id,e.provenance,e.occurred_at,e.event_sequence
FROM person_ecosystem_access_event e
ORDER BY e.school_id,e.person_id,e.event_sequence DESC;

-- Portal permission is represented by an active school-scoped `member` assignment.
-- AuthIdentity is only the separately verified credential binding used to authenticate
-- that already-authorized portal user.
CREATE OR REPLACE FUNCTION bridge_onboard_school_person(
    p_school_id uuid,
    p_preferred_name text,
    p_provenance jsonb DEFAULT '{}'::jsonb
)
RETURNS TABLE(
    person_id uuid,
    student_id uuid,
    club_membership_id uuid,
    member_role_assignment_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
DECLARE
    v_person_id uuid;
    v_student_id uuid;
    v_membership_id uuid;
    v_role_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    IF p_school_id IS NULL OR NOT EXISTS (SELECT 1 FROM public.school s WHERE s.school_id=p_school_id) THEN
        RAISE EXCEPTION 'controlled onboarding requires an existing school';
    END IF;
    IF p_preferred_name IS NULL OR btrim(p_preferred_name)='' THEN
        RAISE EXCEPTION 'controlled onboarding requires a non-empty preferred name';
    END IF;

    INSERT INTO public.person(preferred_name,status,created_at)
    VALUES (btrim(p_preferred_name),'active',v_now)
    RETURNING public.person.person_id INTO v_person_id;

    INSERT INTO public.student(school_id,person_id,school_joined_at,current_status,created_at)
    VALUES (p_school_id,v_person_id,v_now,'active',v_now)
    RETURNING public.student.student_id INTO v_student_id;

    INSERT INTO public.club_membership(
        school_id,person_id,membership_type,valid_from,status,provenance,created_at
    ) VALUES (
        p_school_id,v_person_id,'standard',v_now,'active',
        COALESCE(p_provenance,'{}'::jsonb) || jsonb_build_object('onboarding','automatic'),v_now
    ) RETURNING public.club_membership.club_membership_id INTO v_membership_id;

    INSERT INTO public.person_role_assignment(
        school_id,person_id,role_key,scope_type,scope_id,valid_from,status,provenance,created_at
    ) VALUES (
        p_school_id,v_person_id,'member','school',NULL,v_now,'active',
        COALESCE(p_provenance,'{}'::jsonb) || jsonb_build_object('onboarding','automatic','purpose','portal_access'),v_now
    ) RETURNING public.person_role_assignment.person_role_assignment_id INTO v_role_id;

    INSERT INTO public.person_ecosystem_access_event(
        school_id,person_id,student_enabled,membership_enabled,portal_enabled,
        reason,provenance,occurred_at,created_at
    ) VALUES (
        p_school_id,v_person_id,true,true,true,'automatic full ecosystem onboarding',
        COALESCE(p_provenance,'{}'::jsonb),v_now,v_now
    );

    RETURN QUERY SELECT v_person_id,v_student_id,v_membership_id,v_role_id;
END;
$$;

-- Independently narrow or restore the three default onboarding dimensions.
-- Disabling does not delete Person, history, learning, finance, or communication facts.
CREATE OR REPLACE FUNCTION bridge_set_person_ecosystem_access(
    p_school_id uuid,
    p_person_id uuid,
    p_student_enabled boolean,
    p_membership_enabled boolean,
    p_portal_enabled boolean,
    p_reason text,
    p_actor_person_id uuid DEFAULT NULL,
    p_provenance jsonb DEFAULT '{}'::jsonb
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
DECLARE
    v_now timestamptz := clock_timestamp();
BEGIN
    IF p_school_id IS NULL OR p_person_id IS NULL
       OR p_student_enabled IS NULL OR p_membership_enabled IS NULL OR p_portal_enabled IS NULL THEN
        RAISE EXCEPTION 'ecosystem access change requires school, person, and all three desired states';
    END IF;
    IF p_reason IS NULL OR btrim(p_reason)='' THEN
        RAISE EXCEPTION 'ecosystem access change requires a reason';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.person p WHERE p.person_id=p_person_id) THEN
        RAISE EXCEPTION 'ecosystem access person missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.school s WHERE s.school_id=p_school_id) THEN
        RAISE EXCEPTION 'ecosystem access school missing';
    END IF;

    IF p_student_enabled THEN
        INSERT INTO public.student(school_id,person_id,school_joined_at,current_status,created_at)
        VALUES (p_school_id,p_person_id,v_now,'active',v_now)
        ON CONFLICT (school_id,person_id) DO UPDATE SET current_status='active';
    ELSE
        UPDATE public.student SET current_status='inactive'
         WHERE school_id=p_school_id AND person_id=p_person_id;
    END IF;

    IF p_membership_enabled THEN
        UPDATE public.club_membership
           SET status='active'
         WHERE club_membership_id=(
             SELECT cm.club_membership_id
               FROM public.club_membership cm
              WHERE cm.school_id=p_school_id
                AND cm.person_id=p_person_id
                AND cm.membership_type='standard'
                AND cm.valid_to IS NULL
                AND cm.status IN ('pending','active','paused')
              ORDER BY cm.valid_from DESC,cm.created_at DESC
              LIMIT 1
         );
        IF NOT FOUND THEN
            INSERT INTO public.club_membership(
                school_id,person_id,membership_type,valid_from,status,provenance,created_at
            ) VALUES (
                p_school_id,p_person_id,'standard',v_now,'active',
                COALESCE(p_provenance,'{}'::jsonb) || jsonb_build_object('access_change','membership_enabled'),v_now
            );
        END IF;
    ELSE
        UPDATE public.club_membership
           SET status='paused'
         WHERE school_id=p_school_id
           AND person_id=p_person_id
           AND membership_type='standard'
           AND valid_to IS NULL
           AND status IN ('pending','active');
    END IF;

    IF p_portal_enabled THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.person_role_assignment ra
             WHERE ra.school_id=p_school_id
               AND ra.person_id=p_person_id
               AND ra.role_key='member'
               AND ra.scope_type='school'
               AND ra.scope_id IS NULL
               AND ra.status='active'
               AND ra.valid_from<=v_now
               AND (ra.valid_to IS NULL OR v_now<ra.valid_to)
        ) THEN
            INSERT INTO public.person_role_assignment(
                school_id,person_id,role_key,scope_type,scope_id,valid_from,status,provenance,created_at
            ) VALUES (
                p_school_id,p_person_id,'member','school',NULL,v_now,'active',
                COALESCE(p_provenance,'{}'::jsonb) || jsonb_build_object('access_change','portal_enabled'),v_now
            );
        END IF;
    ELSE
        UPDATE public.person_role_assignment
           SET status='revoked',
               valid_to=GREATEST(v_now,valid_from+interval '1 microsecond')
         WHERE school_id=p_school_id
           AND person_id=p_person_id
           AND role_key='member'
           AND scope_type='school'
           AND scope_id IS NULL
           AND status='active'
           AND valid_to IS NULL;
    END IF;

    INSERT INTO public.person_ecosystem_access_event(
        school_id,person_id,student_enabled,membership_enabled,portal_enabled,
        reason,actor_person_id,provenance,occurred_at,created_at
    ) VALUES (
        p_school_id,p_person_id,p_student_enabled,p_membership_enabled,p_portal_enabled,
        btrim(p_reason),p_actor_person_id,COALESCE(p_provenance,'{}'::jsonb),v_now,v_now
    );
END;
$$;

-- Personal-cabinet context now specifically requires active portal/member access.
-- Other roles (for example instructor) cannot accidentally preserve portal login after
-- portal access has been disabled.
CREATE OR REPLACE FUNCTION bridge_establish_verified_actor_context(
    p_auth_identity_id uuid,
    p_school_id uuid,
    p_request_id uuid
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
DECLARE
    v_person_id uuid;
BEGIN
    IF p_auth_identity_id IS NULL OR p_school_id IS NULL OR p_request_id IS NULL THEN
        RAISE EXCEPTION 'actor context requires auth identity, school and request id';
    END IF;

    SELECT ai.person_id INTO v_person_id
      FROM public.auth_identity ai
     WHERE ai.auth_identity_id=p_auth_identity_id
       AND ai.status='active'
       AND ai.valid_from<=now()
       AND (ai.valid_to IS NULL OR now()<ai.valid_to);
    IF v_person_id IS NULL THEN
        RAISE EXCEPTION 'active auth identity mapping missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.person_role_assignment ra
         WHERE ra.school_id=p_school_id
           AND ra.person_id=v_person_id
           AND ra.role_key='member'
           AND ra.scope_type='school'
           AND ra.scope_id IS NULL
           AND ra.status='active'
           AND ra.valid_from<=now()
           AND (ra.valid_to IS NULL OR now()<ra.valid_to)
    ) THEN
        RAISE EXCEPTION 'actor has no active portal/member access in requested school';
    END IF;

    PERFORM set_config('bridge.actor_auth_identity_id',p_auth_identity_id::text,true);
    PERFORM set_config('bridge.actor_person_id',v_person_id::text,true);
    PERFORM set_config('bridge.actor_school_id',p_school_id::text,true);
    PERFORM set_config('bridge.request_id',p_request_id::text,true);
    RETURN v_person_id;
END;
$$;
REVOKE ALL ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid) FROM bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid) TO bridge_school_auth_gateway;

-- Controlled apply is now a real, separate Evidence Gate. `applied` can only be
-- appended by the controlled apply function; ordinary staging review cannot claim it.
ALTER TABLE identity_staging.identity_import_item_state_event
    DROP CONSTRAINT IF EXISTS identity_import_item_state_event_state_check;
ALTER TABLE identity_staging.identity_import_item_state_event
    ADD CONSTRAINT identity_import_item_state_event_state_check
    CHECK (state IN ('staged','validated','needs_review','ready','applied','rejected','invalid'));
ALTER TABLE identity_staging.identity_import_batch_state_event
    DROP CONSTRAINT IF EXISTS identity_import_batch_state_event_state_check;
ALTER TABLE identity_staging.identity_import_batch_state_event
    ADD CONSTRAINT identity_import_batch_state_event_state_check
    CHECK (state IN ('staged','validated','ready','applied','rejected','invalid'));

CREATE OR REPLACE FUNCTION identity_staging.validate_identity_import_applied_state()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=identity_staging,public
AS $$
BEGIN
    IF NEW.state='applied'
       AND COALESCE(current_setting('bridge.identity_controlled_apply',true),'off')<>'on' THEN
        RAISE EXCEPTION 'identity import applied state requires controlled apply';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER identity_import_item_applied_guard
BEFORE INSERT ON identity_staging.identity_import_item_state_event
FOR EACH ROW EXECUTE FUNCTION identity_staging.validate_identity_import_applied_state();
CREATE TRIGGER identity_import_batch_applied_guard
BEFORE INSERT ON identity_staging.identity_import_batch_state_event
FOR EACH ROW EXECUTE FUNCTION identity_staging.validate_identity_import_applied_state();

CREATE TABLE identity_staging.identity_import_apply_run (
    identity_import_apply_run_id uuid PRIMARY KEY DEFAULT uuidv7(),
    identity_import_batch_id uuid NOT NULL UNIQUE
        REFERENCES identity_staging.identity_import_batch(identity_import_batch_id),
    item_count bigint NOT NULL CHECK (item_count>0),
    applied_by_db_role text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE identity_staging.identity_import_apply_receipt (
    identity_import_apply_receipt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    identity_import_apply_run_id uuid NOT NULL
        REFERENCES identity_staging.identity_import_apply_run(identity_import_apply_run_id),
    identity_import_item_id uuid NOT NULL UNIQUE
        REFERENCES identity_staging.identity_import_item(identity_import_item_id),
    identity_import_action_id uuid NOT NULL
        REFERENCES identity_staging.identity_import_action(identity_import_action_id),
    action_type text NOT NULL,
    raw_payload_sha256 text NOT NULL,
    person_id uuid NOT NULL REFERENCES public.person(person_id),
    student_id uuid REFERENCES public.student(student_id),
    club_membership_id uuid REFERENCES public.club_membership(club_membership_id),
    member_role_assignment_id uuid REFERENCES public.person_role_assignment(person_role_assignment_id),
    new_person_created boolean NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (action_type IN ('link_existing_person','create_new_person')),
    CHECK (raw_payload_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (
      (action_type='create_new_person' AND new_person_created
       AND student_id IS NOT NULL AND club_membership_id IS NOT NULL AND member_role_assignment_id IS NOT NULL)
      OR
      (action_type='link_existing_person' AND NOT new_person_created)
    )
);
CREATE INDEX identity_import_apply_receipt_run_idx
    ON identity_staging.identity_import_apply_receipt(identity_import_apply_run_id);

CREATE TRIGGER identity_import_apply_run_immutable
BEFORE UPDATE OR DELETE ON identity_staging.identity_import_apply_run
FOR EACH ROW EXECUTE FUNCTION identity_staging.reject_identity_import_mutation();
CREATE TRIGGER identity_import_apply_receipt_immutable
BEFORE UPDATE OR DELETE ON identity_staging.identity_import_apply_receipt
FOR EACH ROW EXECUTE FUNCTION identity_staging.reject_identity_import_mutation();

CREATE OR REPLACE FUNCTION identity_staging.apply_identity_import_batch(p_batch_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,identity_staging,public
AS $$
DECLARE
    v_existing_run uuid;
    v_school_id uuid;
    v_batch_state text;
    v_batch_ready boolean;
    v_item_count bigint;
    v_apply_run uuid;
    v_onboard record;
    v_preferred_name text;
    r record;
BEGIN
    IF p_batch_id IS NULL THEN
        RAISE EXCEPTION 'controlled identity apply requires a batch id';
    END IF;

    SELECT ar.identity_import_apply_run_id INTO v_existing_run
      FROM identity_staging.identity_import_apply_run ar
     WHERE ar.identity_import_batch_id=p_batch_id;
    IF v_existing_run IS NOT NULL THEN
        RETURN v_existing_run;
    END IF;

    SELECT b.school_id INTO v_school_id
      FROM identity_staging.identity_import_batch b
     WHERE b.identity_import_batch_id=p_batch_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'identity import batch missing'; END IF;

    -- Lock every staged item. Action insertion also locks its item, so this closes the
    -- race where a later review action could arrive during apply validation.
    PERFORM 1 FROM identity_staging.identity_import_item i
     WHERE i.identity_import_batch_id=p_batch_id
     ORDER BY i.identity_import_item_id
     FOR UPDATE;

    SELECT s.state INTO v_batch_state
      FROM identity_staging.identity_import_batch_current_state s
     WHERE s.identity_import_batch_id=p_batch_id;
    SELECT br.eligible_for_future_apply,br.item_count
      INTO v_batch_ready,v_item_count
      FROM identity_staging.identity_import_batch_future_apply_readiness br
     WHERE br.identity_import_batch_id=p_batch_id;
    IF v_batch_state IS DISTINCT FROM 'ready'
       OR v_batch_ready IS DISTINCT FROM true
       OR v_item_count IS NULL OR v_item_count=0 THEN
        RAISE EXCEPTION 'identity import batch is not safely ready for controlled apply';
    END IF;

    INSERT INTO identity_staging.identity_import_apply_run(
        identity_import_batch_id,item_count,applied_by_db_role,metadata
    ) VALUES (
        p_batch_id,v_item_count,current_user,jsonb_build_object('policy','automatic_student_membership_portal')
    ) RETURNING identity_import_apply_run_id INTO v_apply_run;

    FOR r IN
        SELECT i.identity_import_item_id,i.raw_payload_sha256,i.normalized_candidate,
               a.identity_import_action_id,a.action_type,a.target_person_id
          FROM identity_staging.identity_import_item i
          JOIN identity_staging.identity_import_item_current_state s USING(identity_import_item_id)
          JOIN identity_staging.identity_import_current_action a USING(identity_import_item_id)
         WHERE i.identity_import_batch_id=p_batch_id
           AND s.state='ready'
         ORDER BY i.identity_import_item_id
    LOOP
        IF r.action_type='create_new_person' THEN
            v_preferred_name := NULLIF(btrim(r.normalized_candidate->>'preferred_name'),'');
            IF v_preferred_name IS NULL THEN
                RAISE EXCEPTION 'create-new item requires normalized_candidate.preferred_name';
            END IF;

            SELECT * INTO v_onboard
              FROM public.bridge_onboard_school_person(
                  v_school_id,
                  v_preferred_name,
                  jsonb_build_object(
                      'source','identity_import',
                      'batch_id',p_batch_id,
                      'item_id',r.identity_import_item_id,
                      'action_id',r.identity_import_action_id,
                      'apply_run_id',v_apply_run
                  )
              );

            INSERT INTO identity_staging.identity_import_apply_receipt(
                identity_import_apply_run_id,identity_import_item_id,identity_import_action_id,
                action_type,raw_payload_sha256,person_id,student_id,club_membership_id,
                member_role_assignment_id,new_person_created
            ) VALUES (
                v_apply_run,r.identity_import_item_id,r.identity_import_action_id,
                r.action_type,r.raw_payload_sha256,v_onboard.person_id,v_onboard.student_id,
                v_onboard.club_membership_id,v_onboard.member_role_assignment_id,true
            );
        ELSIF r.action_type='link_existing_person' THEN
            IF r.target_person_id IS NULL THEN
                RAISE EXCEPTION 'link-existing item is missing target person';
            END IF;
            INSERT INTO identity_staging.identity_import_apply_receipt(
                identity_import_apply_run_id,identity_import_item_id,identity_import_action_id,
                action_type,raw_payload_sha256,person_id,new_person_created
            ) VALUES (
                v_apply_run,r.identity_import_item_id,r.identity_import_action_id,
                r.action_type,r.raw_payload_sha256,r.target_person_id,false
            );
        ELSE
            RAISE EXCEPTION 'unsupported identity import action reached controlled apply: %',r.action_type;
        END IF;
    END LOOP;

    IF (SELECT count(*) FROM identity_staging.identity_import_apply_receipt rr
         WHERE rr.identity_import_apply_run_id=v_apply_run)<>v_item_count THEN
        RAISE EXCEPTION 'controlled identity apply receipt count mismatch';
    END IF;

    PERFORM set_config('bridge.identity_controlled_apply','on',true);
    INSERT INTO identity_staging.identity_import_item_state_event(
        identity_import_item_id,state,reason,metadata
    )
    SELECT i.identity_import_item_id,'applied','controlled identity import apply',
           jsonb_build_object('apply_run_id',v_apply_run)
      FROM identity_staging.identity_import_item i
     WHERE i.identity_import_batch_id=p_batch_id;
    INSERT INTO identity_staging.identity_import_batch_state_event(
        identity_import_batch_id,state,reason,metadata
    ) VALUES (
        p_batch_id,'applied','controlled identity import apply',jsonb_build_object('apply_run_id',v_apply_run)
    );
    PERFORM set_config('bridge.identity_controlled_apply','off',true);

    RETURN v_apply_run;
END;
$$;

CREATE VIEW identity_staging.identity_import_apply_summary AS
SELECT ar.identity_import_apply_run_id,ar.identity_import_batch_id,ar.item_count,
       ar.applied_by_db_role,ar.applied_at,
       count(rr.identity_import_apply_receipt_id)::bigint AS receipt_count,
       count(rr.identity_import_apply_receipt_id) FILTER (WHERE rr.new_person_created)::bigint AS new_person_count,
       count(rr.identity_import_apply_receipt_id) FILTER (WHERE NOT rr.new_person_created)::bigint AS linked_existing_count
FROM identity_staging.identity_import_apply_run ar
LEFT JOIN identity_staging.identity_import_apply_receipt rr USING(identity_import_apply_run_id)
GROUP BY ar.identity_import_apply_run_id,ar.identity_import_batch_id,ar.item_count,ar.applied_by_db_role,ar.applied_at;

REVOKE ALL ON TABLE person_ecosystem_access_event,person_ecosystem_access_current
FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_health,
     bridge_school_finance,bridge_school_member,bridge_school_member_principal,bridge_school_auth_gateway;
REVOKE ALL ON SEQUENCE person_ecosystem_access_event_sequence_seq
FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_health,
     bridge_school_finance,bridge_school_member,bridge_school_member_principal,bridge_school_auth_gateway;
REVOKE ALL ON FUNCTION reject_person_ecosystem_access_event_mutation(),
    bridge_onboard_school_person(uuid,text,jsonb),
    bridge_set_person_ecosystem_access(uuid,uuid,boolean,boolean,boolean,text,uuid,jsonb)
FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_health,
     bridge_school_finance,bridge_school_member,bridge_school_member_principal,bridge_school_auth_gateway;

REVOKE ALL ON TABLE identity_staging.identity_import_apply_run,
    identity_staging.identity_import_apply_receipt,
    identity_staging.identity_import_apply_summary
FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_health,
     bridge_school_finance,bridge_school_member,bridge_school_member_principal,bridge_school_auth_gateway;
REVOKE ALL ON FUNCTION identity_staging.validate_identity_import_applied_state(),
    identity_staging.apply_identity_import_batch(uuid)
FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_health,
     bridge_school_finance,bridge_school_member,bridge_school_member_principal,bridge_school_auth_gateway;

GRANT EXECUTE ON FUNCTION bridge_onboard_school_person(uuid,text,jsonb) TO bridge_school_identity_admin;
GRANT EXECUTE ON FUNCTION bridge_set_person_ecosystem_access(uuid,uuid,boolean,boolean,boolean,text,uuid,jsonb)
TO bridge_school_identity_admin;
GRANT EXECUTE ON FUNCTION identity_staging.apply_identity_import_batch(uuid) TO bridge_school_identity_admin;
GRANT SELECT ON TABLE person_ecosystem_access_current TO bridge_school_identity_admin;
GRANT SELECT ON TABLE identity_staging.identity_import_apply_run,
    identity_staging.identity_import_apply_receipt,
    identity_staging.identity_import_apply_summary
TO bridge_school_identity_admin;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0102_identity_controlled_onboarding')
ON CONFLICT DO NOTHING;
COMMIT;
