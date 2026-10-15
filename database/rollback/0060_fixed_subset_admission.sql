\set ON_ERROR_STOP on
-- UNAPPLIED CANDIDATE: safe empty-state deactivation, not restoration of unsafe
-- legacy enqueue/worker grants. This intentionally retains tables, permanent
-- guards, role definitions and migration history for diagnosis/forward repair.
-- Once any approval/observation/reservation/admission exists, refuse automatic
-- reversal: preserve evidence and perform a separately reviewed forward fix.
BEGIN;
SET LOCAL lock_timeout = '5s';
LOCK TABLE video_queue.batch,video_queue.job,video_queue.job_event,
    video_queue.subset_approval,video_queue.source_observation,
    video_queue.subset_admission,video_queue.source_reservation IN ACCESS EXCLUSIVE MODE;
DO $rollback$
BEGIN
    IF EXISTS (SELECT FROM video_queue.subset_approval)
       OR EXISTS (SELECT FROM video_queue.source_observation)
       OR EXISTS (SELECT FROM video_queue.subset_admission)
       OR EXISTS (SELECT FROM video_queue.source_reservation) THEN
        RAISE EXCEPTION 'SUBSET_NONEMPTY_ROLLBACK_REFUSED';
    END IF;
END $rollback$;
REVOKE EXECUTE ON FUNCTION video_queue.approve_subset(uuid,text,jsonb,timestamptz),
    video_queue.set_subset_runtime(uuid,boolean),video_queue.revoke_subset_approval(uuid) FROM video_subset_approver;
REVOKE EXECUTE ON FUNCTION video_queue.observe_subset(uuid,uuid,text,jsonb,timestamptz,uuid,uuid,uuid,integer,text) FROM video_subset_collector;
REVOKE EXECUTE ON FUNCTION video_queue.admit_approved_subset(uuid,text,text,text,uuid),
    video_queue.recover_subset_admission(text,text,text) FROM video_subset_admitter;
REVOKE EXECUTE ON FUNCTION video_queue.claim_job(text,integer,text,text),
    video_queue.subset_claim_job(uuid,text,integer,text,text),
    video_queue.authorize_subset_attempt(uuid,uuid,text,uuid),video_queue.heartbeat_subset_job(uuid,uuid,text,integer),
    video_queue.retry_subset_job(uuid,uuid,text,text,integer),video_queue.finish_subset_job(uuid,uuid,text,text,jsonb,uuid,text) FROM video_subset_worker_v2;
DO $acl$
BEGIN
    IF EXISTS (
        SELECT FROM pg_roles r CROSS JOIN pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE r.rolname IN ('video_subset_approver','video_subset_collector',
                            'video_subset_admitter','video_subset_worker_v2')
          AND n.nspname='video_queue'
          AND p.proname IN ('approve_subset','set_subset_runtime','revoke_subset_approval',
                            'observe_subset','admit_approved_subset','recover_subset_admission',
                            'claim_job','subset_claim_job','authorize_subset_attempt',
                            'heartbeat_subset_job','retry_subset_job','finish_subset_job')
          AND has_function_privilege(r.oid,p.oid,'EXECUTE')) THEN
        RAISE EXCEPTION 'SUBSET_ROLLBACK_EXECUTE_REMAINS';
    END IF;
END $acl$;
COMMIT;
