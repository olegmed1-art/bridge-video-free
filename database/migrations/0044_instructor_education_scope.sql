\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Instructor educational access is explicit and object-scoped.
-- A school-wide instructor role alone is insufficient: the instructor must also hold
-- an active person_access_grant(permission_key='education.read') for the Student's
-- canonical Person. No finance, membership, contact, auth or admin data is exposed.
-- Scoped instructor roles (group/course/etc.) are deliberately not interpreted here;
-- they require a separate approved scope-aware authorization helper.
-- -----------------------------------------------------------------------------

CREATE VIEW instructor_authorized_student
WITH (security_barrier=true) AS
SELECT
    s.school_id,
    s.student_id,
    s.person_id,
    p.preferred_name,
    s.school_joined_at,
    s.current_status
FROM student s
JOIN person p ON p.person_id=s.person_id
WHERE s.school_id=bridge_current_actor_school_id()
  AND bridge_actor_has_role('instructor')
  AND bridge_actor_has_person_permission(s.person_id,'education.read');

CREATE VIEW instructor_student_skill_assessment
WITH (security_barrier=true) AS
SELECT
    sa.school_id,
    sa.student_id,
    ast.person_id,
    ast.preferred_name,
    sa.skill_assessment_id,
    sa.skill_id,
    sk.name AS skill_name,
    sa.assessed_at,
    sa.assessment_value,
    sa.scale_key,
    sa.authority_class,
    sa.assessment_purpose,
    sa.confidence_class,
    sa.confidence_value,
    sa.method_version,
    sa.status,
    sa.created_at
FROM skill_assessment sa
JOIN instructor_authorized_student ast
  ON ast.school_id=sa.school_id AND ast.student_id=sa.student_id
JOIN skill sk ON sk.skill_id=sa.skill_id AND sk.school_id=sa.school_id;

CREATE VIEW instructor_student_error_observation
WITH (security_barrier=true) AS
SELECT
    eo.school_id,
    eo.student_id,
    ast.person_id,
    ast.preferred_name,
    eo.error_observation_id,
    eo.decision_id,
    eo.exercise_attempt_id,
    eo.table_result_id,
    eo.skill_id,
    sk.name AS skill_name,
    eo.topic_id,
    tp.name AS topic_name,
    eo.error_type,
    eo.severity,
    eo.recurrence_group_key,
    eo.observed_at,
    eo.confidence_class,
    eo.confidence_value,
    eo.method_version,
    eo.status,
    eo.created_at
FROM error_observation eo
JOIN instructor_authorized_student ast
  ON ast.school_id=eo.school_id AND ast.student_id=eo.student_id
LEFT JOIN skill sk ON sk.skill_id=eo.skill_id AND sk.school_id=eo.school_id
LEFT JOIN topic tp ON tp.topic_id=eo.topic_id AND tp.school_id=eo.school_id;

CREATE VIEW instructor_student_success_observation
WITH (security_barrier=true) AS
SELECT
    so.school_id,
    so.student_id,
    ast.person_id,
    ast.preferred_name,
    so.success_observation_id,
    so.decision_id,
    so.exercise_attempt_id,
    so.table_result_id,
    so.skill_id,
    sk.name AS skill_name,
    so.topic_id,
    tp.name AS topic_name,
    so.success_type,
    so.independence_level,
    so.observed_at,
    so.confidence_class,
    so.confidence_value,
    so.method_version,
    so.status,
    so.created_at
FROM success_observation so
JOIN instructor_authorized_student ast
  ON ast.school_id=so.school_id AND ast.student_id=so.student_id
LEFT JOIN skill sk ON sk.skill_id=so.skill_id AND sk.school_id=so.school_id
LEFT JOIN topic tp ON tp.topic_id=so.topic_id AND tp.school_id=so.school_id;

CREATE VIEW instructor_student_recommendation
WITH (security_barrier=true) AS
SELECT
    r.school_id,
    r.student_id,
    ast.person_id,
    ast.preferred_name,
    r.recommendation_id,
    r.source_snapshot_id,
    r.recommendation_type,
    r.priority_class,
    r.priority_value,
    r.rationale,
    r.recommendation_payload,
    r.target_topic_id,
    tp.name AS target_topic_name,
    r.target_skill_id,
    sk.name AS target_skill_name,
    r.method_version,
    r.created_at
FROM recommendation r
JOIN instructor_authorized_student ast
  ON ast.school_id=r.school_id AND ast.student_id=r.student_id
LEFT JOIN topic tp ON tp.topic_id=r.target_topic_id AND tp.school_id=r.school_id
LEFT JOIN skill sk ON sk.skill_id=r.target_skill_id AND sk.school_id=r.school_id;

-- New views inherit broad internal-reader defaults from earlier migrations. Keep the
-- external/member authorization surface explicit and narrow instead.
REVOKE SELECT ON
    instructor_authorized_student,
    instructor_student_skill_assessment,
    instructor_student_error_observation,
    instructor_student_success_observation,
    instructor_student_recommendation
FROM bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_auth_gateway;

GRANT SELECT ON
    instructor_authorized_student,
    instructor_student_skill_assessment,
    instructor_student_error_observation,
    instructor_student_success_observation,
    instructor_student_recommendation
TO bridge_school_member;

INSERT INTO schema_migration(migration_key)
VALUES ('0044_instructor_education_scope')
ON CONFLICT DO NOTHING;

COMMIT;
