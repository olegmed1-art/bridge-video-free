\set ON_ERROR_STOP on
-- Disposable PostgreSQL rehearsal only, after installing the unapplied 0061
-- proposal and its reviewed owner/EXECUTE delta. Connect as a dedicated
-- approver login with video_subset_approver, no worker membership.
-- Supply exact live synthetic fixture variables:
-- -v job_id=... -v lease_token=... -v worker_key=...
-- -v attempt_count=... -v source_fingerprint=... -v expires_at=...
-- expires_at must be in the future and no later than the current lease and
-- subset approval expiries. This test never commits its approval or revoke.
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
\if :{?expires_at}
\else
\echo expires_at is required
\quit 2
\endif

BEGIN;
DO $scope$
BEGIN
    IF current_database() <> 'video_rehearsal'
       OR inet_server_addr() IS DISTINCT FROM '127.0.0.1'::inet THEN
        RAISE EXCEPTION 'SYNTHETIC_LOOPBACK_DATABASE_REQUIRED';
    END IF;
    IF NOT pg_has_role(session_user,'video_subset_approver','MEMBER')
       OR pg_has_role(session_user,'video_subset_worker_v2','MEMBER')
       OR pg_has_role(session_user,'bridge_school_worker','MEMBER') THEN
        RAISE EXCEPTION 'DEDICATED_APPROVER_LOGIN_REQUIRED';
    END IF;
    IF has_function_privilege('bridge_school_worker_principal',
           'video_queue.approve_one_job_staging_attempt_v1(uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text)',
           'EXECUTE')
       OR has_function_privilege('video_subset_worker_v2',
           'video_queue.approve_one_job_staging_attempt_v1(uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text)',
           'EXECUTE')
       OR has_table_privilege(session_user,
           'video_queue.one_job_staging_admission_v1','INSERT,UPDATE,DELETE')
       OR has_table_privilege(session_user,
           'video_queue.one_job_staging_lease_approval_v1','INSERT,UPDATE,DELETE') THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVER_ACL_TOO_BROAD';
    END IF;
END $scope$;

CREATE TEMP TABLE one_job_approver_fixture ON COMMIT DROP AS
SELECT gen_random_uuid() AS admission_id,
       gen_random_uuid() AS lease_approval_id,
       :'job_id'::uuid AS job_id,
       :'lease_token'::uuid AS lease_token,
       :'worker_key'::text AS worker_key,
       :'attempt_count'::integer AS attempt_count,
       :'source_fingerprint'::text AS source_fingerprint,
       :'expires_at'::timestamptz AS expires_at;

DO $negative$
DECLARE
    v one_job_approver_fixture%ROWTYPE;
    v_case text;
    v_rejected boolean;
BEGIN
    SELECT * INTO STRICT v FROM one_job_approver_fixture;
    -- A valid base fixture must pass before a rejected variant counts.
    PERFORM video_queue.approve_one_job_staging_attempt_v1(
        v.admission_id,v.lease_approval_id,v.job_id,v.lease_token,
        v.worker_key,v.attempt_count,v.source_fingerprint,v.expires_at,
        'synthetic-approver-negative-test');
    FOREACH v_case IN ARRAY ARRAY[
        'replay_same_attempt','wrong_job','wrong_lease_token','wrong_worker',
        'wrong_attempt','wrong_source','expired_approval'
    ] LOOP
        v_rejected := false;
        BEGIN
            PERFORM video_queue.approve_one_job_staging_attempt_v1(
                v.admission_id,gen_random_uuid(),
                CASE WHEN v_case='wrong_job' THEN gen_random_uuid() ELSE v.job_id END,
                CASE WHEN v_case='wrong_lease_token' THEN gen_random_uuid() ELSE v.lease_token END,
                CASE WHEN v_case='wrong_worker' THEN 'wrong-worker' ELSE v.worker_key END,
                CASE WHEN v_case='wrong_attempt' THEN v.attempt_count+1 ELSE v.attempt_count END,
                CASE WHEN v_case='wrong_source' THEN
                    repeat(CASE WHEN v.source_fingerprint=repeat('f',64) THEN 'e' ELSE 'f' END,64)
                     ELSE v.source_fingerprint END,
                CASE WHEN v_case='expired_approval' THEN clock_timestamp()-interval '1 second' ELSE v.expires_at END,
                'synthetic-approver-negative-test');
        EXCEPTION WHEN OTHERS THEN
            v_rejected := true;
        END;
        IF NOT v_rejected THEN
            RAISE EXCEPTION 'ONE_JOB_APPROVER_NEGATIVE_ACCEPTED: %',v_case;
        END IF;
    END LOOP;
    PERFORM video_queue.revoke_one_job_staging_v1(v.admission_id);
    v_rejected := false;
    BEGIN
        PERFORM video_queue.approve_one_job_staging_attempt_v1(
            v.admission_id,gen_random_uuid(),v.job_id,v.lease_token,
            v.worker_key,v.attempt_count,v.source_fingerprint,v.expires_at,
            'synthetic-approver-negative-test');
    EXCEPTION WHEN OTHERS THEN
        v_rejected := true;
    END;
    IF NOT v_rejected THEN
        RAISE EXCEPTION 'ONE_JOB_REVOKED_APPROVAL_ACCEPTED';
    END IF;
END $negative$;
ROLLBACK;
