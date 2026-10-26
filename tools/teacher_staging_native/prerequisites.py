"""Test-harness schema prerequisites and finite, value-free diagnostics.

No DSN/provider/issuer, SQL execution at import, server error text, query text,
parameters, credentials or runtime rows are emitted by this module.
"""
from pathlib import Path
import hashlib,json,re
HERE=Path(__file__).resolve().parent
TABLES=frozenset(('algorithm algorithm_version school analysis_run analysis_run_input analysis_run_output artifact artifact_version asset asset_location changeset decision domain_event episode evidence evidence_link ingestion_item ingestion_run learning_interaction media_asset outbox_message source source_asset transcript transcript_segment source_identity analysis_candidate').split())
STATES={'42703':'MISSING_COLUMN','42P01':'MISSING_TABLE','42883':'MISSING_FUNCTION','42501':'PRIVILEGE_PREREQUISITE','23503':'FOREIGN_KEY_PREREQUISITE','55P03':'LOCK_TIMEOUT','25P01':'TRANSACTION_REQUIRED'}
CASE_CODES=frozenset('AMBIGUOUS_K_W_ACCEPTED AUTO_LOCK_ACCEPTED BAD_HASH_SELECTOR_ACCEPTED BINARY_DRIVER_PIN BOUNDED_SYNTHETIC_FIXTURE_ROWS CONCURRENT_FENCE_NOT_HELD CPYTHON_PIN DISTINCT_REAL_BACKENDS EXACT_FOREIGN_RUN_REFUSAL EXACT_METADATA_REFUSAL EXACT_SOURCE_READBACK EXPECTED_BOUNDED_LOCK_TIMEOUT EXPLICIT_IDEMPOTENT_TEST FIXTURE_ALGORITHM FIXTURE_PREIMAGE_NOT_RESTORED FIXTURE_ROW_WAS_DELETED FOREIGN_RUN_RECEIPT_ACCEPTED FOREIGN_SCHOOL_ACCEPTED FRESH_FIXTURE_REQUIRED FULL_CHAIN FUTURE_CI_ONLY GENUINE_OR_CANON_ROWS IDEMPOTENCY_GROWTH INDEPENDENT_COMMITTED_STATE INTENDED_ABORT_STAGE_AND_ROLLBACK_ACK INTENDED_SUPPRESSED_ACK_MUST_BE_UNKNOWN ISOLATION_OR_IDENTITY_GUARD LEGACY_STAGING_READ LOCK_REQUIRES_TRANSACTION LOCK_TEST_UNBOUNDED METADATA_DRIFT_ACCEPTED NATIVE_INTERVAL_MUTATION_NOT_STORED NATIVE_PROJECTION NATIVE_SCOPE_PROJECTION_MISMATCH NATIVE_SELECTOR NATIVE_TEXT_MUTATION_NOT_STORED NEGATIVE_READ_ACCEPTED NO_DB_ENV_FALLBACK NO_GENUINE_PROVIDER_HOLD OLD_SCOPE_ACCEPTED_W OLD_SCOPE_W_REFUSAL PARTIAL_WRITE_SURVIVED PINNED_SQL_WRAPPER RESTORE_METADATA_PREIMAGE ROLLBACK_ACK_NOT_CONFIRMED ROLLBACK_REWROTE_ROWS SELECTOR_EXCEPTION STAGING_HOLD STALE_BINDING UNEXPECTED_REFUSAL EXACT_NATIVE_TARGET SOCKET_ONLY_FINAL_POSTMASTER HOST_SERVER_IDENTITY_MISMATCH NATIVE_TARGET_CHANGED FIXTURE_META_REGISTRATION WRITER_SCHEMA_PREREQUISITE_MISSING SCHEMA_CONTRACT_INVALID CATALOG_RESPONSE_OUTSIDE_ALLOWLIST'.split())
CATALOG_SQL="""SELECT n.nspname,c.relname,a.attname
 FROM pg_catalog.pg_attribute a
 JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p')
   AND c.relname=ANY(%s::text[]) AND a.attname=ANY(%s::text[])
   AND a.attnum>0 AND NOT a.attisdropped LIMIT 1025"""
def valid_name(x):return type(x) is str and re.fullmatch('[a-z_][a-z_0-9]{0,62}',x) is not None
def validate_contract(contract):
    if type(contract) is not dict or contract.get('source_only') is not True or contract.get('version')!=1:raise ValueError('SCHEMA_CONTRACT_INVALID')
    required=contract.get('required_columns')
    if type(required) is not dict or set(required)!={'public.'+t for t in TABLES}:raise ValueError('SCHEMA_CONTRACT_INVALID')
    for cols in required.values():
        if type(cols) is not list or not cols or not all(valid_name(c) for c in cols) or cols!=sorted(set(cols)):raise ValueError('SCHEMA_CONTRACT_INVALID')
    return required
