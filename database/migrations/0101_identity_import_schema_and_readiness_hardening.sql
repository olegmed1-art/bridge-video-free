\set ON_ERROR_STOP on
BEGIN;

-- Final hardening for protected identity-import staging.
-- Isolate all raw/unverified import evidence in a dedicated owner-only schema and
-- make "ready" a meaningful, fail-closed state without adding any apply operation.

CREATE SCHEMA IF NOT EXISTS identity_staging;
REVOKE ALL ON SCHEMA identity_staging FROM PUBLIC;
REVOKE ALL ON SCHEMA identity_staging FROM bridge_school_reader,bridge_school_app,
    bridge_school_worker,bridge_school_health,bridge_school_finance,
    bridge_school_member,bridge_school_member_principal,bridge_school_auth_gateway;

ALTER TABLE public.identity_import_batch SET SCHEMA identity_staging;
ALTER TABLE public.identity_import_batch_state_event SET SCHEMA identity_staging;
ALTER TABLE public.identity_import_item SET SCHEMA identity_staging;
ALTER TABLE public.identity_import_item_state_event SET SCHEMA identity_staging;
ALTER TABLE public.identity_import_action SET SCHEMA identity_staging;

ALTER VIEW public.identity_import_batch_current_state SET SCHEMA identity_staging;
ALTER VIEW public.identity_import_item_current_state SET SCHEMA identity_staging;
ALTER VIEW public.identity_import_current_action SET SCHEMA identity_staging;
ALTER VIEW public.identity_import_batch_summary SET SCHEMA identity_staging;

ALTER FUNCTION public.compute_identity_import_item_hash() SET SCHEMA identity_staging;
ALTER FUNCTION public.validate_identity_import_batch_scope() SET SCHEMA identity_staging;
ALTER FUNCTION public.validate_identity_import_item_scope() SET SCHEMA identity_staging;
ALTER FUNCTION public.validate_identity_import_state_scope() SET SCHEMA identity_staging;
ALTER FUNCTION public.seed_identity_import_batch_state() SET SCHEMA identity_staging;
ALTER FUNCTION public.seed_identity_import_item_state() SET SCHEMA identity_staging;
ALTER FUNCTION public.validate_identity_import_action() SET SCHEMA identity_staging;
ALTER FUNCTION public.reject_identity_import_mutation() SET SCHEMA identity_staging;

ALTER FUNCTION identity_staging.compute_identity_import_item_hash() SET search_path=identity_staging,public;
ALTER FUNCTION identity_staging.validate_identity_import_batch_scope() SET search_path=identity_staging,public;
ALTER FUNCTION identity_staging.validate_identity_import_item_scope() SET search_path=identity_staging,public;
ALTER FUNCTION identity_staging.validate_identity_import_state_scope() SET search_path=identity_staging,public;
ALTER FUNCTION identity_staging.seed_identity_import_batch_state() SET search_path=identity_staging,public;
ALTER FUNCTION identity_staging.seed_identity_import_item_state() SET search_path=identity_staging,public;
ALTER FUNCTION identity_staging.validate_identity_import_action() SET search_path=identity_staging,public;
ALTER FUNCTION identity_staging.reject_identity_import_mutation() SET search_path=identity_staging,public;

-- One source identity may occur at most once in a batch. This prevents two staged
-- rows from independently trying to resolve the same external identity.
CREATE UNIQUE INDEX identity_import_batch_source_identity_uk
    ON identity_staging.identity_import_item(identity_import_batch_id,source_identity_id)
    WHERE source_identity_id IS NOT NULL;

CREATE OR REPLACE FUNCTION identity_staging.validate_identity_import_ready_state()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=identity_staging,public
AS $$
DECLARE
    v_action_type text;
    v_resolution uuid;
    v_resolution_status text;
    v_resolution_type text;
    v_item_count bigint;
    v_not_ready bigint;
