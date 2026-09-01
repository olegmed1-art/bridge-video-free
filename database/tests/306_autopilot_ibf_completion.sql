\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    task_row record;
    claimed record;
    valid_summary jsonb;
BEGIN
    SELECT * INTO task_row
      FROM autopilot.register_approved_ibf_analysis(
          'sql-ibf-completion-0307',
          '15031',
          'github:issue:1013#director-go',
          10
      );
    IF NOT task_row.created OR task_row.status <> 'READY' THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_COMPLETION_SETUP_FAILED';
    END IF;

    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-ibf-completion-worker', 60);
    IF claimed.task_id <> task_row.task_id
       OR claimed.goal_type <> 'IBF_READ_ONLY_ANALYSIS'
       OR claimed.current_step_key <> 'ibf.read_only_analysis' THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_COMPLETION_CLAIM_INVALID';
    END IF;

    valid_summary := jsonb_build_object(
        'source_authority', 'ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS',
        'ibf_player_id', '15031',
        'latest_participation', jsonb_build_object(
            'date', '2026-08-27',
            'event_id', 29692,
            'round_id', 9,
            'seat', '4',
            'session_url', 'https://bridge.co.il/viewer/session.php?event=29692&round=9',
            'personal_url', 'https://bridge.co.il/viewer/personal.php?event=29692&round=9&seat=4'
        ),
        'board_count', 1,
        'boards', jsonb_build_array(jsonb_build_object(
            'board_number', 1,
            'personal_row_excerpt', '1 | EW | -420 | 0.00 | H9 | 4S= E',
            'percentage_token', '0.00',
            'score_token', '-420',
            'field_row_count', 4,
            'field_page_sha256', repeat('a', 64)
        )),
        'member_page_sha256', repeat('b', 64),
        'results_index_sha256', repeat('c', 64),
        'session_page_sha256', repeat('d', 64),
        'personal_page_sha256', repeat('e', 64),
        'request_count', 5,
        'http_method', 'GET',
        'production_mutation', false,
        'model_calls', 0,
        'cost_actual_microusd', 0,
        'analysis_scope', 'SOURCE_RETRIEVAL_AND_FIELD_EVIDENCE_ONLY'
    );

    BEGIN
        PERFORM autopilot.complete_task(
            task_row.task_id,
            'sql-ibf-completion-worker',
            claimed.lease_epoch,
            'IBF_READ_ONLY_ANALYSIS_EVIDENCE',
            repeat('f', 64),
            jsonb_set(valid_summary, '{ibf_player_id}', '"15032"'::jsonb)
        );
        RAISE EXCEPTION 'AUTOPILOT_IBF_FORGED_PLAYER_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_IBF_EVIDENCE_INVALID%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM autopilot.complete_task(
            task_row.task_id,
            'sql-ibf-completion-worker',
            claimed.lease_epoch,
            'IBF_READ_ONLY_ANALYSIS_EVIDENCE',
            repeat('f', 64),
            valid_summary || jsonb_build_object('unexpected_key', true)
        );
        RAISE EXCEPTION 'AUTOPILOT_IBF_EXTRA_EVIDENCE_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_IBF_EVIDENCE_INVALID%' THEN RAISE; END IF;
    END;

    IF NOT autopilot.complete_task(
        task_row.task_id,
        'sql-ibf-completion-worker',
        claimed.lease_epoch,
        'IBF_READ_ONLY_ANALYSIS_EVIDENCE',
        repeat('f', 64),
        valid_summary
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_COMPLETION_REJECTED';
    END IF;

    IF (SELECT status FROM autopilot.task WHERE task_id = task_row.task_id) <> 'DONE'
       OR (SELECT terminal_reason_code FROM autopilot.task WHERE task_id = task_row.task_id)
          <> 'ACCEPTANCE_EVIDENCE_RETAINED'
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.evidence
            WHERE task_id = task_row.task_id
              AND evidence_class = 'IBF_READ_ONLY_ANALYSIS_EVIDENCE'
              AND metadata_json = valid_summary
              AND retained
       )
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.task_event
            WHERE task_id = task_row.task_id
              AND event_type = 'TASK_DONE'
              AND state_to = 'DONE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_COMPLETION_EVIDENCE_GATE_FAILED';
    END IF;
END $$;

ROLLBACK;
