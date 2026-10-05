"""Read-only owner-path attestation; no manifest approval or mutation permit."""
from dataclasses import asdict
import json
import os

from database import native_cli_permission_engine as engine
from ops.native_permission_hold_guard import EXPECTED_TARGET
from ops.oracle_autopilot_source_preflight import connection_parameters

PHASE = 'startup'


def parameters(raw):
    # Reuse strict credential parsing, then reconstruct EVERY libpq setting.
    parsed = connection_parameters(raw, EXPECTED_TARGET['session_owner'])
    return dict(host=EXPECTED_TARGET['neon']['host'], port=5432, dbname='neondb',
                user='neondb_owner', password=parsed['password'], sslmode='verify-full',
                sslrootcert='/etc/ssl/certs/ca-certificates.crt', channel_binding='require',
                gssencmode='disable', connect_timeout=10,
                application_name='native-maintenance-owner-attest', options='')


def observe(connect, raw):
    return _observe(connect, raw, candidate=False)


def candidate(connect, raw):
    """Return private candidate bytes; no self-approval via engine.prepare."""
    return _observe(connect, raw, candidate=True)


def _observe(connect, raw, *, candidate):
    global PHASE
    target = engine.Target(**{**EXPECTED_TARGET,
                             'neon': engine.NeonBinding(**EXPECTED_TARGET['neon'])})
    PHASE = 'credential_policy'
    kwargs = parameters(raw)
    PHASE = 'connection'
    with connect(**kwargs, autocommit=True) as conn:
        # Set read-only BEFORE the first transaction; all statements below read
        # or constrain this session. No execute/prepare/change or ACL writes.
        conn.read_only = True
        PHASE = 'read_only_transaction'
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='2s'")
            engine.check(conn.execute("SELECT current_setting('transaction_read_only')").fetchone()
                         == ('on',), 'READ_ONLY_REQUIRED')
            PHASE = 'identity_and_snapshot'
            state = engine.snapshot(conn, target)
            PHASE = 'dormant_state'
            engine.dormant(state)
            PHASE = 'existing_privileges'
            engine.privileges(conn, target, False)
            PHASE = 'expected_acl_scope'
            after = engine.expected_after(state)  # Validate scope without writing.
            snapshot_digest = engine.digest(state)
    report = dict(audit='NATIVE_OWNER_READ_ONLY_PASS', target=asdict(target),
                snapshot_digest=snapshot_digest, snapshot_approved=False,
                native_enabled=False, receipts=0, nonterminal_tasks=0,
                light_native_execute=0, production_mutations=False)
    if candidate:
        return report, engine.encode(dict(version=1, target=asdict(target), before=state, after=after))
    return report