BEGIN
    IF TG_TABLE_NAME='identity_import_item_state_event' AND NEW.state='ready' THEN
        SELECT action_type,entity_resolution_decision_id
          INTO v_action_type,v_resolution
          FROM identity_import_current_action
         WHERE identity_import_item_id=NEW.identity_import_item_id;

        IF v_action_type IS NULL
           OR v_action_type NOT IN ('link_existing_person','create_new_person') THEN
            RAISE EXCEPTION 'identity import item cannot become ready without a resolvable current action';
        END IF;

        IF v_action_type='link_existing_person' THEN
            SELECT decision_type,status
              INTO v_resolution_type,v_resolution_status
              FROM public.entity_resolution_decision
             WHERE resolution_id=v_resolution;
            IF v_resolution_type IS DISTINCT FROM 'link'
               OR v_resolution_status IS DISTINCT FROM 'active' THEN
                RAISE EXCEPTION 'identity import item link resolution is no longer active';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='identity_import_batch_state_event' AND NEW.state='ready' THEN
        SELECT count(*) INTO v_item_count
          FROM identity_import_item
         WHERE identity_import_batch_id=NEW.identity_import_batch_id;
        IF v_item_count=0 THEN
            RAISE EXCEPTION 'identity import batch cannot become ready while empty';
        END IF;

        SELECT count(*) INTO v_not_ready
          FROM identity_import_item i
          LEFT JOIN identity_import_item_current_state s USING(identity_import_item_id)
          LEFT JOIN identity_import_current_action a USING(identity_import_item_id)
          LEFT JOIN public.entity_resolution_decision erd
            ON erd.resolution_id=a.entity_resolution_decision_id
         WHERE i.identity_import_batch_id=NEW.identity_import_batch_id
           AND (
             s.state IS DISTINCT FROM 'ready'
             OR a.action_type IS NULL
             OR a.action_type NOT IN ('link_existing_person','create_new_person')
             OR (a.action_type='link_existing_person'
                 AND (erd.decision_type IS DISTINCT FROM 'link' OR erd.status IS DISTINCT FROM 'active'))
           );
        IF v_not_ready<>0 THEN
            RAISE EXCEPTION 'identity import batch contains items that are not safely ready';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER identity_import_item_ready_guard
BEFORE INSERT ON identity_staging.identity_import_item_state_event
FOR EACH ROW EXECUTE FUNCTION identity_staging.validate_identity_import_ready_state();
CREATE TRIGGER identity_import_batch_ready_guard
BEFORE INSERT ON identity_staging.identity_import_batch_state_event
FOR EACH ROW EXECUTE FUNCTION identity_staging.validate_identity_import_ready_state();

CREATE VIEW identity_staging.identity_import_item_future_apply_readiness AS
SELECT
    i.identity_import_item_id,
    i.identity_import_batch_id,
    s.state,
    a.action_type,
    a.target_person_id,
    a.entity_resolution_decision_id,
    CASE
      WHEN s.state<>'ready' THEN false
      WHEN a.action_type='create_new_person' THEN true
      WHEN a.action_type='link_existing_person'
       AND erd.decision_type='link' AND erd.status='active' THEN true
      ELSE false
    END AS eligible_for_future_apply
FROM identity_staging.identity_import_item i
LEFT JOIN identity_staging.identity_import_item_current_state s USING(identity_import_item_id)
LEFT JOIN identity_staging.identity_import_current_action a USING(identity_import_item_id)
LEFT JOIN public.entity_resolution_decision erd
  ON erd.resolution_id=a.entity_resolution_decision_id;

CREATE VIEW identity_staging.identity_import_batch_future_apply_readiness AS
SELECT
    b.identity_import_batch_id,
    bs.state,
    count(ir.identity_import_item_id)::bigint AS item_count,
    count(ir.identity_import_item_id) FILTER (WHERE ir.eligible_for_future_apply)::bigint AS eligible_item_count,
    (bs.state='ready'
      AND count(ir.identity_import_item_id)>0
      AND bool_and(ir.eligible_for_future_apply)) AS eligible_for_future_apply
FROM identity_staging.identity_import_batch b
LEFT JOIN identity_staging.identity_import_batch_current_state bs USING(identity_import_batch_id)
LEFT JOIN identity_staging.identity_import_item_future_apply_readiness ir USING(identity_import_batch_id)
GROUP BY b.identity_import_batch_id,bs.state;

REVOKE ALL ON TABLE identity_staging.identity_import_batch,
    identity_staging.identity_import_batch_state_event,
    identity_staging.identity_import_item,
    identity_staging.identity_import_item_state_event,
    identity_staging.identity_import_action,
    identity_staging.identity_import_batch_current_state,
    identity_staging.identity_import_item_current_state,
    identity_staging.identity_import_current_action,
    identity_staging.identity_import_batch_summary,
    identity_staging.identity_import_item_future_apply_readiness,
    identity_staging.identity_import_batch_future_apply_readiness
FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker,
    bridge_school_health,bridge_school_finance,bridge_school_member,
    bridge_school_member_principal,bridge_school_auth_gateway;

REVOKE ALL ON FUNCTION identity_staging.compute_identity_import_item_hash(),
    identity_staging.validate_identity_import_batch_scope(),
    identity_staging.validate_identity_import_item_scope(),
    identity_staging.validate_identity_import_state_scope(),
    identity_staging.seed_identity_import_batch_state(),
    identity_staging.seed_identity_import_item_state(),
    identity_staging.validate_identity_import_action(),
    identity_staging.reject_identity_import_mutation(),
    identity_staging.validate_identity_import_ready_state()
FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker,
    bridge_school_health,bridge_school_finance,bridge_school_member,
    bridge_school_member_principal,bridge_school_auth_gateway;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0101_identity_import_schema_and_readiness_hardening')
ON CONFLICT DO NOTHING;
COMMIT;
