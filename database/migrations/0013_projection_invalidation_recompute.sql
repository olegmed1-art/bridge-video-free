\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Invalidation batches preserve why a dependency cascade happened. The existing
-- InvalidationRecord remains the per-entity fact; mutable recomputation state is only
-- changed through guarded queue functions below.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invalidation_batch (
    invalidation_batch_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    root_cause_entity_id uuid NOT NULL,
    reason text NOT NULL,
    initiated_by text,
    method_version text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    initiated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS invalidation_batch_school_time_idx
    ON invalidation_batch(school_id, initiated_at DESC);

ALTER TABLE invalidation_record
    ADD COLUMN IF NOT EXISTS invalidation_batch_id uuid,
    ADD COLUMN IF NOT EXISTS dependency_depth integer;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='invalidation_record_batch_fk') THEN
        ALTER TABLE invalidation_record
        ADD CONSTRAINT invalidation_record_batch_fk
        FOREIGN KEY (invalidation_batch_id) REFERENCES invalidation_batch(invalidation_batch_id) NOT VALID;
        ALTER TABLE invalidation_record VALIDATE CONSTRAINT invalidation_record_batch_fk;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='invalidation_record_depth_ck') THEN
        ALTER TABLE invalidation_record
        ADD CONSTRAINT invalidation_record_depth_ck
        CHECK (dependency_depth IS NULL OR dependency_depth >= 0);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS invalidation_record_batch_target_uk
    ON invalidation_record(invalidation_batch_id, target_entity_id)
    WHERE invalidation_batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS invalidation_record_target_time_idx
    ON invalidation_record(school_id, target_entity_id, invalidated_at DESC);

-- -----------------------------------------------------------------------------
-- Generic registration of materialized projection outputs. This is intentionally
-- separate from the output table: historical generations remain registered forever.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projection_output_entity (
    projection_output_entity_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    entity_id uuid NOT NULL,
    entity_type text NOT NULL,
    projection_run_id uuid NOT NULL REFERENCES projection_run(projection_run_id),
    projection_key text NOT NULL,
    generation_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, entity_id, entity_type)
);
CREATE INDEX IF NOT EXISTS projection_output_generation_idx
    ON projection_output_entity(school_id, projection_key, generation_id, entity_type);

CREATE OR REPLACE FUNCTION register_student_profile_projection_output()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_projection_key text;
    v_generation uuid;
BEGIN
    IF NEW.projection_run_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT school_id, projection_key, generation_id
      INTO v_school, v_projection_key, v_generation
      FROM projection_run
     WHERE projection_run_id=NEW.projection_run_id;

    IF v_school IS NULL OR v_school <> NEW.school_id OR v_generation <> NEW.generation_id THEN
        RAISE EXCEPTION 'student profile projection output registration mismatch';
    END IF;

    INSERT INTO projection_output_entity(
        school_id, entity_id, entity_type, projection_run_id, projection_key, generation_id
    ) VALUES (
        NEW.school_id, NEW.snapshot_id, 'student_profile_snapshot',
        NEW.projection_run_id, v_projection_key, NEW.generation_id
    )
    ON CONFLICT (school_id, entity_id, entity_type) DO NOTHING;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS student_profile_projection_output_registration ON student_profile_snapshot;
CREATE TRIGGER student_profile_projection_output_registration
AFTER INSERT ON student_profile_snapshot
FOR EACH ROW EXECUTE FUNCTION register_student_profile_projection_output();

