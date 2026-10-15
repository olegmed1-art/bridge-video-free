\set ON_ERROR_STOP on
-- Disposable loopback video_rehearsal after 0060, 0061 and 0062 proposals.
-- Connect as video_subset_worker_v2 only, with no public writer role.
-- Supply -v admission_id=... -v job_id=... -v lease_token=...
-- -v worker_key=... -v attempt_count=... -v source_fingerprint=...
-- -v master_json_text='...' -v ai_done_drive_id=...
-- -v ai_done_json_text='...'. The base fixture must
-- have a current approver decision and consumed ATTEMPT source observation.
-- Separate owner-side tests must advance lease/approval expiry and confirm
-- expiry denial. An approver must perform revocation in a separate session;
-- this queue-only script never impersonates that role. It rolls back its row.
\if :{?admission_id}
\else
\echo admission_id is required
\quit 2
\endif
\if :{?job_id}
\else
\echo job_id is required
\quit 2
\endif
\if :{?lease_token}
\else
\echo lease_token is required
\quit 2
\endif
\if :{?worker_key}
\else
\echo worker_key is required
\quit 2
\endif
\if :{?attempt_count}
\else
\echo attempt_count is required
\quit 2
\endif
\if :{?source_fingerprint}
\else
\echo source_fingerprint is required
\quit 2
\endif
\if :{?master_json_text}
\else
\echo master_json_text is required
\quit 2
\endif
\if :{?ai_done_drive_id}
\else
\echo ai_done_drive_id is required
\quit 2
\endif
\if :{?ai_done_json_text}
\else
\echo ai_done_json_text is required
\quit 2
\endif

BEGIN;
DO $scope$
DECLARE v_table text;
BEGIN
    IF current_database() <> 'video_rehearsal'
       OR inet_server_addr() IS DISTINCT FROM '127.0.0.1'::inet
       OR NOT pg_has_role(session_user,'video_subset_worker_v2','MEMBER')
       OR pg_has_role(session_user,'bridge_school_worker','MEMBER')
       OR pg_has_role(session_user,'video_subset_approver','MEMBER') THEN
        RAISE EXCEPTION 'ISOLATED_ONE_JOB_QUEUE_LOGIN_REQUIRED';
    END IF;
    FOREACH v_table IN ARRAY ARRAY[
        'public.changeset','public.ingestion_run','public.learning_interaction',
        'public.episode','public.decision','public.asset','public.media_asset',
        'public.asset_location','public.source','public.source_asset',
        'public.ingestion_item','public.transcript','public.transcript_segment',
        'public.analysis_run','public.analysis_run_input','public.evidence',
        'public.evidence_link','public.artifact','public.artifact_version',
        'public.analysis_run_output','public.domain_event','public.outbox_message',
        'public.source_identity','public.run_checkpoint_event',
        'public.analysis_candidate','video_queue.one_job_private_result_v1',
        'video_queue.one_job_private_result_attempt_v1'
    ] LOOP
      IF has_table_privilege(session_user,v_table,'INSERT,UPDATE,DELETE')
         OR has_any_column_privilege(session_user,v_table,'INSERT,UPDATE') THEN
        RAISE EXCEPTION 'ONE_JOB_DIRECT_DML_PRESENT';
      END IF;
    END LOOP;
    IF has_function_privilege(session_user,
         'video_queue.finish_one_job_staging_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)',
         'EXECUTE') THEN
        RAISE EXCEPTION 'ONE_JOB_PUBLIC_CANDIDATE_FINISH_STILL_GRANTED';
    END IF;
END $scope$;

CREATE TEMP TABLE one_job_private_fixture ON COMMIT DROP AS
SELECT :'admission_id'::uuid AS admission_id,
       :'job_id'::uuid AS job_id,
       :'lease_token'::uuid AS lease_token,
       :'worker_key'::text AS worker_key,
       :'attempt_count'::integer AS attempt_count,
       :'source_fingerprint'::text AS source_fingerprint,
       gen_random_uuid() AS receipt_id,
       :'master_json_text'::text AS master_json_text,
       :'ai_done_drive_id'::text AS ai_done_drive_id,
       :'ai_done_json_text'::text AS ai_done_json_text;

DO $negative$
DECLARE
    v one_job_private_fixture%ROWTYPE;
    v_base jsonb;
    v_case text;
    v_rejected boolean;
    v_changed_done jsonb;
    v_changed_master text;
BEGIN
    SELECT * INTO STRICT v FROM one_job_private_fixture;
    -- The exact committed candidate must pass before negatives count.
    v_base := video_queue.stage_one_job_private_result_v1(
        v.admission_id,v.job_id,v.lease_token,v.worker_key,v.attempt_count,
        v.source_fingerprint,v.receipt_id,v.master_json_text,
        v.ai_done_drive_id,v.ai_done_json_text);
    IF v_base->>'status' IS DISTINCT FROM 'PRIVATE_RESULT_STAGED'
       OR v_base->>'receipt_id' IS DISTINCT FROM v.receipt_id::text THEN
        RAISE EXCEPTION 'ONE_JOB_PRIVATE_BASE_FIXTURE_FAILED';
    END IF;
    v_changed_done := v.ai_done_json_text::jsonb ||
        jsonb_build_object('synthetic_replay_change',true);
    v_changed_master := jsonb_set(v.master_json_text::jsonb,
        '{source,driveId}',to_jsonb('different-source-id'::text))::text;
    FOREACH v_case IN ARRAY ARRAY[
        'cross_job','wrong_lease','wrong_attempt','wrong_source',
        'changed_master_source','changed_bundle_same_receipt',
        'same_bundle_new_receipt'
    ] LOOP
        v_rejected := false;
        BEGIN
            PERFORM video_queue.stage_one_job_private_result_v1(
                v.admission_id,
                CASE WHEN v_case='cross_job' THEN gen_random_uuid() ELSE v.job_id END,
                CASE WHEN v_case='wrong_lease' THEN gen_random_uuid() ELSE v.lease_token END,
                v.worker_key,
                CASE WHEN v_case='wrong_attempt' THEN v.attempt_count+1 ELSE v.attempt_count END,
                CASE WHEN v_case='wrong_source' THEN
                    repeat(CASE WHEN v.source_fingerprint=repeat('f',64) THEN 'e' ELSE 'f' END,64)
                     ELSE v.source_fingerprint END,
                CASE WHEN v_case='same_bundle_new_receipt' THEN gen_random_uuid() ELSE v.receipt_id END,
                CASE WHEN v_case='changed_master_source' THEN v_changed_master
                     ELSE v.master_json_text END,
                v.ai_done_drive_id,
                CASE WHEN v_case='changed_bundle_same_receipt' THEN v_changed_done::text
                     ELSE v.ai_done_json_text END);
        EXCEPTION WHEN OTHERS THEN
            v_rejected := true;
        END;
        IF NOT v_rejected THEN
            RAISE EXCEPTION 'ONE_JOB_PRIVATE_NEGATIVE_ACCEPTED: %',v_case;
        END IF;
    END LOOP;
END $negative$;
ROLLBACK;
