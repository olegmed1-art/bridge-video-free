\set ON_ERROR_STOP on
-- Run under the queue-only login against a disposable synthetic fixture that
-- an independent owner/approver session has already revoked or allowed to
-- expire. Pass expected_error=ONE_JOB_ADMISSION_HOLD for revocation,
-- ONE_JOB_LEASE_APPROVAL_HOLD for attempt-approval expiry, or
-- ONE_JOB_LEASE_LOST for lease expiry. This script never writes a public row.
\if :{?expected_error}
\else
\echo expected_error is required
\quit 2
\endif
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
BEGIN
    IF current_database() <> 'video_rehearsal'
       OR inet_server_addr() IS DISTINCT FROM '127.0.0.1'::inet
       OR NOT pg_has_role(session_user,'video_subset_worker_v2','MEMBER')
       OR pg_has_role(session_user,'bridge_school_worker','MEMBER') THEN
        RAISE EXCEPTION 'ISOLATED_ONE_JOB_QUEUE_LOGIN_REQUIRED';
    END IF;
END $scope$;
CREATE TEMP TABLE one_job_closed_fixture ON COMMIT DROP AS
SELECT :'expected_error'::text AS expected_error,
       :'admission_id'::uuid AS admission_id,
       :'job_id'::uuid AS job_id,
       :'lease_token'::uuid AS lease_token,
       :'worker_key'::text AS worker_key,
       :'attempt_count'::integer AS attempt_count,
       :'source_fingerprint'::text AS source_fingerprint,
       :'master_json_text'::text AS master_json_text,
       :'ai_done_drive_id'::text AS ai_done_drive_id,
       :'ai_done_json_text'::text AS ai_done_json_text;
DO $negative$
DECLARE v one_job_closed_fixture%ROWTYPE; v_error text;
BEGIN
    SELECT * INTO STRICT v FROM one_job_closed_fixture;
    BEGIN
        PERFORM video_queue.stage_one_job_private_result_v1(
            v.admission_id,v.job_id,v.lease_token,v.worker_key,v.attempt_count,
            v.source_fingerprint,gen_random_uuid(),v.master_json_text,
            v.ai_done_drive_id,v.ai_done_json_text);
    EXCEPTION WHEN OTHERS THEN
        v_error := SQLERRM;
    END;
    IF v_error IS DISTINCT FROM v.expected_error THEN
        RAISE EXCEPTION 'ONE_JOB_CLOSED_SCOPE_WRONG_RESULT: expected %, got %',
            v.expected_error,v_error;
    END IF;
END $negative$;
ROLLBACK;
