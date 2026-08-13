\set ON_ERROR_STOP on
BEGIN;

-- Tournament/event identity. Source provenance is mandatory; a school-authored event
-- can use a school-internal Source rather than bypassing provenance.
CREATE TABLE IF NOT EXISTS tournament (
    tournament_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    source_id uuid NOT NULL REFERENCES source(source_id),
    provider_native_key text,
    name text NOT NULL,
    organizer text,
    event_format text NOT NULL DEFAULT 'pairs',
    scoring_type text,
    starts_at timestamptz,
    ends_at timestamptz,
    temporal_precision text NOT NULL DEFAULT 'day',
    source_local_time text,
    source_timezone text,
    status text NOT NULL DEFAULT 'active',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at),
    CHECK (temporal_precision IN ('exact','minute','day','unknown')),
    UNIQUE (source_id, provider_native_key)
);
CREATE INDEX IF NOT EXISTS tournament_school_time_idx
    ON tournament(school_id, starts_at DESC NULLS LAST, created_at DESC);

-- Competition entry: pair/team/individual entry as named by the tournament source.
-- It deliberately does not attach directly to Student/Person. Member identities below
-- remain source-scoped until an explicit EntityResolutionDecision is recorded.
CREATE TABLE IF NOT EXISTS tournament_participation (
    tournament_participation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tournament_id uuid NOT NULL REFERENCES tournament(tournament_id),
    source_native_key text,
    entry_type text NOT NULL DEFAULT 'pair',
    entry_number text,
    pair_number text,
    team_number text,
    entry_label text,
    status text NOT NULL DEFAULT 'active',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (entry_type IN ('pair','team','individual','unknown')),
    UNIQUE (tournament_id, source_native_key)
);
CREATE INDEX IF NOT EXISTS tournament_participation_number_idx
    ON tournament_participation(tournament_id, entry_number, pair_number, team_number);

CREATE TABLE IF NOT EXISTS tournament_participant_member (
    tournament_participant_member_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tournament_participation_id uuid NOT NULL REFERENCES tournament_participation(tournament_participation_id),
    source_identity_id uuid NOT NULL REFERENCES source_identity(source_identity_id),
    member_no integer CHECK (member_no IS NULL OR member_no > 0),
    seat_label text,
    member_role text NOT NULL DEFAULT 'player',
    source_native_key text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tournament_participation_id, source_identity_id)
);
CREATE INDEX IF NOT EXISTS tournament_participant_identity_idx
    ON tournament_participant_member(source_identity_id, tournament_participation_id);

-- Historical attribution says which explicit identity-resolution decision was used
-- when a tournament source identity was associated with an internal Person/Student.
-- It is append-only: later revocation never rewrites this historical basis.
CREATE TABLE IF NOT EXISTS tournament_identity_attribution (
    tournament_identity_attribution_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tournament_participant_member_id uuid NOT NULL REFERENCES tournament_participant_member(tournament_participant_member_id),
    entity_resolution_decision_id uuid NOT NULL REFERENCES entity_resolution_decision(resolution_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    student_id uuid REFERENCES student(student_id),
    confidence_class text NOT NULL DEFAULT 'UNKNOWN',
    attribution_method text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tournament_participant_member_id, entity_resolution_decision_id)
);
CREATE INDEX IF NOT EXISTS tournament_identity_attribution_person_idx
    ON tournament_identity_attribution(person_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tournament_identity_attribution_student_idx
    ON tournament_identity_attribution(student_id, created_at DESC)
    WHERE student_id IS NOT NULL;

-- TournamentBoard links an event-specific board occurrence to a reusable Deal when the
-- deal is known. Board number is not globally unique because multi-session events may
-- legitimately repeat visible board numbers; source_native_key is the provider identity.
CREATE TABLE IF NOT EXISTS tournament_board (
    tournament_board_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tournament_id uuid NOT NULL REFERENCES tournament(tournament_id),
    source_observation_id uuid REFERENCES source_observation(source_observation_id),
    source_native_key text,
    board_number text NOT NULL,
    board_sequence_no integer CHECK (board_sequence_no IS NULL OR board_sequence_no > 0),
    deal_id uuid REFERENCES deal(deal_id),
    dealer_override text,
    vulnerability_override text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tournament_id, source_native_key)
);
CREATE INDEX IF NOT EXISTS tournament_board_number_idx
    ON tournament_board(tournament_id, board_number, board_sequence_no);
CREATE INDEX IF NOT EXISTS tournament_board_deal_idx
    ON tournament_board(deal_id, tournament_id)
    WHERE deal_id IS NOT NULL;

