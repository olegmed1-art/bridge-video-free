\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Unified communication hub and administrative work tracking.
-- Channels are delivery adapters; the logical conversation/message history is shared.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS club_communication (
    communication_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    communication_type text NOT NULL,
    subject text,
    primary_person_id uuid REFERENCES person(person_id),
    related_entity_type text,
    related_entity_id uuid,
    status text NOT NULL DEFAULT 'open',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    CHECK (status IN ('open','closed','cancelled','archived')),
    CHECK (closed_at IS NULL OR closed_at >= created_at)
);
CREATE INDEX IF NOT EXISTS club_communication_person_idx
    ON club_communication(school_id, primary_person_id, created_at DESC)
    WHERE primary_person_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS club_message (
    message_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    communication_id uuid NOT NULL REFERENCES club_communication(communication_id),
    sender_person_id uuid REFERENCES person(person_id),
    recipient_person_id uuid REFERENCES person(person_id),
    author_actor_type text NOT NULL DEFAULT 'person',
    message_type text NOT NULL DEFAULT 'text',
    body_text text,
    body_structured jsonb NOT NULL DEFAULT '{}'::jsonb,
    visibility_class text NOT NULL DEFAULT 'private_to_person',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (author_actor_type IN ('person','ai','system','administrator','instructor')),
    CHECK (visibility_class IN ('public_club','member_visible','student_visible','instructor_only','admin_only','private_to_person','draft_internal')),
    CHECK (body_text IS NOT NULL OR body_structured <> '{}'::jsonb)
);
CREATE INDEX IF NOT EXISTS club_message_communication_idx
    ON club_message(communication_id, created_at);
CREATE INDEX IF NOT EXISTS club_message_recipient_idx
    ON club_message(school_id, recipient_person_id, created_at DESC)
    WHERE recipient_person_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS message_delivery (
    message_delivery_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    message_id uuid NOT NULL REFERENCES club_message(message_id),
    recipient_person_id uuid NOT NULL REFERENCES person(person_id),
    contact_method_id uuid REFERENCES contact_method(contact_method_id),
    channel text NOT NULL,
    provider_reference text,
    status text NOT NULL DEFAULT 'queued',
    queued_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    delivered_at timestamptz,
    read_at timestamptz,
    failed_at timestamptz,
    error_category text,
    attempt_no integer NOT NULL DEFAULT 1 CHECK (attempt_no > 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (channel IN ('email','telegram','whatsapp','web','sms','other')),
    CHECK (status IN ('queued','sending','sent','delivered','read','failed','cancelled'))
);
CREATE UNIQUE INDEX IF NOT EXISTS message_delivery_attempt_uk
    ON message_delivery(message_id, recipient_person_id, channel, attempt_no);
CREATE INDEX IF NOT EXISTS message_delivery_pending_idx
    ON message_delivery(status, queued_at)
    WHERE status IN ('queued','sending','failed');
CREATE INDEX IF NOT EXISTS message_delivery_person_idx
    ON message_delivery(school_id, recipient_person_id, created_at DESC);

CREATE TABLE IF NOT EXISTS communication_campaign (
    campaign_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    name text NOT NULL,
    communication_type text NOT NULL,
    audience_definition jsonb NOT NULL DEFAULT '{}'::jsonb,
    template_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'draft',
    scheduled_at timestamptz,
    created_by_person_id uuid REFERENCES person(person_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('draft','scheduled','running','completed','cancelled','archived'))
);
CREATE INDEX IF NOT EXISTS communication_campaign_status_idx
    ON communication_campaign(school_id, status, scheduled_at NULLS FIRST, created_at DESC);

CREATE TABLE IF NOT EXISTS campaign_recipient (
    campaign_recipient_id uuid PRIMARY KEY DEFAULT uuidv7(),
    campaign_id uuid NOT NULL REFERENCES communication_campaign(campaign_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    communication_id uuid REFERENCES club_communication(communication_id),
    status text NOT NULL DEFAULT 'selected',
    selection_reason jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('selected','suppressed','queued','sent','failed','cancelled')),
    UNIQUE (campaign_id, person_id)
);

CREATE TABLE IF NOT EXISTS admin_task (
    admin_task_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    title text NOT NULL,
    task_type text NOT NULL DEFAULT 'general',
    subject_person_id uuid REFERENCES person(person_id),
    related_entity_type text,
    related_entity_id uuid,
    assigned_to_person_id uuid REFERENCES person(person_id),
    priority text NOT NULL DEFAULT 'normal',
    due_at timestamptz,
    created_by_person_id uuid REFERENCES person(person_id),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (priority IN ('low','normal','high','urgent'))
);
CREATE INDEX IF NOT EXISTS admin_task_assignee_idx
    ON admin_task(school_id, assigned_to_person_id, due_at NULLS LAST, created_at DESC)
    WHERE assigned_to_person_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS admin_task_subject_idx
    ON admin_task(school_id, subject_person_id, created_at DESC)
    WHERE subject_person_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS admin_task_state_event (
    admin_task_state_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    admin_task_id uuid NOT NULL REFERENCES admin_task(admin_task_id),
    state text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_person_id uuid REFERENCES person(person_id),
    reason text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state IN ('open','in_progress','waiting','completed','cancelled','archived','invalid'))
);
CREATE INDEX IF NOT EXISTS admin_task_state_time_idx
    ON admin_task_state_event(admin_task_id, occurred_at DESC, created_at DESC);

