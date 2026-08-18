\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Fail-closed member-facing projections.
-- The member runtime role intentionally has no direct SELECT on the underlying school
-- tables. Every projection is scoped by transaction-local verified actor context.
-- Missing/invalid actor context therefore returns zero rows rather than broad data.
-- -----------------------------------------------------------------------------

CREATE VIEW member_self_profile
WITH (security_barrier=true) AS
SELECT
    p.person_id,
    p.preferred_name,
    p.locale,
    p.timezone,
    p.status,
    p.created_at
FROM person p
WHERE p.person_id=bridge_current_actor_person_id();

CREATE VIEW member_self_membership
WITH (security_barrier=true) AS
SELECT
    m.club_membership_id,
    m.school_id,
    m.person_id,
    m.membership_type,
    m.valid_from,
    m.valid_to,
    m.status,
    m.created_at
FROM club_membership m
WHERE m.school_id=bridge_current_actor_school_id()
  AND m.person_id=bridge_current_actor_person_id();

CREATE VIEW member_self_contact_method
WITH (security_barrier=true) AS
SELECT
    cm.contact_method_id,
    cm.school_id,
    cm.person_id,
    cm.channel,
    cm.normalized_value,
    cm.display_value,
    cm.verification_status,
    cm.preferred_flag,
    cm.valid_from,
    cm.valid_to,
    cm.status,
    cm.created_at
FROM contact_method cm
WHERE cm.school_id=bridge_current_actor_school_id()
  AND cm.person_id=bridge_current_actor_person_id();

CREATE VIEW member_self_contact_preference
WITH (security_barrier=true) AS
SELECT
    cp.contact_preference_id,
    cp.school_id,
    cp.person_id,
    cp.channel,
    cp.communication_type,
    cp.permission_state,
    cp.valid_from,
    cp.valid_to,
    cp.created_at
FROM contact_preference cp
WHERE cp.school_id=bridge_current_actor_school_id()
  AND cp.person_id=bridge_current_actor_person_id();

CREATE VIEW member_self_booking
WITH (security_barrier=true) AS
SELECT
    b.booking_id,
    b.school_id,
    b.person_id,
    b.club_event_id,
    e.event_type,
    e.title,
    e.service_id,
    e.starts_at,
    e.ends_at,
    e.status AS event_status,
    b.state AS booking_state,
    b.state_occurred_at,
    b.created_at
FROM club_booking_current_state b
JOIN club_event e ON e.club_event_id=b.club_event_id
WHERE b.school_id=bridge_current_actor_school_id()
  AND b.person_id=bridge_current_actor_person_id();

CREATE VIEW member_self_entitlement
WITH (security_barrier=true) AS
SELECT
    eb.entitlement_id,
    eb.school_id,
    eb.person_id,
    eb.service_id,
    s.name AS service_name,
    s.service_type,
    eb.quantity_granted,
    eb.quantity_used_net,
    eb.quantity_remaining,
    eb.valid_from,
    eb.valid_to,
    eb.status
FROM person_entitlement_balance eb
JOIN club_service s ON s.service_id=eb.service_id
WHERE eb.school_id=bridge_current_actor_school_id()
  AND eb.person_id=bridge_current_actor_person_id();

CREATE VIEW member_self_financial_balance
WITH (security_barrier=true) AS
SELECT
    fb.school_id,
    fb.person_id,
    fb.currency_code,
    fb.charges,
    fb.payments,
    fb.adjustments,
    fb.balance_due
FROM person_financial_balance fb
WHERE fb.school_id=bridge_current_actor_school_id()
  AND fb.person_id=bridge_current_actor_person_id();

CREATE VIEW member_self_charge
WITH (security_barrier=true) AS
SELECT
    c.charge_id,
    c.school_id,
    c.person_id,
    c.service_id,
    c.booking_id,
    c.package_grant_id,
    c.amount,
    c.currency_code,
    c.charged_at,
    c.due_at,
    c.charge_type,
    c.created_at
FROM club_charge c
WHERE c.school_id=bridge_current_actor_school_id()
  AND c.person_id=bridge_current_actor_person_id();

CREATE VIEW member_self_payment
WITH (security_barrier=true) AS
SELECT
    p.payment_id,
    p.school_id,
    p.person_id,
    p.amount,
    p.currency_code,
    p.paid_at,
    p.payment_method,
    p.created_at
FROM club_payment p
WHERE p.school_id=bridge_current_actor_school_id()
  AND p.person_id=bridge_current_actor_person_id();

CREATE VIEW member_self_message
WITH (security_barrier=true) AS
SELECT
    m.message_id,
    m.school_id,
    m.communication_id,
    m.sender_person_id,
    m.recipient_person_id,
    m.author_actor_type,
    m.message_type,
    m.body_text,
    m.body_structured,
    m.visibility_class,
    m.created_at
FROM club_message m
WHERE m.school_id=bridge_current_actor_school_id()
  AND (
        (m.visibility_class='private_to_person'
         AND (m.sender_person_id=bridge_current_actor_person_id()
              OR m.recipient_person_id=bridge_current_actor_person_id()))
        OR m.visibility_class='public_club'
        OR (m.visibility_class='member_visible' AND bridge_actor_has_role('member'))
        OR (m.visibility_class='student_visible' AND bridge_actor_has_role('student'))
      );

CREATE VIEW member_self_message_delivery
WITH (security_barrier=true) AS
SELECT
    d.message_delivery_id,
    d.school_id,
    d.message_id,
    d.recipient_person_id,
    d.channel,
    d.status,
    d.queued_at,
    d.sent_at,
    d.delivered_at,
    d.read_at,
    d.failed_at,
    d.error_category,
    d.attempt_no,
    d.created_at
FROM message_delivery d
WHERE d.school_id=bridge_current_actor_school_id()
  AND d.recipient_person_id=bridge_current_actor_person_id();

-- The member role receives only these projections, never broad reader inheritance.
REVOKE ALL ON TABLE
    person,
    student,
    club_membership,
    contact_method,
    contact_preference,
    person_entitlement,
    entitlement_usage,
    club_booking,
    club_booking_state_event,
    club_charge,
    club_payment,
    payment_allocation,
    financial_adjustment,
    accounting_document_reference,
    club_payment_refund,
    club_communication,
    club_message,
    message_delivery,
    admin_task,
    admin_task_state_event
FROM bridge_school_member;

GRANT SELECT ON
    member_self_profile,
    member_self_membership,
    member_self_contact_method,
    member_self_contact_preference,
    member_self_booking,
    member_self_entitlement,
    member_self_financial_balance,
    member_self_charge,
    member_self_payment,
    member_self_message,
    member_self_message_delivery
TO bridge_school_member;

-- Default privileges granted these views to the broad internal reader. That is harmless
-- but unnecessary; keep the member surface explicit and narrow.
REVOKE SELECT ON
    member_self_profile,
    member_self_membership,
    member_self_contact_method,
    member_self_contact_preference,
    member_self_booking,
    member_self_entitlement,
    member_self_financial_balance,
    member_self_charge,
    member_self_payment,
    member_self_message,
    member_self_message_delivery
FROM bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_finance, bridge_school_health;
GRANT SELECT ON
    member_self_profile,
    member_self_membership,
    member_self_contact_method,
    member_self_contact_preference,
    member_self_booking,
    member_self_entitlement,
    member_self_financial_balance,
    member_self_charge,
    member_self_payment,
    member_self_message,
    member_self_message_delivery
TO bridge_school_member;

INSERT INTO schema_migration(migration_key)
VALUES ('0039_member_self_service_views')
ON CONFLICT DO NOTHING;

COMMIT;
