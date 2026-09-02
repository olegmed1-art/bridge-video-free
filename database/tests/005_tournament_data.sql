\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_school uuid;
    v_other_school uuid;
    v_source uuid;
    v_other_source uuid;
    v_identity_n uuid;
    v_identity_s uuid;
    v_identity_e uuid;
    v_identity_w uuid;
    v_person_n uuid;
    v_person_wrong uuid;
    v_student_n uuid;
    v_resolution_n uuid;
    v_tournament uuid;
    v_other_tournament uuid;
    v_ns_entry uuid;
    v_ew_entry uuid;
    v_other_entry uuid;
    v_member_n uuid;
    v_board uuid;
    v_result uuid;
    v_obs uuid;
    failed boolean;
BEGIN
    SELECT school_id INTO v_school FROM school WHERE stable_name='Школа спортивного бриджа';
    IF v_school IS NULL THEN RAISE EXCEPTION 'school seed missing'; END IF;

    INSERT INTO school(stable_name) VALUES ('CI tournament other school')
    RETURNING school_id INTO v_other_school;

    INSERT INTO source(school_id, source_type, title)
    VALUES (v_school, 'tournament_server', 'CI tournament source')
    RETURNING source_id INTO v_source;

    INSERT INTO source(school_id, source_type, title)
    VALUES (v_other_school, 'tournament_server', 'CI other tournament source')
    RETURNING source_id INTO v_other_source;

    failed := false;
    BEGIN
        INSERT INTO tournament(school_id, source_id, provider_native_key, name)
        VALUES (v_school, v_other_source, 'wrong-school', 'Wrong source scope');
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'tournament/source school guard failed'; END IF;

    INSERT INTO tournament(
        school_id, source_id, provider_native_key, name, organizer,
        event_format, scoring_type, starts_at, temporal_precision
    ) VALUES (
        v_school, v_source, 'ci-tournament-1', 'CI Tournament', 'CI Organizer',
        'pairs', 'matchpoints', now(), 'exact'
    ) RETURNING tournament_id INTO v_tournament;

    INSERT INTO tournament(
        school_id, source_id, provider_native_key, name, event_format, scoring_type
    ) VALUES (
        v_school, v_source, 'ci-tournament-2', 'CI Other Tournament', 'pairs', 'matchpoints'
    ) RETURNING tournament_id INTO v_other_tournament;

    INSERT INTO source_identity(source_id, source_native_key, display_name)
    VALUES (v_source, 'ci-player-n', 'CI North') RETURNING source_identity_id INTO v_identity_n;
    INSERT INTO source_identity(source_id, source_native_key, display_name)
    VALUES (v_source, 'ci-player-s', 'CI South') RETURNING source_identity_id INTO v_identity_s;
    INSERT INTO source_identity(source_id, source_native_key, display_name)
    VALUES (v_source, 'ci-player-e', 'CI East') RETURNING source_identity_id INTO v_identity_e;
    INSERT INTO source_identity(source_id, source_native_key, display_name)
    VALUES (v_source, 'ci-player-w', 'CI West') RETURNING source_identity_id INTO v_identity_w;

    INSERT INTO tournament_participation(
        tournament_id, source_native_key, entry_type, entry_number, pair_number, entry_label
    ) VALUES (v_tournament, 'entry-ns', 'pair', '1', '1', 'NS Pair')
    RETURNING tournament_participation_id INTO v_ns_entry;

    INSERT INTO tournament_participation(
        tournament_id, source_native_key, entry_type, entry_number, pair_number, entry_label
    ) VALUES (v_tournament, 'entry-ew', 'pair', '2', '2', 'EW Pair')
    RETURNING tournament_participation_id INTO v_ew_entry;

    INSERT INTO tournament_participation(
        tournament_id, source_native_key, entry_type, entry_number, pair_number
    ) VALUES (v_other_tournament, 'other-entry', 'pair', '99', '99')
    RETURNING tournament_participation_id INTO v_other_entry;

    INSERT INTO tournament_participant_member(tournament_participation_id, source_identity_id, member_no, seat_label)
    VALUES (v_ns_entry, v_identity_n, 1, 'N')
    RETURNING tournament_participant_member_id INTO v_member_n;
    INSERT INTO tournament_participant_member(tournament_participation_id, source_identity_id, member_no, seat_label)
    VALUES (v_ns_entry, v_identity_s, 2, 'S');
    INSERT INTO tournament_participant_member(tournament_participation_id, source_identity_id, member_no, seat_label)
    VALUES (v_ew_entry, v_identity_e, 1, 'E');
    INSERT INTO tournament_participant_member(tournament_participation_id, source_identity_id, member_no, seat_label)
    VALUES (v_ew_entry, v_identity_w, 2, 'W');

    -- External identity is attached to Student only through an explicit link decision.
    INSERT INTO person(preferred_name) VALUES ('CI resolved North') RETURNING person_id INTO v_person_n;
    INSERT INTO person(preferred_name) VALUES ('CI wrong person') RETURNING person_id INTO v_person_wrong;
    INSERT INTO student(school_id, person_id) VALUES (v_school, v_person_n) RETURNING student_id INTO v_student_n;
    INSERT INTO entity_resolution_decision(
        source_identity_id, target_person_id, decision_type, confidence_class, status
    ) VALUES (v_identity_n, v_person_n, 'link', 'HIGH', 'active')
    RETURNING resolution_id INTO v_resolution_n;

    INSERT INTO tournament_identity_attribution(
        tournament_participant_member_id, entity_resolution_decision_id,
        person_id, student_id, confidence_class, attribution_method
    ) VALUES (
        v_member_n, v_resolution_n, v_person_n, v_student_n, 'HIGH', 'ci-explicit-resolution'
    );

    failed := false;
    BEGIN
        INSERT INTO tournament_identity_attribution(
            tournament_participant_member_id, entity_resolution_decision_id,
            person_id, confidence_class
        ) VALUES (v_member_n, v_resolution_n, v_person_wrong, 'LOW');
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'tournament identity attribution guard failed'; END IF;

    INSERT INTO source_observation(
        source_id, provider_native_key, provider_revision, payload_hash, payload
    ) VALUES (
        v_source, 'board-1', 'r1', 'board-hash-1', '{"board":1}'::jsonb
    ) RETURNING source_observation_id INTO v_obs;

    INSERT INTO tournament_board(
        tournament_id, source_observation_id, source_native_key,
        board_number, board_sequence_no
    ) VALUES (v_tournament, v_obs, 'board-1', '1', 1)
    RETURNING tournament_board_id INTO v_board;

    INSERT INTO table_result(
        school_id, tournament_board_id, source_id, provider_native_key, payload_hash,
        record_kind, table_no, round_no, ns_participation_id, ew_participation_id,
        contract, declarer, tricks_taken, result_delta, raw_score_ns,
        matchpoints_ns, matchpoints_ew, percentage_ns, percentage_ew
    ) VALUES (
        v_school, v_board, v_source, 'result-board1-table1', 'result-hash-1',
        'observed', '1', 1, v_ns_entry, v_ew_entry,
        '4S', 'N', 10, 0, 420,
        8, 2, 80, 20
    ) RETURNING result_id INTO v_result;

    -- Exact redelivery must not create another raw result.
    failed := false;
    BEGIN
        INSERT INTO table_result(
            school_id, tournament_board_id, source_id, provider_native_key, payload_hash,
            ns_participation_id, ew_participation_id
        ) VALUES (
            v_school, v_board, v_source, 'result-board1-table1', 'result-hash-1',
            v_ns_entry, v_ew_entry
        );
    EXCEPTION WHEN unique_violation THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'exact tournament result deduplication failed'; END IF;

    -- Same native result key with corrected content is preserved as a new immutable row.
    INSERT INTO table_result(
        school_id, tournament_board_id, source_id, provider_native_key, payload_hash,
        correction_of_result_id, record_kind, table_no, round_no,
        ns_participation_id, ew_participation_id,
        contract, declarer, tricks_taken, raw_score_ns
    ) VALUES (
        v_school, v_board, v_source, 'result-board1-table1', 'result-hash-2',
        v_result, 'correction', '1', 1,
        v_ns_entry, v_ew_entry,
        '4S', 'N', 11, 450
    );

    failed := false;
    BEGIN
        INSERT INTO table_result(
            school_id, tournament_board_id, source_id, provider_native_key, payload_hash,
            correction_of_result_id, record_kind
        ) VALUES (
            v_school, v_board, v_source, 'different-native-key', 'result-hash-3',
            v_result, 'correction'
        );
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'table result correction lineage guard failed'; END IF;

    failed := false;
    BEGIN
        INSERT INTO table_result(
            school_id, tournament_board_id, source_id, provider_native_key, payload_hash,
            ns_participation_id, ew_participation_id
        ) VALUES (
            v_school, v_board, v_source, 'cross-tournament-entry', 'result-hash-4',
            v_other_entry, v_ew_entry
        );
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'table result tournament participation scope guard failed'; END IF;

    failed := false;
    BEGIN
        INSERT INTO table_result(
            school_id, tournament_board_id, source_id, provider_native_key, payload_hash,
            ns_participation_id, ew_participation_id
        ) VALUES (
            v_school, v_board, v_source, 'same-entry-both-directions', 'result-hash-5',
            v_ns_entry, v_ns_entry
        );
    EXCEPTION WHEN raise_exception THEN failed := true;
    END;
    IF NOT failed THEN RAISE EXCEPTION 'NS/EW distinct participation guard failed'; END IF;

    -- Runtime boundaries: tournament ingestion is a worker concern; raw result and
    -- attribution history are append-only and the interactive app cannot write them.
    IF NOT has_table_privilege('bridge_school_worker','tournament','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','tournament_board','UPDATE')
       OR NOT has_table_privilege('bridge_school_worker','table_result','INSERT')
       OR NOT has_table_privilege('bridge_school_worker','tournament_identity_attribution','INSERT') THEN
        RAISE EXCEPTION 'worker tournament privileges missing';
    END IF;

    IF has_table_privilege('bridge_school_worker','table_result','UPDATE')
       OR has_table_privilege('bridge_school_worker','table_result','DELETE')
       OR has_table_privilege('bridge_school_worker','tournament_identity_attribution','UPDATE')
       OR has_table_privilege('bridge_school_worker','tournament_identity_attribution','DELETE') THEN
        RAISE EXCEPTION 'raw tournament result/attribution history is not append-only for worker';
    END IF;

    IF has_table_privilege('bridge_school_app','tournament','INSERT')
       OR has_table_privilege('bridge_school_app','table_result','INSERT')
       OR has_table_privilege('bridge_school_app','tournament_participant_member','UPDATE') THEN
        RAISE EXCEPTION 'interactive app crossed tournament ingestion boundary';
    END IF;

    IF has_function_privilege('bridge_school_worker','validate_table_result_scope()','EXECUTE')
       OR has_function_privilege('bridge_school_app','validate_tournament_identity_attribution()','EXECUTE') THEN
        RAISE EXCEPTION 'internal tournament validation function exposed to runtime';
    END IF;
END $$;

ROLLBACK;