CREATE OR REPLACE VIEW admin_task_current_state AS
SELECT DISTINCT ON (t.admin_task_id)
    t.admin_task_id,
    t.school_id,
    t.title,
    t.task_type,
    t.subject_person_id,
    t.related_entity_type,
    t.related_entity_id,
    t.assigned_to_person_id,
    t.priority,
    t.due_at,
    se.state,
    se.occurred_at AS state_occurred_at,
    t.created_at
FROM admin_task t
LEFT JOIN admin_task_state_event se ON se.admin_task_id=t.admin_task_id
ORDER BY t.admin_task_id, se.occurred_at DESC NULLS LAST, se.created_at DESC NULLS LAST;

CREATE OR REPLACE FUNCTION validate_communication_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_person uuid;
BEGIN
    IF TG_TABLE_NAME='club_communication' THEN
        IF NEW.primary_person_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.primary_person_id) THEN
            RAISE EXCEPTION 'communication person missing';
        END IF;
    ELSIF TG_TABLE_NAME='club_message' THEN
        SELECT school_id INTO v_school FROM club_communication WHERE communication_id=NEW.communication_id;
        IF v_school IS NULL OR v_school <> NEW.school_id THEN RAISE EXCEPTION 'message communication school mismatch'; END IF;
        IF NEW.sender_person_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.sender_person_id) THEN RAISE EXCEPTION 'message sender missing'; END IF;
        IF NEW.recipient_person_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.recipient_person_id) THEN RAISE EXCEPTION 'message recipient missing'; END IF;
    ELSIF TG_TABLE_NAME='message_delivery' THEN
        SELECT school_id, recipient_person_id INTO v_school, v_person FROM club_message WHERE message_id=NEW.message_id;
        IF v_school IS NULL OR v_school <> NEW.school_id THEN RAISE EXCEPTION 'delivery message school mismatch'; END IF;
        IF v_person IS NOT NULL AND v_person <> NEW.recipient_person_id THEN RAISE EXCEPTION 'delivery recipient differs from message recipient'; END IF;
        IF NEW.contact_method_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM contact_method cm WHERE cm.contact_method_id=NEW.contact_method_id AND cm.school_id=NEW.school_id AND cm.person_id=NEW.recipient_person_id
        ) THEN RAISE EXCEPTION 'delivery contact scope mismatch'; END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_campaign_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
BEGIN
    IF TG_TABLE_NAME='campaign_recipient' THEN
        SELECT school_id INTO v_school FROM communication_campaign WHERE campaign_id=NEW.campaign_id;
        IF v_school IS NULL THEN RAISE EXCEPTION 'campaign recipient campaign missing'; END IF;
        IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN RAISE EXCEPTION 'campaign recipient person missing'; END IF;
        IF NEW.communication_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM club_communication c WHERE c.communication_id=NEW.communication_id AND c.school_id=v_school
        ) THEN RAISE EXCEPTION 'campaign recipient communication school mismatch'; END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_admin_task_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.subject_person_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.subject_person_id) THEN RAISE EXCEPTION 'admin task subject missing'; END IF;
    IF NEW.assigned_to_person_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.assigned_to_person_id) THEN RAISE EXCEPTION 'admin task assignee missing'; END IF;
    IF NEW.created_by_person_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.created_by_person_id) THEN RAISE EXCEPTION 'admin task creator missing'; END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_communication_scope_guard ON club_communication;
CREATE TRIGGER club_communication_scope_guard BEFORE INSERT OR UPDATE OF school_id, primary_person_id ON club_communication FOR EACH ROW EXECUTE FUNCTION validate_communication_scope();
DROP TRIGGER IF EXISTS club_message_scope_guard ON club_message;
CREATE TRIGGER club_message_scope_guard BEFORE INSERT OR UPDATE OF school_id, communication_id, sender_person_id, recipient_person_id ON club_message FOR EACH ROW EXECUTE FUNCTION validate_communication_scope();
DROP TRIGGER IF EXISTS message_delivery_scope_guard ON message_delivery;
CREATE TRIGGER message_delivery_scope_guard BEFORE INSERT OR UPDATE OF school_id, message_id, recipient_person_id, contact_method_id ON message_delivery FOR EACH ROW EXECUTE FUNCTION validate_communication_scope();
DROP TRIGGER IF EXISTS campaign_recipient_scope_guard ON campaign_recipient;
CREATE TRIGGER campaign_recipient_scope_guard BEFORE INSERT OR UPDATE OF campaign_id, person_id, communication_id ON campaign_recipient FOR EACH ROW EXECUTE FUNCTION validate_campaign_scope();
DROP TRIGGER IF EXISTS admin_task_scope_guard ON admin_task;
CREATE TRIGGER admin_task_scope_guard BEFORE INSERT OR UPDATE OF subject_person_id, assigned_to_person_id, created_by_person_id ON admin_task FOR EACH ROW EXECUTE FUNCTION validate_admin_task_scope();

GRANT INSERT, UPDATE ON TABLE
    club_communication,
    communication_campaign,
    campaign_recipient,
    admin_task
TO bridge_school_app;

GRANT INSERT ON TABLE
    club_message,
    admin_task_state_event
TO bridge_school_app;

GRANT INSERT, UPDATE ON TABLE message_delivery TO bridge_school_worker;

REVOKE UPDATE ON TABLE club_message, admin_task_state_event FROM bridge_school_app, bridge_school_worker;

REVOKE DELETE ON TABLE
    club_communication,
    club_message,
    message_delivery,
    communication_campaign,
    campaign_recipient,
    admin_task,
    admin_task_state_event
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;

GRANT SELECT ON admin_task_current_state TO bridge_school_reader;

REVOKE ALL ON FUNCTION validate_communication_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_campaign_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_admin_task_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0022_club_communications_admin')
ON CONFLICT DO NOTHING;

COMMIT;
