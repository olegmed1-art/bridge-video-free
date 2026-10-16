\set ON_ERROR_STOP on
BEGIN;
DO $test$
DECLARE before_count bigint; current_count bigint; r record;
BEGIN
 SELECT count(*) INTO before_count FROM autopilot.role_dispatch_outbox;
 SELECT (SELECT count(*) FROM autopilot.role_dispatch_active)
      + (SELECT count(*) FROM autopilot.role_dispatch_archive) INTO current_count;
 IF before_count<>current_count THEN RAISE EXCEPTION 'ACTIVE_ARCHIVE_PARTITION_INVALID'; END IF;
 SELECT * INTO STRICT r FROM autopilot.mailbox_rotation_readiness();
 IF r.used_dispatches<>(SELECT count(*) FROM autopilot.role_dispatch_active WHERE mailbox_pr=r.mailbox_pr)
    OR r.remaining<>r.max_dispatches-r.used_dispatches
    OR r.readiness NOT IN ('NORMAL','NEAR_CAPACITY','CAPACITY_REACHED') THEN
   RAISE EXCEPTION 'ACTIVE_QUEUE_READINESS_INVALID';
 END IF;
 IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='autopilot.role_dispatch_outbox'::regclass
                AND tgname='role_dispatch_mailbox_capacity_guard' AND (tgtype & 2)=0)
    OR NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='autopilot.role_dispatch_outbox'::regclass
                   AND tgname='role_dispatch_history_guard') THEN
   RAISE EXCEPTION 'ACTIVE_QUEUE_GUARDS_MISSING';
 END IF;
 IF has_table_privilege('autopilot_runtime','autopilot.role_dispatch_active','SELECT')
    OR has_table_privilege('autopilot_callback','autopilot.role_dispatch_archive','SELECT')
    OR has_function_privilege('public','autopilot.role_dispatch_queue_available(integer)','EXECUTE') THEN
   RAISE EXCEPTION 'ACTIVE_QUEUE_PRIVILEGE_EXPANSION';
 END IF;
 IF NOT EXISTS (SELECT FROM public.autopilot_operational_health_signal
                WHERE signal_key='autopilot_transport_history' AND severity IN ('ok','warning')) THEN
   RAISE EXCEPTION 'TRANSPORT_HISTORY_SIGNAL_MISSING';
 END IF;
END $test$;
ROLLBACK;
