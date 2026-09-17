#!/usr/bin/env bash
set -euo pipefail

DATABASE_URL=${1:?DATABASE_URL required}
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)

psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE
  work_id uuid; probe record; materialized record; claimed record;
  dispatch record; outbox record; repair_id uuid; event_time text; ack jsonb;
BEGIN
  SELECT work_item_id INTO work_id FROM autopilot.register_universal_work_item(
    'publication-concurrency-340','AUTOPILOT','REPOSITORY_REPAIR','Concurrent permit fixture.',
    1150,0,'{"expected_changed_files":["tests/test_example.py"]}'::jsonb,
    NULL,'database-test','SQL_TEST');
  SELECT * INTO probe FROM autopilot.claim_project_work_probe('publication-concurrency-planner-340',60);
  SELECT * INTO materialized FROM autopilot.materialize_project_work_probe(
    work_id,'publication-concurrency-planner-340',probe.lease_epoch,true,repeat('a',40));
  SELECT * INTO claimed FROM autopilot.claim_next_task('publication-concurrency-worker-340',60);
  SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
    claimed.task_id,'publication-concurrency-worker-340',claimed.lease_epoch);
  SELECT * INTO outbox FROM autopilot.claim_role_dispatch_outbox_v2('publication-concurrency-publisher-340',60);
  PERFORM autopilot.mark_role_dispatch_published(
    dispatch.dispatch_id,'publication-concurrency-publisher-340',outbox.claim_epoch,9903420,repeat('b',64));
  repair_id:=autopilot.materialize_role_repair(
    materialized.task_id,'BOUNDED_DEFECT','Concurrent permit fixture.');
  SELECT * INTO claimed FROM autopilot.claim_next_task('publication-concurrency-repair-340',60);
  IF claimed.task_id IS DISTINCT FROM repair_id THEN RAISE EXCEPTION 'CONCURRENCY_REPAIR_NOT_CLAIMED'; END IF;
  SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
    claimed.task_id,'publication-concurrency-repair-340',claimed.lease_epoch);
  SELECT * INTO outbox FROM autopilot.claim_role_dispatch_outbox_v2('publication-concurrency-publisher-340',60);
  PERFORM autopilot.mark_role_dispatch_published(
    dispatch.dispatch_id,'publication-concurrency-publisher-340',outbox.claim_epoch,9903421,repeat('b',64));
  event_time:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"');
  ack:=jsonb_build_object(
    'dispatch_id',dispatch.dispatch_id::text,'dispatch_pr',9903421,
    'dispatch_epoch',dispatch.dispatch_epoch,'role',dispatch.role,
    'task_fingerprint',dispatch.task_fingerprint,'target_pr',dispatch.target_pr,
    'expected_head_sha',dispatch.expected_head_sha,'mode','REPAIR',
    'command_pr',1150,'command_comment_id',99034210,'command_created_at',event_time,
    'ack_reaction_id',99034211,'ack_created_at',event_time);
  PERFORM * FROM autopilot.accept_role_dispatch_codex_ack(
    'github-codex-ack:99034211',repeat('d',64),true,'olegmed1-art/bridge-video-free',1150,
    'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,
    'chatgpt-codex-connector[bot]',199175422,ack);
END $$;
SQL

# Prove 0339 rollback serializes with a receipt writer.  A blocker on the
# backup table holds the rollback after its receipt emptiness check.  With the
# required ACCESS EXCLUSIVE receipt lock, the concurrent insert must remain
# blocked until rollback drops the table and then fail; it must never commit
# evidence that the rollback can subsequently erase.
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 \
  -f "$repo_root/database/rollbacks/0340_autopilot_publication_permit_issuer.sql" \
  >/dev/null
{
  echo 'BEGIN;'
  echo 'LOCK TABLE autopilot.migration_0339_function_backup IN ACCESS EXCLUSIVE MODE;'
  echo 'SELECT pg_sleep(2);'
  echo 'COMMIT;'
} | PGAPPNAME=pr1546-0339-blocker psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 >"$tmp/0339-blocker.out" 2>"$tmp/0339-blocker.err" &
blocker_pid=$!
blocker_locked=false
for _ in {1..100}; do
  if [[ "$(psql "$DATABASE_URL" -XAt -c "SELECT EXISTS(
      SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING(pid)
      WHERE l.relation='autopilot.migration_0339_function_backup'::regclass
        AND l.mode='AccessExclusiveLock' AND l.granted
        AND a.application_name='pr1546-0339-blocker');")" == t ]]; then
    blocker_locked=true
    break
  fi
  sleep 0.05
done
if [[ "$blocker_locked" != true ]]; then
  echo '0339 blocker failed to acquire backup-table lock' >&2
  exit 1
fi
set +e
PGAPPNAME=pr1546-0339-rollback psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 \
  -f "$repo_root/database/rollbacks/0339_autopilot_native_cli_receipts.sql" \
  >"$tmp/0339-rollback.out" 2>"$tmp/0339-rollback.err" &