def load_contract():
    contract=json.loads((HERE/'WRITER_SCHEMA_CONTRACT.json').read_bytes());validate_contract(contract)
    pins=contract.get('writer_module_sha256',{})
    expected={'database/video_result_persistence.py','database/source_identity_persistence.py','database/video_candidate_persistence.py'}
    if set(pins)!=expected:raise ValueError('SCHEMA_CONTRACT_INVALID')
    for path,h in pins.items():
        if hashlib.sha256((HERE/'source/vendor'/path).read_bytes()).hexdigest()!=h:raise ValueError('SCHEMA_CONTRACT_INVALID')
    return contract
class PrerequisiteMissing(RuntimeError):
    def __init__(self,diagnostic):
        super().__init__('WRITER_SCHEMA_PREREQUISITE_MISSING');self.diagnostic=diagnostic
def validate_writer_prerequisites(connection,contract):
    required=validate_contract(contract);columns=sorted(set().union(*map(set,required.values())))
    observed=connection.execute(CATALOG_SQL,(sorted(TABLES),columns)).fetchall()
    if len(observed)>1024:raise ValueError('CATALOG_RESPONSE_OUTSIDE_ALLOWLIST')
    present=set()
    for row in observed:
        if (type(row) not in (tuple,list) or len(row)!=3 or row[0]!='public' or row[1] not in TABLES or row[2] not in columns):raise ValueError('CATALOG_RESPONSE_OUTSIDE_ALLOWLIST')
        present.add(('public.'+row[1],row[2]))
    missing=[dict(table=t,columns=[c for c in cols if (t,c) not in present]) for t,cols in sorted(required.items())]
    missing=[r for r in missing if r['columns']]
    if missing:
        count=sum(len(r['columns']) for r in missing)
        bounded=[dict(table=r['table'],columns=r['columns'][:8]) for r in missing[:8]]
        diagnostic=dict(code='WRITER_SCHEMA_PREREQUISITE_MISSING',source='CATALOG_ALLOWLIST',missing=bounded,
            missing_total_columns=count,details_truncated=(len(missing)>8 or any(len(r['columns'])>8 for r in missing)),source_only=True)
        raise PrerequisiteMissing(diagnostic)
    return dict(status='PASS_WRITER_SCHEMA_PREREQUISITES',tables=len(required),required_columns=sum(map(len,required.values())),source_only=True,genuine_checks=0)
def safe_sql_diagnostic(exc,contract):
    required=validate_contract(contract)
    try:state=getattr(exc,'sqlstate',None)
    except BaseException:state=None
    if type(state) is not str or state not in STATES:return None
    table='UNKNOWN';column='UNKNOWN'
    try:
        diag=getattr(exc,'diag',None);t=getattr(diag,'table_name',None);c=getattr(diag,'column_name',None)
        if t in TABLES:table='public.'+t
        allowed=required[table] if table in required else set().union(*map(set,required.values()))
        if c in allowed:column=c
    except BaseException:pass
    return dict(code='WRITER_SQL_PREREQUISITE_ERROR',sqlstate=state,category=STATES[state],table=table,column=column,source_only=True)
def safe_failure_code(exc):
    if isinstance(exc,PrerequisiteMissing):return 'WRITER_SCHEMA_PREREQUISITE_MISSING'
    if isinstance(exc,(AssertionError,ValueError)) and exc.args and type(exc.args[0]) is str:
        code=exc.args[0].split(':',1)[0]
        if code in CASE_CODES:return code
    return 'NATIVE_CASE_FAILED'
def safe_failure_type(exc):
    if isinstance(exc,PrerequisiteMissing):return 'PrerequisiteMissing'
    if type(exc) in (AssertionError,ValueError,RuntimeError):return type(exc).__name__
    return 'NATIVE_EXCEPTION'
def writer_failure_summary(result):
    if type(result) is not dict:return None
    statuses={'ROLLED_BACK','COMMITTED_AND_READ_BACK','COMMITTED_READBACK_FAILED','UNKNOWN_COMMIT_OR_ROLLBACK','SYNTHETIC_COMMIT_READBACK_CHECK_PASSED'}
    outcomes={'ROLLED_BACK','COMMITTED','UNKNOWN'}
    return dict(status=result.get('status') if result.get('status') in statuses else 'UNKNOWN',
        transaction_outcome=result.get('transaction_outcome') if result.get('transaction_outcome') in outcomes else 'UNKNOWN',
        automatic_retry_allowed=result.get('automatic_retry_allowed') is True)