def main():
    global PHASE
    from ops.native_maintenance_store_runner import source_check
    import psycopg
    engine.check(os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art', 'RERUN_ACTOR_REFUSED')
    source = os.environ.get('EXPECTED_MAIN')
    PHASE = 'source_before'
    source_check(source)
    report = observe(psycopg.connect, os.environ.get('NATIVE_OWNER_DATABASE_URL', ''))
    PHASE = 'source_after'
    source_check(source)
    print(json.dumps({**report, 'source_sha': source}, sort_keys=True))


def failure_reason(exc):
    # Inspect locally, emit only fixed categories; never serialize libpq errors.
    try:
        message = str(exc).lower()
    except BaseException:
        return 'unclassified'
    for fragment, reason in (
        ('password authentication failed', 'authentication'),
        ('could not translate host name', 'dns'),
        ('name or service not known', 'dns'),
        ('certificate', 'tls_certificate'),
        ('channel binding', 'channel_binding'),
        ('timeout', 'timeout'),
        ('connection refused', 'connection_refused'),
    ):
        if fragment in message:
            return reason
    if isinstance(exc, engine.Refused):
        return 'database_guard'
    return 'unclassified'


def entrypoint():
    try:
        main()
    except BaseException as exc:
        # Never print exception text, connection string, snapshot or private data.
        print(json.dumps({'audit': 'NATIVE_OWNER_READ_ONLY_REFUSED', 'phase': PHASE,
                          'reason': failure_reason(exc),
                          'production_mutations': False}))
        raise SystemExit(2) from None


# Separate operational observation; never an initial-install/grant candidate.
INITIALIZED_SQL = """WITH receipt_meta AS (
 SELECT r.dispatch_id,r.owner_name,r.state,
        CASE WHEN r.provider_task_id ~ '^task_[A-Za-z0-9_]{1,120}$'
          THEN r.provider_task_id END AS provider_task_id,
        r.created_at,r.submitted_at,r.completed_at,
        o.repository,o.target_pr,o.task_id,o.status AS outbox_status,
        o.delivery_contract_version,t.status AS task_status,
        CASE WHEN jsonb_typeof(r.terminal->'status')='string'
          AND r.terminal->>'status' IN ('SUCCEEDED','BLOCKED')
          THEN r.terminal->>'status' END AS terminal_status,
        CASE WHEN jsonb_typeof(r.terminal->'result_code')='string'
          AND r.terminal->>'result_code' ~ '^[A-Z][A-Z0-9_]{0,63}$'
          THEN r.terminal->>'result_code' END AS result_code,
        coalesce(
          r.state='TERMINAL'
          AND r.owner_name=%s
          AND r.provider_task_id ~ '^task_[A-Za-z0-9_]{1,120}$'
          AND r.prompt_sha256 ~ '^[0-9a-f]{64}$'
          AND r.submitted_at IS NOT NULL AND r.completed_at IS NOT NULL
          AND CASE WHEN jsonb_typeof(r.terminal)='object' THEN
            (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(r.terminal) k)
             =ARRAY['provider_evidence_sha256','result_code','status','summary','target_head_sha']
            AND jsonb_typeof(r.terminal->'status')='string'
            AND jsonb_typeof(r.terminal->'result_code')='string'
            AND jsonb_typeof(r.terminal->'provider_evidence_sha256')='string'
            AND jsonb_typeof(r.terminal->'target_head_sha')='string'
            AND jsonb_typeof(r.terminal->'summary')='string'
            AND r.terminal->>'status' IN ('SUCCEEDED','BLOCKED')
            AND r.terminal->>'result_code' ~ '^[A-Z][A-Z0-9_]{0,63}$'
            AND r.terminal->>'provider_evidence_sha256' ~ '^[0-9a-f]{64}$'
            AND r.terminal->>'target_head_sha' ~ '^[0-9a-f]{40}$'
          ELSE false END, false) AS terminal_metadata_shape_ok,
        coalesce(o.delivery_contract_version=4
          AND o.status='CALLBACK_ACCEPTED'
          AND CASE r.terminal->>'status'
            WHEN 'SUCCEEDED' THEN t.status='DONE'
              AND r.terminal->>'target_head_sha'=o.expected_head_sha
            WHEN 'BLOCKED' THEN t.status='FAILED_CLOSED'
            ELSE false END,false) AS outbox_task_terminal_metadata_ok
 FROM autopilot.native_cli_receipt r
 LEFT JOIN autopilot.role_dispatch_outbox o USING(dispatch_id)
 LEFT JOIN autopilot.task t ON t.task_id=o.task_id
)
SELECT
 (SELECT count(*) FROM pg_catalog.pg_class WHERE oid=ANY(ARRAY[
  'autopilot.native_cli_config'::regclass,'autopilot.native_cli_receipt'::regclass,
  'autopilot.task'::regclass,'autopilot.role_dispatch_outbox'::regclass,
  'autopilot.project_work_item'::regclass,'assistant_lab.job'::regclass,
  'assistant_lab.control_command'::regclass])
  AND relkind='r' AND NOT relrowsecurity AND NOT relforcerowsecurity),
 (SELECT count(*) FROM autopilot.native_cli_config),
 (SELECT count(*) FROM autopilot.native_cli_config WHERE singleton IS TRUE AND enabled IS FALSE),
 (SELECT count(*) FROM autopilot.task WHERE status IS NULL OR status NOT IN
  ('OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED')),
 (SELECT count(*) FROM assistant_lab.job WHERE status IS NULL OR status NOT IN
  ('CANCELLED','COMPLETED','FAILED')),
 (SELECT count(*) FROM assistant_lab.control_command WHERE status IS NULL OR status NOT IN
  ('CANCELLED','COMPLETED','FAILED')),
 (SELECT count(*) FROM receipt_meta),
 (SELECT count(*) FROM receipt_meta WHERE state='TERMINAL'),
 (SELECT count(*) FROM receipt_meta WHERE state IS DISTINCT FROM 'TERMINAL'),
 (SELECT count(*) FROM receipt_meta WHERE NOT terminal_metadata_shape_ok),
 (SELECT count(*) FROM receipt_meta WHERE NOT outbox_task_terminal_metadata_ok),
 (SELECT count(*) FROM autopilot.project_work_item
  WHERE repository='olegmed1-art/bridge-video-free' AND target_pr=%s),
 (SELECT count(*) FROM autopilot.role_dispatch_outbox
  WHERE repository='olegmed1-art/bridge-video-free' AND target_pr=%s),
 (SELECT count(*) FROM receipt_meta
  WHERE repository='olegmed1-art/bridge-video-free' AND target_pr=%s)
"""
def initialized_counts(baseline):
    engine.check(type(baseline) is dict and set(baseline) == {
        'version', 'scope', 'expected_receipts', 'incident_target_pr', 'review_nonce'},
        'INITIALIZED_BASELINE_REQUIRED')
    engine.check(type(baseline['version']) is int and baseline['version'] == 1
                 and baseline['scope'] == 'initialized-runtime'
                 and type(baseline['expected_receipts']) is int
                 and 1 <= baseline['expected_receipts'] <= 32
                 and type(baseline['incident_target_pr']) is int
                 and 1 <= baseline['incident_target_pr'] <= 1000000
                 and type(baseline['review_nonce']) is str
                 and len(baseline['review_nonce']) == 64
                 and all(c in '0123456789abcdef' for c in baseline['review_nonce']),
                 'INITIALIZED_BASELINE_INVALID')
    count = baseline['expected_receipts']
    return (7, 1, 1, 0, 0, 0, count, count, 0, 0, 0, 0, 0, 0)


def initialized_report():
    return dict(audit='NATIVE_OWNER_INITIALIZED_READ_ONLY_PASS',
                native_enabled=False, retained_terminal_baseline_matched=True, nonterminal_tasks=0,
                snapshot_approved=False, native_initial_install_qualified=False,
                native_execution_authorized=False, historical_provenance_verified=False,
                incident_reconciled=False, replay_authorized=False,
                live_admission=False, production_mutations=False)


def observe_initialized(connect, raw, baseline):
    """Observe the reviewed retained terminal state, without permission authority.

    This checks metadata shape, not summary contents or provider authenticity.
    Existing incident containment remains unresolved; no candidate is returned.
    The host caller must retain its existing verified-runtime/full-HOLD envelope.
    """
    global PHASE
    target = engine.Target(**{**EXPECTED_TARGET,
                             'neon': engine.NeonBinding(**EXPECTED_TARGET['neon'])})
    PHASE = 'initialized_baseline'
    expected = initialized_counts(baseline)
    PHASE = 'credential_policy'
    kwargs = parameters(raw)
    PHASE = 'connection'
    with connect(**kwargs, autocommit=True) as conn:
        conn.read_only = True
        PHASE = 'read_only_transaction'
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='2s'")
            engine.check(conn.execute("SELECT current_setting('transaction_read_only')").fetchone()
                         == ('on',), 'READ_ONLY_REQUIRED')
            PHASE = 'initialized_identity'
            engine.identity(conn, target)
            PHASE = 'initialized_metadata'
            incident = baseline['incident_target_pr']
            counts = conn.execute(INITIALIZED_SQL, (target.recipient, incident, incident, incident)).fetchone()
            engine.check(type(counts) is tuple and len(counts) == len(expected)
                         and all(type(n) is int for n in counts)
                         and counts == expected, 'INITIALIZED_METADATA_GUARD')
    return initialized_report()


if __name__ == '__main__':
    entrypoint()