-- -----------------------------------------------------------------------------
-- Automatically build the causal graph for the new profile/recommendation layer.
-- Classification edges (topic/skill) are kept as depends_on because changing a
-- definition/policy can legitimately require recomputation.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION register_learning_observation_dependencies()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_child uuid;
BEGIN
    IF TG_TABLE_NAME='error_observation' THEN
        v_child := (to_jsonb(NEW)->>'error_observation_id')::uuid;
    ELSE
        v_child := (to_jsonb(NEW)->>'success_observation_id')::uuid;
    END IF;

    IF NEW.decision_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
        VALUES (NEW.school_id, NEW.decision_id, v_child, 'derived_from', NEW.method_version)
        ON CONFLICT DO NOTHING;
    END IF;
    IF NEW.exercise_attempt_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
        VALUES (NEW.school_id, NEW.exercise_attempt_id, v_child, 'derived_from', NEW.method_version)
        ON CONFLICT DO NOTHING;
    END IF;
    IF NEW.table_result_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
        VALUES (NEW.school_id, NEW.table_result_id, v_child, 'derived_from', NEW.method_version)
        ON CONFLICT DO NOTHING;
    END IF;
    IF NEW.entity_resolution_decision_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
        VALUES (NEW.school_id, NEW.entity_resolution_decision_id, v_child, 'depends_on', NEW.method_version)
        ON CONFLICT DO NOTHING;
    END IF;
    IF NEW.tournament_identity_attribution_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
        VALUES (NEW.school_id, NEW.tournament_identity_attribution_id, v_child, 'depends_on', NEW.method_version)
        ON CONFLICT DO NOTHING;
    END IF;
    IF NEW.skill_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
        VALUES (NEW.school_id, NEW.skill_id, v_child, 'depends_on', NEW.method_version)
        ON CONFLICT DO NOTHING;
    END IF;
    IF NEW.topic_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
        VALUES (NEW.school_id, NEW.topic_id, v_child, 'depends_on', NEW.method_version)
        ON CONFLICT DO NOTHING;
    END IF;
    IF NEW.generated_by_analysis_run_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
        VALUES (NEW.school_id, NEW.generated_by_analysis_run_id, v_child, 'derived_from', NEW.method_version)
        ON CONFLICT DO NOTHING;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS error_observation_dependency_registration ON error_observation;
CREATE TRIGGER error_observation_dependency_registration
AFTER INSERT ON error_observation
FOR EACH ROW EXECUTE FUNCTION register_learning_observation_dependencies();
DROP TRIGGER IF EXISTS success_observation_dependency_registration ON success_observation;
CREATE TRIGGER success_observation_dependency_registration
AFTER INSERT ON success_observation
FOR EACH ROW EXECUTE FUNCTION register_learning_observation_dependencies();

CREATE OR REPLACE FUNCTION register_student_profile_input_dependencies()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_input uuid;
BEGIN
    v_input := COALESCE(
        NEW.skill_assessment_id,
        NEW.metric_observation_id,
        NEW.error_observation_id,
        NEW.success_observation_id,
        NEW.decision_assessment_id,
        NEW.exercise_attempt_assessment_id,
        NEW.table_result_id,
        NEW.source_observation_id
    );

    INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type)
    SELECT s.school_id, v_input, NEW.snapshot_id, 'depends_on'
      FROM student_profile_snapshot s
     WHERE s.snapshot_id=NEW.snapshot_id
    ON CONFLICT DO NOTHING;

    IF NEW.entity_resolution_decision_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type)
        SELECT s.school_id, NEW.entity_resolution_decision_id, NEW.snapshot_id, 'depends_on'
          FROM student_profile_snapshot s
         WHERE s.snapshot_id=NEW.snapshot_id
        ON CONFLICT DO NOTHING;
    END IF;
    IF NEW.tournament_identity_attribution_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type)
        SELECT s.school_id, NEW.tournament_identity_attribution_id, NEW.snapshot_id, 'depends_on'
          FROM student_profile_snapshot s
         WHERE s.snapshot_id=NEW.snapshot_id
        ON CONFLICT DO NOTHING;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS student_profile_input_dependency_registration ON student_profile_input;
CREATE TRIGGER student_profile_input_dependency_registration
AFTER INSERT ON student_profile_input
FOR EACH ROW EXECUTE FUNCTION register_student_profile_input_dependencies();

CREATE OR REPLACE FUNCTION register_recommendation_dependencies()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
    VALUES (NEW.school_id, NEW.source_snapshot_id, NEW.recommendation_id, 'derived_from', NEW.method_version)
    ON CONFLICT DO NOTHING;

    IF NEW.projection_run_id IS NOT NULL THEN
        INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type, method_version)
        VALUES (NEW.school_id, NEW.projection_run_id, NEW.recommendation_id, 'derived_from', NEW.method_version)
        ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS recommendation_dependency_registration ON recommendation;
CREATE TRIGGER recommendation_dependency_registration
AFTER INSERT ON recommendation
FOR EACH ROW EXECUTE FUNCTION register_recommendation_dependencies();