rollback_0339_pid=$!
receipt_locked=false
for _ in {1..100}; do
  if [[ "$(psql "$DATABASE_URL" -XAt -c "SELECT EXISTS(
      SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING(pid)
      WHERE l.relation='autopilot.native_cli_receipt'::regclass
        AND l.mode='AccessExclusiveLock' AND l.granted
        AND a.application_name='pr1546-0339-rollback');")" == t ]]; then
    receipt_locked=true
    break
  fi
  sleep 0.05
done
if [[ "$receipt_locked" != true ]]; then
  echo '0339 rollback failed to acquire receipt-table lock before writer' >&2
  kill "$rollback_0339_pid" "$blocker_pid" 2>/dev/null || true
  wait "$rollback_0339_pid" "$blocker_pid" 2>/dev/null || true
  exit 1
fi
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -c \
  "INSERT INTO autopilot.native_cli_receipt(dispatch_id,request)
   SELECT dispatch_id,jsonb_build_object('race','rollback-0339')
   FROM autopilot.role_dispatch_outbox WHERE github_dispatch_comment_id=9903421;" \
  >"$tmp/0339-insert.out" 2>"$tmp/0339-insert.err" &
insert_0339_pid=$!
wait "$blocker_pid"
blocker_rc=$?
wait "$rollback_0339_pid"
rollback_0339_rc=$?
wait "$insert_0339_pid"
insert_0339_rc=$?
set -e
if [[ $blocker_rc -ne 0 || $rollback_0339_rc -ne 0 || $insert_0339_rc -eq 0 ]]; then
  echo "0339 rollback/write serialization failed: blocker=$blocker_rc rollback=$rollback_0339_rc insert=$insert_0339_rc" >&2
  cat "$tmp/0339-blocker.err" "$tmp/0339-rollback.err" "$tmp/0339-insert.err" >&2
  exit 1
fi
test "$(psql "$DATABASE_URL" -XAt -c "SELECT count(*) FROM public.schema_migration WHERE migration_key='0339_autopilot_native_cli_receipts';")" = 0
test "$(psql "$DATABASE_URL" -XAt -c "SELECT to_regclass('autopilot.native_cli_receipt') IS NULL;")" = t
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 \
  -f "$repo_root/database/migrations/0339_autopilot_native_cli_receipts.sql" >/dev/null
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 \
  -f "$repo_root/database/migrations/0340_autopilot_publication_permit_issuer.sql" >/dev/null

echo 'native receipt rollback/write serialization: PASS'

make_evidence() {
  local publication_id=$1 approval_id=$2 payload=$3 provenance=$4
  cat <<SQL
SELECT autopilot.issue_codex_publication_permit(
  (SELECT jsonb_build_object(
    'dispatch_id',o.dispatch_id::text,
    'dispatch_epoch',o.dispatch_epoch,
    'role',o.role,
    'task_fingerprint',o.task_fingerprint,
    'target_pr',o.target_pr,
    'expected_head_sha',o.expected_head_sha,
    'command_comment_id',o.codex_command_comment_id,
    'publication_comment_id',$publication_id::bigint,
    'approval_comment_id',$approval_id::bigint,
    'payload_sha256',repeat('$payload',64),
    'provenance_evidence_sha256',repeat('$provenance',64))
   FROM autopilot.role_dispatch_outbox o
   WHERE o.github_dispatch_comment_id=9903421),600);
SQL
}

{
  echo 'BEGIN;'
  make_evidence 99034212 99034213 e f
  echo 'SELECT pg_sleep(2);'
  echo 'COMMIT;'
} | psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 >"$tmp/a.out" 2>"$tmp/a.err" &
a_pid=$!
sleep 0.4
set +e
make_evidence 99034214 99034215 1 2 | psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 >"$tmp/b.out" 2>"$tmp/b.err"
b_rc=$?
set -e
wait "$a_pid"
if [[ $b_rc -eq 0 ]]; then
  echo 'conflicting concurrent issuer unexpectedly succeeded' >&2
  exit 1
fi
grep -F 'PUBLICATION_PERMIT_REUSE_CONFLICT' "$tmp/b.err"

# Exact replay is idempotent after the winning transaction commits.
make_evidence 99034212 99034213 e f | psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 >/dev/null

# Populated evidence must block rollback and remain readable afterwards.
set +e
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 \
  -f "$repo_root/database/rollbacks/0340_autopilot_publication_permit_issuer.sql" \
  >"$tmp/rollback.out" 2>"$tmp/rollback.err"
rollback_rc=$?
set -e
if [[ $rollback_rc -eq 0 ]]; then
  echo 'populated issuer rollback unexpectedly succeeded' >&2
  exit 1
fi
grep -F 'PUBLICATION_ISSUER_ROLLBACK_REQUIRES_RETAINED_EVIDENCE_PLAN' "$tmp/rollback.err"
test "$(psql "$DATABASE_URL" -XAt -c "SELECT count(*) FROM autopilot.codex_publication_permit;")" = 1
test "$(psql "$DATABASE_URL" -XAt -c "SELECT to_regprocedure('autopilot.issue_codex_publication_permit(jsonb,integer)') IS NOT NULL;")" = t

echo 'publication permit concurrency + retained-evidence rollback: PASS'
