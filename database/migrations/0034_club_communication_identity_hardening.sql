\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Communication/admin identity hardening.
-- Runtime may maintain operational presentation/state fields, but it must not rewrite
-- who a historical communication/campaign/task belongs to. Campaign content becomes
-- frozen once the campaign first leaves draft.
-- -----------------------------------------------------------------------------

-- Communication identity/context is immutable after creation.
REVOKE UPDATE ON TABLE club_communication FROM bridge_school_app, bridge_school_worker;
GRANT UPDATE (subject, status, metadata, closed_at)
    ON club_communication TO bridge_school_app;

ALTER TABLE club_communication
    ADD CONSTRAINT club_communication_closed_timestamp_ck
    CHECK (status <> 'closed' OR closed_at IS NOT NULL) NOT VALID;
ALTER TABLE club_communication
    VALIDATE CONSTRAINT club_communication_closed_timestamp_ck;

-- Freeze campaign audience/template once it first leaves draft, even if status is later
-- moved back to draft. This is integrity behavior, not a workflow-transition policy.
ALTER TABLE communication_campaign
    ADD COLUMN IF NOT EXISTS content_locked_at timestamptz;

CREATE OR REPLACE FUNCTION lock_communication_campaign_content()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status='draft' THEN
            NEW.content_locked_at := NULL;
        ELSE
            NEW.content_locked_at := COALESCE(NEW.content_locked_at, now());
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.content_locked_at IS NOT NULL THEN
        IF NEW.name IS DISTINCT FROM OLD.name
           OR NEW.communication_type IS DISTINCT FROM OLD.communication_type
           OR NEW.audience_definition IS DISTINCT FROM OLD.audience_definition
           OR NEW.template_payload IS DISTINCT FROM OLD.template_payload
           OR NEW.created_by_person_id IS DISTINCT FROM OLD.created_by_person_id
           OR NEW.school_id IS DISTINCT FROM OLD.school_id THEN
            RAISE EXCEPTION 'campaign content/identity is frozen after leaving draft';
        END IF;
        NEW.content_locked_at := OLD.content_locked_at;
    ELSIF NEW.status <> 'draft' THEN
        NEW.content_locked_at := now();
    ELSE
        NEW.content_locked_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS communication_campaign_content_lock ON communication_campaign;
CREATE TRIGGER communication_campaign_content_lock
BEFORE INSERT OR UPDATE ON communication_campaign
FOR EACH ROW EXECUTE FUNCTION lock_communication_campaign_content();

REVOKE UPDATE ON TABLE communication_campaign FROM bridge_school_app, bridge_school_worker;
GRANT UPDATE (name, audience_definition, template_payload, status, scheduled_at)
    ON communication_campaign TO bridge_school_app;

-- Recipient identity is immutable. A communication link may be attached once after
-- audience selection, then remains stable; status is a mutable operational projection.
CREATE OR REPLACE FUNCTION validate_campaign_recipient_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.campaign_id IS DISTINCT FROM OLD.campaign_id
       OR NEW.person_id IS DISTINCT FROM OLD.person_id
       OR NEW.selection_reason IS DISTINCT FROM OLD.selection_reason THEN
        RAISE EXCEPTION 'campaign recipient identity/selection is immutable';
    END IF;
    IF OLD.communication_id IS NOT NULL
       AND NEW.communication_id IS DISTINCT FROM OLD.communication_id THEN
        RAISE EXCEPTION 'campaign recipient communication link is immutable once assigned';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS campaign_recipient_update_guard ON campaign_recipient;
CREATE TRIGGER campaign_recipient_update_guard
BEFORE UPDATE ON campaign_recipient
FOR EACH ROW EXECUTE FUNCTION validate_campaign_recipient_update();

REVOKE UPDATE ON TABLE campaign_recipient FROM bridge_school_app, bridge_school_worker;
GRANT UPDATE (status, communication_id) ON campaign_recipient TO bridge_school_app;

-- Admin task subject/origin identity is immutable; mutable task-management fields remain.
REVOKE UPDATE ON TABLE admin_task FROM bridge_school_app, bridge_school_worker;
GRANT UPDATE (title, assigned_to_person_id, priority, due_at, metadata)
    ON admin_task TO bridge_school_app;

REVOKE ALL ON FUNCTION lock_communication_campaign_content()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_campaign_recipient_update()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0034_club_communication_identity_hardening')
ON CONFLICT DO NOTHING;

COMMIT;
