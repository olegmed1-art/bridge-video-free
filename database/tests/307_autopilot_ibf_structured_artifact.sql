\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    task_row record;
    claimed record;
    artifact jsonb;
    artifact_bytes bytea;
    artifact_hash text;
    manifest jsonb;
    changed_artifact jsonb;
    changed_bytes bytea;
    changed_hash text;
    changed_manifest jsonb;
BEGIN
    SELECT * INTO task_row
      FROM autopilot.register_approved_ibf_analysis(
          'sql-ibf-artifact-0308',
          '15031',
          'github:issue:1013#director-go',
          10
      );
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-ibf-artifact-worker', 60);
    IF claimed.task_id <> task_row.task_id THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_SETUP_FAILED';
    END IF;

    artifact := jsonb_build_object(
        'schema_version', 'IBF_STRUCTURED_TOURNAMENT_V1',
        'source_authority', 'ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS',
        'ibf_player_id', '15031',
        'latest_participation', jsonb_build_object(
            'date', '2026-08-27', 'event_id', 29692, 'round_id', 9, 'seat', '4',
            'session_url', 'https://bridge.co.il/viewer/session.php?event=29692&round=9',
            'personal_url', 'https://bridge.co.il/viewer/personal.php?event=29692&round=9&seat=4'
        ),
        'board_count', 1,
        'boards', jsonb_build_array(jsonb_build_object(
            'board_number', 1,
            'dealer', 'N',
            'vulnerability', 'None',
            'hands', jsonb_build_object(
                'N', jsonb_build_object('S','9854','H','Q952','D','7','C','J964'),
                'E', jsonb_build_object('S','AKJT632','H','JT74','D','93','C',''),
                'S', jsonb_build_object('S','Q7','H','K83','D','QJT64','C','T87'),
                'W', jsonb_build_object('S','','H','A6','D','AK852','C','AKQ532')
            ),
            'double_dummy_tricks', jsonb_build_object(
                'N', jsonb_build_object('NT',0,'S',0,'H',3,'D',2,'C',3),
                'S', jsonb_build_object('NT',4,'S',0,'H',3,'D',2,'C',4),
                'E', jsonb_build_object('NT',9,'S',13,'H',10,'D',11,'C',9),
                'W', jsonb_build_object('NT',9,'S',13,'H',10,'D',11,'C',9)
            ),
            'par_score', -1510,
            'field_results', jsonb_build_array(jsonb_build_object(
                'row',1,'ew_seat','4','ns_seat','5','ew_percentage',0.00,
                'ns_percentage',100.00,'ew_score_cell',null,'ns_score_cell',50,
                'adjustment',null,'opening_lead','D7','contract','6C-1 [W]',
                'target_side','EW'
            )),
            'target_result', jsonb_build_object(
                'side','EW','percentage',0.00,'opening_lead','D7',
                'contract','6C-1 [W]','player_error_demonstrated',false
            ),
            'observability', jsonb_build_object(
                'bidding','UNOBSERVABLE_NO_AUCTION',
                'opening_lead','SOURCE_OBSERVED_NOT_EVALUATED',
                'defense','UNOBSERVABLE_NO_PLAY_RECORD',
                'declarer_play','UNOBSERVABLE_NO_PLAY_RECORD',
                'competitive_decision','UNOBSERVABLE_NO_AUCTION'
            ),
            'dds_source_url_sha256', repeat('a',64),
            'board_page_sha256', repeat('b',64)
        )),
        'teaching_analysis', jsonb_build_object(
            'review_order', jsonb_build_array(jsonb_build_object(
                'board_number',1,'percentage',0.00,'player_error_demonstrated',false
            )),
            'causal_attribution','NOT_DEMONSTRATED_BY_SCORE_OR_DOUBLE_DUMMY_ALONE',
            'missing_source_dimensions', jsonb_build_array('AUCTION','PLAY_RECORD'),
            'methodology_or_canon_applied',false
        ),
        'production_mutation',false,
        'model_calls',0,
        'cost_actual_microusd',0
    );
    artifact_bytes := convert_to(artifact::text, 'UTF8');
    artifact_hash := encode(public.digest(artifact_bytes, 'sha256'), 'hex');
    manifest := jsonb_build_object(
        'analysis_scope','STRUCTURED_SOURCE_AND_REVIEW_CANDIDATES',
        'artifact_bytes',octet_length(artifact_bytes),
        'artifact_schema_version','IBF_STRUCTURED_TOURNAMENT_V1',
        'artifact_sha256',artifact_hash,
        'board_count',1,
        'event_id',29692,
        'ibf_player_id','15031',
        'methodology_or_canon_applied',false,
        'model_calls',0,
        'production_mutation',false,
        'round_id',9,
        'seat','4',
        'source_authority','ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS'
    );

    BEGIN
        PERFORM autopilot.store_ibf_structured_artifact(
            task_row.task_id,'sql-ibf-artifact-worker',claimed.lease_epoch,
            'IBF_STRUCTURED_TOURNAMENT_V1',repeat('f',64),artifact_bytes,manifest
        );
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_FORGED_HASH_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_IBF_ARTIFACT_INVALID%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM autopilot.store_ibf_structured_artifact(
            task_row.task_id,'sql-ibf-artifact-worker',claimed.lease_epoch,
            'IBF_STRUCTURED_TOURNAMENT_V1',artifact_hash,artifact_bytes,
            manifest || jsonb_build_object('unexpected',true)
        );
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_EXTRA_MANIFEST_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_IBF_ARTIFACT_MANIFEST_INVALID%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM autopilot.store_ibf_structured_artifact(
            task_row.task_id,'sql-ibf-artifact-worker',claimed.lease_epoch,
            'IBF_STRUCTURED_TOURNAMENT_V1',artifact_hash,artifact_bytes,
            jsonb_set(manifest,'{ibf_player_id}','"15032"'::jsonb)
        );
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_FORGED_PLAYER_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_IBF_ARTIFACT_CONTENT_INVALID%' THEN RAISE; END IF;
    END;

    IF NOT autopilot.store_ibf_structured_artifact(
        task_row.task_id,'sql-ibf-artifact-worker',claimed.lease_epoch,
        'IBF_STRUCTURED_TOURNAMENT_V1',artifact_hash,artifact_bytes,manifest
    ) OR NOT autopilot.store_ibf_structured_artifact(
        task_row.task_id,'sql-ibf-artifact-worker',claimed.lease_epoch,
        'IBF_STRUCTURED_TOURNAMENT_V1',artifact_hash,artifact_bytes,manifest
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_STORE_FAILED';
    END IF;

    changed_artifact := jsonb_set(artifact,'{boards,0,par_score}','-1500'::jsonb);
    changed_bytes := convert_to(changed_artifact::text,'UTF8');
    changed_hash := encode(public.digest(changed_bytes,'sha256'),'hex');
    changed_manifest := jsonb_set(
        jsonb_set(manifest,'{artifact_sha256}',to_jsonb(changed_hash)),
        '{artifact_bytes}',to_jsonb(octet_length(changed_bytes))
    );
    BEGIN
        PERFORM autopilot.store_ibf_structured_artifact(
            task_row.task_id,'sql-ibf-artifact-worker',claimed.lease_epoch,
            'IBF_STRUCTURED_TOURNAMENT_V1',changed_hash,changed_bytes,changed_manifest
        );
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_CONFLICT_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_IBF_ARTIFACT_IDEMPOTENCY_CONFLICT%' THEN RAISE; END IF;
    END;

    IF (SELECT count(*) FROM autopilot.ibf_structured_artifact
         WHERE task_id=task_row.task_id AND retained
           AND content_sha256=artifact_hash AND content_bytes=artifact_bytes
           AND manifest_json=manifest) <> 1
       OR has_table_privilege('autopilot_runtime','autopilot.ibf_structured_artifact','SELECT')
       OR has_table_privilege('autopilot_runtime','autopilot.ibf_structured_artifact','INSERT')
       OR has_table_privilege('autopilot_runtime','autopilot.ibf_structured_artifact','UPDATE')
       OR has_table_privilege('autopilot_runtime','autopilot.ibf_structured_artifact','DELETE') THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_ARTIFACT_RETENTION_GATE_FAILED';
    END IF;
END $$;

ROLLBACK;