CREATE OR REPLACE FUNCTION register_session_plan_recommendation_dependency()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
BEGIN
    SELECT s.school_id INTO v_school
      FROM session_plan sp
      JOIN session s ON s.session_id=sp.session_id
     WHERE sp.session_plan_id=NEW.session_plan_id;

    INSERT INTO dependency_edge(school_id, parent_entity_id, child_entity_id, dependency_type)
    VALUES (v_school, NEW.recommendation_id, NEW.session_plan_id, 'depends_on')
    ON CONFLICT DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS session_plan_recommendation_dependency_registration ON session_plan_recommendation;
CREATE TRIGGER session_plan_recommendation_dependency_registration
AFTER INSERT ON session_plan_recommendation
FOR EACH ROW EXECUTE FUNCTION register_session_plan_recommendation_dependency();

-- -----------------------------------------------------------------------------
-- Durable recomputation queue. Queue rows are operational/mutable, while every state
-- transition and every causal invalidation link is retained append-only.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projection_recompute_request (
    projection_recompute_request_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    projection_key text NOT NULL,
    scope_key text NOT NULL DEFAULT 'default',
    priority integer NOT NULL DEFAULT 100,
    status text NOT NULL DEFAULT 'pending',
    requested_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    claimed_by text,
    completed_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    result_projection_run_id uuid REFERENCES projection_run(projection_run_id),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
    CHECK (completed_at IS NULL OR completed_at >= requested_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS projection_recompute_one_active_scope_uk
    ON projection_recompute_request(school_id, projection_key, scope_key)
    WHERE status IN ('pending','running');
CREATE INDEX IF NOT EXISTS projection_recompute_queue_idx
    ON projection_recompute_request(status, priority DESC, requested_at, created_at);

CREATE TABLE IF NOT EXISTS projection_recompute_cause (
    projection_recompute_request_id uuid NOT NULL REFERENCES projection_recompute_request(projection_recompute_request_id),
    invalidation_id uuid NOT NULL REFERENCES invalidation_record(invalidation_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (projection_recompute_request_id, invalidation_id)
);
CREATE INDEX IF NOT EXISTS projection_recompute_cause_invalidation_idx
    ON projection_recompute_cause(invalidation_id, projection_recompute_request_id);

CREATE TABLE IF NOT EXISTS projection_recompute_state_event (
    projection_recompute_state_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    projection_recompute_request_id uuid NOT NULL REFERENCES projection_recompute_request(projection_recompute_request_id),
    state_type text NOT NULL,
    worker_key text,
    projection_run_id uuid REFERENCES projection_run(projection_run_id),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state_type IN ('created','claimed','succeeded','failed','retried','cancelled'))
);
CREATE INDEX IF NOT EXISTS projection_recompute_state_history_idx
    ON projection_recompute_state_event(projection_recompute_request_id, occurred_at, projection_recompute_state_event_id);

-- -----------------------------------------------------------------------------
-- Traverse the dependency DAG, preserve all invalidated targets, mark derived profile
-- products stale/invalidated, and enqueue current projection scopes exactly once.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION invalidate_dependency_subgraph(
    p_school_id uuid,
    p_cause_entity_id uuid,
    p_reason text,
    p_metadata jsonb DEFAULT '{}'::jsonb,
    p_initiated_by text DEFAULT 'worker'
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_batch uuid;
    r record;
    v_request uuid;
BEGIN
    IF p_school_id IS NULL OR NOT EXISTS (SELECT 1 FROM school WHERE school_id=p_school_id) THEN
        RAISE EXCEPTION 'invalidation school missing';
    END IF;
    IF p_cause_entity_id IS NULL OR NULLIF(btrim(p_reason),'') IS NULL THEN
        RAISE EXCEPTION 'invalidation cause and reason are required';
    END IF;

    INSERT INTO invalidation_batch(
        school_id, root_cause_entity_id, reason, initiated_by, metadata
    ) VALUES (
        p_school_id, p_cause_entity_id, p_reason, p_initiated_by, COALESCE(p_metadata,'{}'::jsonb)
    ) RETURNING invalidation_batch_id INTO v_batch;

    WITH RECURSIVE affected(target_entity_id, dependency_depth, path) AS (
        SELECT d.child_entity_id, 1, ARRAY[p_cause_entity_id, d.child_entity_id]::uuid[]
          FROM dependency_edge d
         WHERE d.school_id=p_school_id
           AND d.parent_entity_id=p_cause_entity_id
           AND d.dependency_type IN ('derived_from','depends_on')
        UNION ALL
        SELECT d.child_entity_id, a.dependency_depth+1, a.path || d.child_entity_id
          FROM affected a
          JOIN dependency_edge d
            ON d.school_id=p_school_id
           AND d.parent_entity_id=a.target_entity_id
           AND d.dependency_type IN ('derived_from','depends_on')
         WHERE a.dependency_depth < 128
           AND NOT d.child_entity_id = ANY(a.path)
    ), minimal AS (
        SELECT target_entity_id, MIN(dependency_depth) AS dependency_depth
          FROM affected
         GROUP BY target_entity_id
    )
    INSERT INTO invalidation_record(
        school_id, target_entity_id, cause_entity_id, reason, scope,
        invalidated_at, recomputation_status, invalidation_batch_id, dependency_depth
    )
    SELECT p_school_id, target_entity_id, p_cause_entity_id, p_reason,
           COALESCE(p_metadata,'{}'::jsonb), now(), 'pending', v_batch, dependency_depth
      FROM minimal
    ON CONFLICT (invalidation_batch_id, target_entity_id)
    WHERE invalidation_batch_id IS NOT NULL
    DO NOTHING;

    -- Preserve staleness/invalidity as append-only state history on known derived objects.
    INSERT INTO student_profile_snapshot_state_event(
        snapshot_id, state_type, effective_at, stale_from, cause_entity_id, reason
    )
    SELECT s.snapshot_id, 'stale', now(), now(), p_cause_entity_id, p_reason
      FROM invalidation_record ir
      JOIN student_profile_snapshot s ON s.snapshot_id=ir.target_entity_id
     WHERE ir.invalidation_batch_id=v_batch;

    INSERT INTO recommendation_state_event(recommendation_id, state_type, reason, occurred_at)
    SELECT r.recommendation_id, 'invalidated', p_reason, now()
      FROM invalidation_record ir
      JOIN recommendation r ON r.recommendation_id=ir.target_entity_id
     WHERE ir.invalidation_batch_id=v_batch;

    -- Current generations only are scheduled for recomputation. Historical generations
    -- stay invalidated in history but do not consume worker capacity.
    FOR r IN
        SELECT DISTINCT ir.invalidation_id, pgc.school_id, pgc.projection_key, pgc.scope_key
          FROM invalidation_record ir
          JOIN projection_output_entity poe
            ON poe.school_id=ir.school_id
           AND poe.entity_id=ir.target_entity_id
          JOIN projection_generation_current pgc
            ON pgc.school_id=poe.school_id
           AND pgc.projection_key=poe.projection_key
           AND pgc.generation_id=poe.generation_id
         WHERE ir.invalidation_batch_id=v_batch
    LOOP
        SELECT projection_recompute_request_id
          INTO v_request
          FROM projection_recompute_request
         WHERE school_id=r.school_id
           AND projection_key=r.projection_key
           AND scope_key=r.scope_key
           AND status IN ('pending','running')
         ORDER BY requested_at, created_at
         LIMIT 1
         FOR UPDATE;

        IF v_request IS NULL THEN
            INSERT INTO projection_recompute_request(
                school_id, projection_key, scope_key, priority, status
            ) VALUES (
                r.school_id, r.projection_key, r.scope_key, 100, 'pending'
            ) RETURNING projection_recompute_request_id INTO v_request;

            INSERT INTO projection_recompute_state_event(
                projection_recompute_request_id, state_type, details
            ) VALUES (
                v_request, 'created', jsonb_build_object('invalidation_batch_id',v_batch)
            );
        END IF;

        INSERT INTO projection_recompute_cause(projection_recompute_request_id, invalidation_id)
        VALUES (v_request, r.invalidation_id)
        ON CONFLICT DO NOTHING;
    END LOOP;

    RETURN v_batch;
END;
$$;

CREATE OR REPLACE FUNCTION claim_projection_recompute(p_worker_key text)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_request uuid;
BEGIN
    IF NULLIF(btrim(p_worker_key),'') IS NULL THEN
        RAISE EXCEPTION 'worker key is required';
    END IF;

    SELECT projection_recompute_request_id
      INTO v_request
      FROM projection_recompute_request
     WHERE status='pending'
     ORDER BY priority DESC, requested_at, created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1;

    IF v_request IS NULL THEN
        RETURN NULL;
    END IF;

    UPDATE projection_recompute_request
       SET status='running', claimed_at=now(), claimed_by=p_worker_key,
           attempt_count=attempt_count+1, last_error=NULL
     WHERE projection_recompute_request_id=v_request;

    INSERT INTO projection_recompute_state_event(
        projection_recompute_request_id, state_type, worker_key
    ) VALUES (v_request, 'claimed', p_worker_key);

    RETURN v_request;
END;
$$;

CREATE OR REPLACE FUNCTION complete_projection_recompute(
    p_request_id uuid,
    p_projection_run_id uuid
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_request projection_recompute_request%ROWTYPE;
    v_run_school uuid;
    v_run_key text;
    v_generation uuid;
    v_run_status text;
    v_completed timestamptz;
BEGIN
    SELECT * INTO v_request
      FROM projection_recompute_request
     WHERE projection_recompute_request_id=p_request_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'recompute request missing'; END IF;
    IF v_request.status <> 'running' THEN RAISE EXCEPTION 'recompute request is not running'; END IF;

    SELECT school_id, projection_key, generation_id, status, completed_at
      INTO v_run_school, v_run_key, v_generation, v_run_status, v_completed
      FROM projection_run
     WHERE projection_run_id=p_projection_run_id;

    IF v_run_school IS NULL OR v_run_school <> v_request.school_id OR v_run_key <> v_request.projection_key THEN
        RAISE EXCEPTION 'projection run does not match recompute request';
    END IF;
    IF v_run_status <> 'success' OR v_completed IS NULL THEN
        RAISE EXCEPTION 'recompute projection run is not successfully completed';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM projection_generation_current pgc
         WHERE pgc.school_id=v_request.school_id
           AND pgc.projection_key=v_request.projection_key
           AND pgc.scope_key=v_request.scope_key
           AND pgc.generation_id=v_generation
           AND pgc.projection_run_id=p_projection_run_id
    ) THEN
        RAISE EXCEPTION 'recomputed generation is not the current activated generation';
    END IF;

    UPDATE projection_recompute_request
       SET status='succeeded', completed_at=now(), result_projection_run_id=p_projection_run_id,
           last_error=NULL
     WHERE projection_recompute_request_id=p_request_id;

    UPDATE invalidation_record ir
       SET recomputation_status='recomputed'
      FROM projection_recompute_cause c
     WHERE c.projection_recompute_request_id=p_request_id
       AND c.invalidation_id=ir.invalidation_id;

    INSERT INTO projection_recompute_state_event(
        projection_recompute_request_id, state_type, worker_key, projection_run_id
    ) VALUES (
        p_request_id, 'succeeded', v_request.claimed_by, p_projection_run_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION fail_projection_recompute(
    p_request_id uuid,
    p_error text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_worker text;
BEGIN
    IF NULLIF(btrim(p_error),'') IS NULL THEN RAISE EXCEPTION 'failure reason is required'; END IF;

    SELECT claimed_by INTO v_worker
      FROM projection_recompute_request
     WHERE projection_recompute_request_id=p_request_id
       AND status='running'
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'running recompute request missing'; END IF;

    UPDATE projection_recompute_request
       SET status='failed', completed_at=now(), last_error=p_error
     WHERE projection_recompute_request_id=p_request_id;

    UPDATE invalidation_record ir
       SET recomputation_status='failed'
      FROM projection_recompute_cause c
     WHERE c.projection_recompute_request_id=p_request_id
       AND c.invalidation_id=ir.invalidation_id;

    INSERT INTO projection_recompute_state_event(
        projection_recompute_request_id, state_type, worker_key, details
    ) VALUES (
        p_request_id, 'failed', v_worker, jsonb_build_object('error',p_error)
    );
END;
$$;

CREATE OR REPLACE FUNCTION retry_projection_recompute(p_request_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_request projection_recompute_request%ROWTYPE;
BEGIN
    SELECT * INTO v_request
      FROM projection_recompute_request
     WHERE projection_recompute_request_id=p_request_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'recompute request missing'; END IF;
    IF v_request.status <> 'failed' THEN RAISE EXCEPTION 'only failed recompute request may be retried'; END IF;
    IF EXISTS (
        SELECT 1 FROM projection_recompute_request other
         WHERE other.school_id=v_request.school_id
           AND other.projection_key=v_request.projection_key
           AND other.scope_key=v_request.scope_key
           AND other.status IN ('pending','running')
           AND other.projection_recompute_request_id<>p_request_id
    ) THEN
        RAISE EXCEPTION 'another active recompute request already exists for scope';
    END IF;

    UPDATE projection_recompute_request
       SET status='pending', requested_at=now(), claimed_at=NULL, claimed_by=NULL,
           completed_at=NULL, result_projection_run_id=NULL, last_error=NULL
     WHERE projection_recompute_request_id=p_request_id;

    UPDATE invalidation_record ir
       SET recomputation_status='pending'
      FROM projection_recompute_cause c
     WHERE c.projection_recompute_request_id=p_request_id
       AND c.invalidation_id=ir.invalidation_id;

    INSERT INTO projection_recompute_state_event(
        projection_recompute_request_id, state_type, details
    ) VALUES (
        p_request_id, 'retried', '{}'::jsonb
    );
END;
$$;

-- Read model for applications: current generation plus latest snapshot state. It can
-- explicitly expose a stale current profile while recomputation is pending.
CREATE OR REPLACE VIEW current_student_profile_status AS
SELECT
    s.school_id,
    s.student_id,
    s.snapshot_id,
    s.generation_id,
    s.projection_run_id,
    pgc.scope_key,
    s.as_of_time,
    s.input_watermark,
    s.computed_profile,
    st.state_type AS latest_state,
    st.stale_from,
    st.reason AS latest_state_reason,
    st.recorded_at AS latest_state_recorded_at
FROM projection_generation_current pgc
JOIN student_profile_snapshot s
  ON s.school_id=pgc.school_id
 AND s.generation_id=pgc.generation_id
LEFT JOIN LATERAL (
    SELECT e.state_type, e.stale_from, e.reason, e.recorded_at
      FROM student_profile_snapshot_state_event e
     WHERE e.snapshot_id=s.snapshot_id
     ORDER BY e.recorded_at DESC, e.snapshot_state_event_id DESC
     LIMIT 1
) st ON true
WHERE pgc.projection_key='student_profile';

-- -----------------------------------------------------------------------------
-- Runtime boundaries: dependency construction happens via trusted triggers; invalidation
-- and queue state changes happen only through guarded SECURITY DEFINER functions.
-- -----------------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE ON TABLE
    invalidation_batch,
    invalidation_record,
    projection_output_entity,
    projection_recompute_request,
    projection_recompute_cause,
    projection_recompute_state_event
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

GRANT SELECT ON TABLE current_student_profile_status TO bridge_school_reader;

REVOKE ALL ON FUNCTION invalidate_dependency_subgraph(uuid, uuid, text, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION claim_projection_recompute(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION complete_projection_recompute(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION fail_projection_recompute(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION retry_projection_recompute(uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION invalidate_dependency_subgraph(uuid, uuid, text, jsonb, text) TO bridge_school_worker;
GRANT EXECUTE ON FUNCTION claim_projection_recompute(text) TO bridge_school_worker;
GRANT EXECUTE ON FUNCTION complete_projection_recompute(uuid, uuid) TO bridge_school_worker;
GRANT EXECUTE ON FUNCTION fail_projection_recompute(uuid, text) TO bridge_school_worker;
GRANT EXECUTE ON FUNCTION retry_projection_recompute(uuid) TO bridge_school_worker;

REVOKE ALL ON FUNCTION register_student_profile_projection_output() FROM PUBLIC;
REVOKE ALL ON FUNCTION register_learning_observation_dependencies() FROM PUBLIC;
REVOKE ALL ON FUNCTION register_student_profile_input_dependencies() FROM PUBLIC;
REVOKE ALL ON FUNCTION register_recommendation_dependencies() FROM PUBLIC;
REVOKE ALL ON FUNCTION register_session_plan_recommendation_dependency() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION register_student_profile_projection_output() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION register_learning_observation_dependencies() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION register_student_profile_input_dependencies() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION register_recommendation_dependencies() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION register_session_plan_recommendation_dependency() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0013_projection_invalidation_recompute')
ON CONFLICT DO NOTHING;

COMMIT;