-- TableResult is an immutable source fact. Exact redelivery is deduplicated by
-- (source, native key, payload hash). A provider correction with the same native key
-- and a different payload hash creates a new row and may point to correction_of_result_id.
CREATE TABLE IF NOT EXISTS table_result (
    result_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    tournament_board_id uuid NOT NULL REFERENCES tournament_board(tournament_board_id),
    source_id uuid NOT NULL REFERENCES source(source_id),
    source_observation_id uuid REFERENCES source_observation(source_observation_id),
    provider_native_key text NOT NULL,
    payload_hash text NOT NULL,
    correction_of_result_id uuid REFERENCES table_result(result_id),
    record_kind text NOT NULL DEFAULT 'observed',
    observed_at timestamptz NOT NULL DEFAULT now(),
    table_no text,
    round_no integer CHECK (round_no IS NULL OR round_no > 0),
    ns_participation_id uuid REFERENCES tournament_participation(tournament_participation_id),
    ew_participation_id uuid REFERENCES tournament_participation(tournament_participation_id),
    contract text,
    declarer text,
    opening_lead text,
    tricks_taken smallint CHECK (tricks_taken IS NULL OR tricks_taken BETWEEN 0 AND 13),
    result_delta smallint CHECK (result_delta IS NULL OR result_delta BETWEEN -13 AND 13),
    raw_score_ns integer,
    matchpoints_ns numeric(14,4),
    matchpoints_ew numeric(14,4),
    percentage_ns numeric(8,4) CHECK (percentage_ns IS NULL OR percentage_ns BETWEEN 0 AND 100),
    percentage_ew numeric(8,4) CHECK (percentage_ew IS NULL OR percentage_ew BETWEEN 0 AND 100),
    imps_ns numeric(14,4),
    imps_ew numeric(14,4),
    scoring_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (record_kind IN ('observed','correction','retraction')),
    CHECK (correction_of_result_id IS NULL OR correction_of_result_id <> result_id),
    UNIQUE (source_id, provider_native_key, payload_hash)
);
CREATE INDEX IF NOT EXISTS table_result_board_time_idx
    ON table_result(tournament_board_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS table_result_native_idx
    ON table_result(source_id, provider_native_key, observed_at DESC);
CREATE INDEX IF NOT EXISTS table_result_ns_entry_idx
    ON table_result(ns_participation_id, observed_at DESC)
    WHERE ns_participation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS table_result_ew_entry_idx
    ON table_result(ew_participation_id, observed_at DESC)
    WHERE ew_participation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS table_result_correction_idx
    ON table_result(correction_of_result_id)
    WHERE correction_of_result_id IS NOT NULL;

-- Validate school/source scope for the tournament itself.
CREATE OR REPLACE FUNCTION validate_tournament_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_school uuid;
BEGIN
    SELECT school_id INTO v_source_school FROM source WHERE source_id = NEW.source_id;
    IF v_source_school IS NULL OR v_source_school <> NEW.school_id THEN
        RAISE EXCEPTION 'tournament source belongs to another school or is missing';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tournament_scope_guard ON tournament;
CREATE TRIGGER tournament_scope_guard
BEFORE INSERT OR UPDATE OF school_id, source_id
ON tournament
FOR EACH ROW
EXECUTE FUNCTION validate_tournament_scope();

-- A member SourceIdentity must originate from the same school as the tournament.
CREATE OR REPLACE FUNCTION validate_tournament_member_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_tournament_school uuid;
    v_identity_school uuid;
BEGIN
    SELECT t.school_id
      INTO v_tournament_school
      FROM tournament_participation p
      JOIN tournament t ON t.tournament_id = p.tournament_id
     WHERE p.tournament_participation_id = NEW.tournament_participation_id;

    SELECT s.school_id
      INTO v_identity_school
      FROM source_identity si
      JOIN source s ON s.source_id = si.source_id
     WHERE si.source_identity_id = NEW.source_identity_id;

    IF v_tournament_school IS NULL OR v_identity_school IS NULL OR v_tournament_school <> v_identity_school THEN
        RAISE EXCEPTION 'tournament participant source identity belongs to another school or is missing';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tournament_member_scope_guard ON tournament_participant_member;
CREATE TRIGGER tournament_member_scope_guard
BEFORE INSERT OR UPDATE OF tournament_participation_id, source_identity_id
ON tournament_participant_member
FOR EACH ROW
EXECUTE FUNCTION validate_tournament_member_scope();

-- Attribution is allowed only through a matching explicit link decision. This prevents
-- a name-only guess from silently contaminating a Student history.
CREATE OR REPLACE FUNCTION validate_tournament_identity_attribution()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_member_identity uuid;
    v_resolution_identity uuid;
    v_resolution_person uuid;
    v_resolution_type text;
    v_resolution_status text;
    v_student_person uuid;
    v_student_school uuid;
    v_tournament_school uuid;
BEGIN
    SELECT source_identity_id
      INTO v_member_identity
      FROM tournament_participant_member
     WHERE tournament_participant_member_id = NEW.tournament_participant_member_id;

    SELECT source_identity_id, target_person_id, decision_type, status
      INTO v_resolution_identity, v_resolution_person, v_resolution_type, v_resolution_status
      FROM entity_resolution_decision
     WHERE resolution_id = NEW.entity_resolution_decision_id;

    IF v_member_identity IS NULL OR v_resolution_identity IS NULL THEN
        RAISE EXCEPTION 'member or identity-resolution decision is missing';
    END IF;
    IF v_member_identity <> v_resolution_identity THEN
        RAISE EXCEPTION 'identity-resolution decision does not belong to tournament member identity';
    END IF;
    IF v_resolution_type <> 'link' OR v_resolution_status <> 'active' OR v_resolution_person IS NULL THEN
        RAISE EXCEPTION 'tournament attribution requires an active explicit link decision';
    END IF;
    IF NEW.person_id <> v_resolution_person THEN
        RAISE EXCEPTION 'attributed person does not match identity-resolution target';
    END IF;

    SELECT t.school_id
      INTO v_tournament_school
      FROM tournament_participant_member m
      JOIN tournament_participation p ON p.tournament_participation_id = m.tournament_participation_id
      JOIN tournament t ON t.tournament_id = p.tournament_id
     WHERE m.tournament_participant_member_id = NEW.tournament_participant_member_id;

    IF NEW.student_id IS NOT NULL THEN
        SELECT person_id, school_id
          INTO v_student_person, v_student_school
          FROM student
         WHERE student_id = NEW.student_id;
        IF v_student_person IS NULL OR v_student_person <> NEW.person_id THEN
            RAISE EXCEPTION 'attributed student does not belong to attributed person';
        END IF;
        IF v_student_school <> v_tournament_school THEN
            RAISE EXCEPTION 'attributed student belongs to another school';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tournament_identity_attribution_guard ON tournament_identity_attribution;
CREATE TRIGGER tournament_identity_attribution_guard
BEFORE INSERT OR UPDATE OF tournament_participant_member_id, entity_resolution_decision_id, person_id, student_id
ON tournament_identity_attribution
FOR EACH ROW
EXECUTE FUNCTION validate_tournament_identity_attribution();

-- If a TournamentBoard is linked to a Deal, both must belong to the same school.
-- If a SourceObservation is attached, it must belong to the Tournament's Source.
CREATE OR REPLACE FUNCTION validate_tournament_board_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_tournament_school uuid;
    v_tournament_source uuid;
    v_deal_school uuid;
    v_observation_source uuid;
BEGIN
    SELECT school_id, source_id
      INTO v_tournament_school, v_tournament_source
      FROM tournament
     WHERE tournament_id = NEW.tournament_id;

    IF NEW.deal_id IS NOT NULL THEN
        SELECT school_id INTO v_deal_school FROM deal WHERE deal_id = NEW.deal_id;
        IF v_deal_school IS NULL OR v_deal_school <> v_tournament_school THEN
            RAISE EXCEPTION 'tournament board deal belongs to another school or is missing';
        END IF;
    END IF;

    IF NEW.source_observation_id IS NOT NULL THEN
        SELECT source_id INTO v_observation_source
          FROM source_observation
         WHERE source_observation_id = NEW.source_observation_id;
        IF v_observation_source IS NULL OR v_observation_source <> v_tournament_source THEN
            RAISE EXCEPTION 'tournament board observation does not belong to tournament source';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tournament_board_scope_guard ON tournament_board;
CREATE TRIGGER tournament_board_scope_guard
BEFORE INSERT OR UPDATE OF tournament_id, deal_id, source_observation_id
ON tournament_board
FOR EACH ROW
EXECUTE FUNCTION validate_tournament_board_scope();

-- Result scope and correction-lineage validation. Missing participant references are
-- allowed so ingestion can quarantine/resolve them later via PendingReference, but any
-- participant reference that is present must belong to the same tournament.
CREATE OR REPLACE FUNCTION validate_table_result_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_tournament_id uuid;
    v_school_id uuid;
    v_source_school uuid;
    v_obs_source uuid;
    v_ns_tournament uuid;
    v_ew_tournament uuid;
    v_old_source uuid;
    v_old_native_key text;
BEGIN
    SELECT t.tournament_id, t.school_id
      INTO v_tournament_id, v_school_id
      FROM tournament_board b
      JOIN tournament t ON t.tournament_id = b.tournament_id
     WHERE b.tournament_board_id = NEW.tournament_board_id;

    IF v_tournament_id IS NULL OR v_school_id <> NEW.school_id THEN
        RAISE EXCEPTION 'table result school does not match tournament board';
    END IF;

    SELECT school_id INTO v_source_school FROM source WHERE source_id = NEW.source_id;
    IF v_source_school IS NULL OR v_source_school <> NEW.school_id THEN
        RAISE EXCEPTION 'table result source belongs to another school or is missing';
    END IF;

    IF NEW.source_observation_id IS NOT NULL THEN
        SELECT source_id INTO v_obs_source
          FROM source_observation
         WHERE source_observation_id = NEW.source_observation_id;
        IF v_obs_source IS NULL OR v_obs_source <> NEW.source_id THEN
            RAISE EXCEPTION 'table result observation does not match result source';
        END IF;
    END IF;

    IF NEW.ns_participation_id IS NOT NULL THEN
        SELECT tournament_id INTO v_ns_tournament
          FROM tournament_participation
         WHERE tournament_participation_id = NEW.ns_participation_id;
        IF v_ns_tournament IS NULL OR v_ns_tournament <> v_tournament_id THEN
            RAISE EXCEPTION 'NS participation belongs to another tournament';
        END IF;
    END IF;

    IF NEW.ew_participation_id IS NOT NULL THEN
        SELECT tournament_id INTO v_ew_tournament
          FROM tournament_participation
         WHERE tournament_participation_id = NEW.ew_participation_id;
        IF v_ew_tournament IS NULL OR v_ew_tournament <> v_tournament_id THEN
            RAISE EXCEPTION 'EW participation belongs to another tournament';
        END IF;
    END IF;

    IF NEW.ns_participation_id IS NOT NULL
       AND NEW.ew_participation_id IS NOT NULL
       AND NEW.ns_participation_id = NEW.ew_participation_id THEN
        RAISE EXCEPTION 'same tournament participation cannot occupy both NS and EW';
    END IF;

    IF NEW.correction_of_result_id IS NOT NULL THEN
        SELECT source_id, provider_native_key
          INTO v_old_source, v_old_native_key
          FROM table_result
         WHERE result_id = NEW.correction_of_result_id;
        IF v_old_source IS NULL THEN
            RAISE EXCEPTION 'corrected table result is missing';
        END IF;
        IF v_old_source <> NEW.source_id OR v_old_native_key <> NEW.provider_native_key THEN
            RAISE EXCEPTION 'correction must preserve source and provider native key';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS table_result_scope_guard ON table_result;
CREATE TRIGGER table_result_scope_guard
BEFORE INSERT OR UPDATE OF
    school_id,
    tournament_board_id,
    source_id,
    source_observation_id,
    provider_native_key,
    correction_of_result_id,
    ns_participation_id,
    ew_participation_id
ON table_result
FOR EACH ROW
EXECUTE FUNCTION validate_table_result_scope();

-- Runtime permissions. Tournament context may be normalized as more source material is
-- resolved, but raw TableResult and historical identity-attribution records remain append-only.
GRANT INSERT, UPDATE ON TABLE
    tournament,
    tournament_participation,
    tournament_participant_member,
    tournament_board
TO bridge_school_worker;

GRANT INSERT ON TABLE
    tournament_identity_attribution,
    table_result
TO bridge_school_worker;

REVOKE UPDATE, DELETE ON TABLE
    tournament_identity_attribution,
    table_result
FROM bridge_school_worker;

REVOKE INSERT, UPDATE, DELETE ON TABLE
    tournament,
    tournament_participation,
    tournament_participant_member,
    tournament_identity_attribution,
    tournament_board,
    table_result
FROM bridge_school_app;

REVOKE DELETE ON TABLE
    tournament,
    tournament_participation,
    tournament_participant_member,
    tournament_identity_attribution,
    tournament_board,
    table_result
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

-- Trigger helpers are internal implementation details.
REVOKE ALL ON FUNCTION validate_tournament_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_tournament_member_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_tournament_identity_attribution() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_tournament_board_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_table_result_scope() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION validate_tournament_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_tournament_member_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_tournament_identity_attribution() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_tournament_board_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE EXECUTE ON FUNCTION validate_table_result_scope() FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0009_tournament_data')
ON CONFLICT DO NOTHING;

COMMIT;
