-- Diagnostic only. Execute inside REPEATABLE READ, READ ONLY with a 5s timeout.
-- No row identities, task content, credentials, locks or producer mutations.
WITH observation AS MATERIALIZED (
SELECT jsonb_build_object(
  'started_at', floor(extract(epoch FROM transaction_timestamp()))::bigint,
  'identity', jsonb_build_object('database', current_database(), 'role', current_user,
    'snapshot', pg_current_snapshot()::text, 'backend_pid', pg_backend_pid()),
  'transaction_read_only', current_setting('transaction_read_only') = 'on',
  'isolation', upper(replace(current_setting('transaction_isolation'), ' ', '_')),
  'statement_timeout_ms', CASE current_setting('statement_timeout') WHEN '5s' THEN 5000 ELSE NULL END,
  'controls', (SELECT jsonb_build_object('native_enabled', n.enabled,
      'planner_enabled', p.enabled, 'autopilot_role_enabled', r.enabled,
      'autopilot_can_repair', r.can_repair)
    FROM autopilot.native_cli_config n CROSS JOIN autopilot.project_planner_state p
    CROSS JOIN autopilot.role_registry r WHERE n.singleton AND p.singleton AND r.role_id='AUTOPILOT'),
  'task_total', (SELECT count(*) FROM autopilot.task),
  'task_status_counts', (SELECT jsonb_object_agg(status, count) FROM (
    SELECT coalesce(expected.status, actual.status) AS status, coalesce(actual.count, 0) AS count
    FROM (VALUES ('NEW'),('VALIDATING'),('READY'),('RUNNING'),('WAITING_EXTERNAL'),
      ('EVALUATING'),('OWNER_REQUIRED'),('FAILED_CLOSED'),('BUDGET_STOP'),('DONE'),('CANCELLED')) expected(status)
    FULL JOIN (SELECT status,count(*) AS count FROM autopilot.task GROUP BY status) actual USING(status)
  ) counts),
  'outbox_total', (SELECT count(*) FROM autopilot.role_dispatch_outbox),
  'outbox_status_counts', (SELECT jsonb_object_agg(status, count) FROM (
    SELECT coalesce(expected.status, actual.status) AS status, coalesce(actual.count, 0) AS count
    FROM (VALUES ('PENDING'),('CLAIMED'),('RETRY'),('PUBLISHED'),('DELIVERY_FAILED'),
      ('SENT'),('CALLBACK_ACCEPTED'),('FAILED_CLOSED')) expected(status)
    FULL JOIN (SELECT status,count(*) AS count FROM autopilot.role_dispatch_outbox GROUP BY status) actual USING(status)
  ) counts),
  'obligation_counts', jsonb_build_object(
    'retained_task_leases', (SELECT count(*) FROM autopilot.task WHERE lease_owner IS NOT NULL OR lease_until IS NOT NULL),
    'retained_outbox_claims', (SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE claim_owner IS NOT NULL OR claim_until IS NOT NULL),
    'resource_leases', (SELECT count(*) FROM autopilot.resource_lease),
    'probe_leases', (SELECT count(*) FROM autopilot.project_work_item WHERE probe_lease_owner IS NOT NULL OR probe_lease_until IS NOT NULL),
    'nonterminal_native_receipts', (SELECT count(*) FROM autopilot.native_cli_receipt
      WHERE state <> 'TERMINAL' OR terminal IS NULL OR completed_at IS NULL),
    'active_work_items', (SELECT count(*) FROM autopilot.project_work_item WHERE state NOT IN ('DONE','PAUSED')),
    'legacy_backends', (SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE datname=current_database()
      AND usename LIKE 'autopilot\_%' ESCAPE '\' AND pid<>pg_backend_pid()),
    'provider_probe_inflight', (SELECT count(*) FROM autopilot.provider_circuit_state WHERE probe_in_flight)
  )
) AS value
)
SELECT value || jsonb_build_object('observed_at', floor(extract(epoch FROM clock_timestamp()))::bigint) AS observation
FROM observation;
