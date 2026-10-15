"""Build fixed, reviewable Neon payloads locally; contains no connector calls."""
import hashlib
import json
import re
from pathlib import Path
import build_recovery as recovery

ROOT = Path(__file__).resolve().parent
PINNED = {
    "0059_universal_video_terminal_size_guard": "be1275d293a6aa1a27d5e41b0d2f725179fd3e6b56dd849fe8f4d87c64819a08",
    "0060_fixed_subset_admission": "cfd5bf6088761dcc1d38a4d0febb6fb24f3d4f396fd67ca08568ff44994c44bc",
}
TEMP_GRANT = "GRANT video_subset_owner TO CURRENT_USER WITH ADMIN FALSE, INHERIT TRUE, SET FALSE GRANTED BY CURRENT_USER"
TEMP_REVOKE = "REVOKE video_subset_owner FROM CURRENT_USER GRANTED BY CURRENT_USER RESTRICT"
FUNCTION_CAPS = {
    "approve_subset": "video_subset_approver", "set_subset_runtime": "video_subset_approver",
    "revoke_subset_approval": "video_subset_approver", "observe_subset": "video_subset_collector",
    "admit_approved_subset": "video_subset_admitter", "recover_subset_admission": "video_subset_admitter",
    **{name: "video_subset_worker_v2" for name in ("claim_job", "subset_claim_job", "authorize_subset_attempt",
       "heartbeat_subset_job", "retry_subset_job", "finish_subset_job")},
}
OWNER_ONLY = ("subset_json", "subset_sha", "subset_shape", "subset_capability", "subset_disabled",
              "subset_validate_contract", "subset_observed_sources", "subset_build_binding",
              "subset_assert_final", "subset_guard", "subset_deferred_guard",
              "subset_worker_eligible", "subset_lock_lease")


def function_acl_matrix(*, recovered=False):
    """Exact direct ACL tuples, including unknown grantees, grantors and GO."""
    names = sorted(set(FUNCTION_CAPS) | set(OWNER_ONLY))
    if recovered:
        names.remove("claim_job")
    values = ",".join("(" + recovery.quote(n) + ")" for n in names)
    caps = " UNION ALL SELECT p.oid,c.role_name::regrole::oid,p.proowner,'EXECUTE'::text,false FROM owned p JOIN (VALUES " + ",".join(
        "(" + recovery.quote(n) + "," + recovery.quote(r) + ")" for n, r in FUNCTION_CAPS.items()) + ") c(name,role_name) ON c.name=p.proname"
    return f"""(WITH names(name) AS (VALUES {values}), owned AS (
 SELECT p.* FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='video_queue' AND p.proowner='video_subset_owner'::regrole),
 expected AS (SELECT oid,proowner AS grantee,proowner AS grantor,'EXECUTE'::text AS privilege_type,false AS is_grantable FROM owned
 {'' if recovered else caps}), actual AS (
 SELECT p.oid,a.grantee,a.grantor,a.privilege_type,a.is_grantable FROM owned p,
 LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a)
 SELECT (SELECT count(*) FROM owned)={len(names)}
 AND NOT EXISTS(SELECT FROM names LEFT JOIN owned ON owned.proname=names.name GROUP BY names.name HAVING count(owned.oid)<>1)
 AND NOT EXISTS((SELECT * FROM actual EXCEPT SELECT * FROM expected) UNION ALL
                (SELECT * FROM expected EXCEPT SELECT * FROM actual)))"""


def function_default_acl_safe():
    """No inherited EXECUTE recipient for future queue functions."""
    return """EXISTS(SELECT FROM pg_default_acl d
      WHERE d.defaclrole='video_subset_owner'::regrole AND d.defaclnamespace=0
        AND d.defaclobjtype='f' AND NOT EXISTS (
          SELECT FROM aclexplode(d.defaclacl) a
          WHERE a.grantee<>d.defaclrole OR a.grantor<>d.defaclrole
             OR a.privilege_type<>'EXECUTE' OR a.is_grantable))
    AND NOT EXISTS(SELECT FROM pg_default_acl d
      CROSS JOIN LATERAL aclexplode(d.defaclacl) a
      WHERE d.defaclrole IN (session_user::regrole,'video_subset_owner'::regrole)
        AND d.defaclobjtype='f'
        AND (d.defaclnamespace=0 OR d.defaclnamespace='video_queue'::regnamespace)
        AND (a.grantee<>d.defaclrole OR a.grantor<>d.defaclrole
             OR a.privilege_type<>'EXECUTE' OR a.is_grantable))"""


def split_sql(text):
    """Lexical split for the two SHA-pinned PG files, not arbitrary SQL routing."""
    out, start, i = [], 0, 0
    while i < len(text):
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end + 1
        elif text.startswith("/*", i):
            depth, i = 1, i + 2
            while depth:
                if i >= len(text):
                    raise ValueError("unterminated comment")
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif text[i] in ("'", '"'):
            delimiter = text[i]
            escape = delimiter == "'" and i > 0 and text[i-1] in "eE" and (i < 2 or not text[i-2].isalnum())
            i += 1
            while True:
                if i >= len(text):
                    raise ValueError("unterminated quote")
                if escape and text[i] == "\\":
                    i += 2
                elif text[i] == delimiter:
                    i += 1
                    if i < len(text) and text[i] == delimiter:
                        i += 1
                    else:
                        break
                else:
                    i += 1
        elif text[i] == "$" and (match := re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", text[i:])):
            tag = match.group()
            end = text.find(tag, i + len(tag))
            if end < 0:
                raise ValueError("unterminated dollar quote")
            i = end + len(tag)
        elif text[i] == ";":
            out.append(text[start:i+1]); start, i = i + 1, i + 1
        else:
            i += 1
    if text[start:].strip():
        out.append(text[start:])
    return out


def bare(statement):
    return re.sub(r"(?m)^\s*--[^\n]*\n", "", statement).strip().rstrip(";").strip()


def migration_statements(key):
    directory = "candidates" if key == "0060_fixed_subset_admission" else "migrations"
    raw = (ROOT / "source/database" / directory / (key + ".sql")).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINNED[key]:
        raise ValueError("migration checksum changed")
    text = raw.decode()
    prefix = "\\set ON_ERROR_STOP on\n"
    if not text.startswith(prefix):
        raise ValueError("unexpected client directive")
    statements = split_sql(text[len(prefix):])
    if bare(statements[0]) != "BEGIN" or bare(statements[-1]) != "COMMIT":
        raise ValueError("unexpected transaction envelope")
    body = statements[1:-1]
    if any(re.match(r"(?i)^(BEGIN|COMMIT|ROLLBACK|START TRANSACTION)\b", bare(s)) for s in body):
        raise ValueError("internal transaction control")
    return body


def require(condition, label):
    return f"DO $assert$ BEGIN IF NOT coalesce(({condition}),false) THEN RAISE EXCEPTION '{label}'; END IF; END $assert$"


def catalog_digest():
    # Scoped semantic drift guard, not a complete PostgreSQL catalog backup.
    # Temporary current-user self-grant is excluded ONLY after a separate exact
    # assertion; its absence before/after the envelope is independently required.
    return """(SELECT md5(jsonb_build_object(
 'schema',(SELECT to_jsonb(n) FROM pg_namespace n WHERE n.nspname='video_queue'),
 'relations',(SELECT jsonb_agg(jsonb_build_array(c.oid,c.relname,c.relkind,c.relowner,c.relacl,c.relrowsecurity,c.relforcerowsecurity,c.relpersistence,c.reloptions) ORDER BY c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue'),
 'columns',(SELECT jsonb_agg(jsonb_build_array(a.attrelid,a.attnum,a.attname,a.atttypid,a.atttypmod,a.attnotnull,a.attidentity,a.attgenerated,a.attisdropped,a.attacl) ORDER BY a.attrelid,a.attnum) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue' AND a.attnum>0),
 'functions',(SELECT jsonb_agg(jsonb_build_array(p.oid,p.proowner,p.proacl,p.proconfig,pg_get_functiondef(p.oid)) ORDER BY p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='video_queue'),
 'triggers',(SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue'),
 'constraints',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.oid) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='video_queue'),
 'defaults',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.oid) FROM pg_default_acl d WHERE d.defaclrole=session_user::regrole),
 'roles',(SELECT jsonb_agg(jsonb_build_array(r.oid,r.rolname,r.rolcanlogin,r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls,r.rolinherit,r.rolconfig) ORDER BY r.oid) FROM pg_roles r WHERE r.rolname LIKE 'video_subset_%' OR r.rolname IN ('neondb_owner','bridge_school_app','bridge_school_worker','bridge_school_reader','bridge_school_app_principal','bridge_school_worker_principal')),
 'memberships',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.oid) FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member WHERE (r.rolname LIKE 'video_subset_%' OR m.rolname IN ('neondb_owner','bridge_school_app','bridge_school_worker','bridge_school_reader','bridge_school_app_principal','bridge_school_worker_principal')) AND NOT(r.rolname='video_subset_owner' AND a.member=current_user::regrole AND a.grantor=current_user::regrole))
 )::text))"""


def self_grant_absent():
    return "NOT EXISTS(SELECT FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid WHERE r.rolname='video_subset_owner' AND a.member=current_user::regrole AND a.grantor=current_user::regrole)"


def prefix():
    target = recovery.target()
    destination = " AND ".join([
        "current_database()=" + recovery.quote(target["database_name"]),
        *[f"current_setting('neon.{k}',true)={recovery.quote(target[k])}"
          for k in ("project_id", "branch_id", "endpoint_id")]])
    return ["SET LOCAL lock_timeout='5s'", "SET LOCAL statement_timeout='60s'",
            "SET LOCAL transaction_timeout='5min'", "SET LOCAL idle_in_transaction_session_timeout='30s'",
            "SET LOCAL TimeZone='UTC'", "SET LOCAL DateStyle='ISO, YMD'", "SET LOCAL extra_float_digits=3",
            "SET LOCAL search_path=pg_catalog,public", "SET LOCAL standard_conforming_strings=on",
            "SELECT pg_advisory_xact_lock(hashtext(current_database()),hashtext('bridge_school_schema_migrations'))",
            require(destination + " AND session_user='neondb_owner' AND current_user=session_user AND current_setting('server_version_num')::int BETWEEN 180000 AND 189999 AND current_setting('createrole_self_grant')=''", "RECOVERY_TARGET_OR_OWNER"),
            require("NOT EXISTS(SELECT FROM pg_prepared_xacts WHERE database=current_database())", "RECOVERY_PREPARED_HOLD"),
            require(self_grant_absent(), "RECOVERY_UNEXPECTED_OWNER_MEMBERSHIP")]


def inert_subset():
    return """NOT EXISTS(SELECT FROM video_queue.subset_approval) AND
 NOT EXISTS(SELECT FROM video_queue.source_observation) AND NOT EXISTS(SELECT FROM video_queue.subset_admission) AND
 NOT EXISTS(SELECT FROM video_queue.source_reservation WHERE admission_request_key IS NOT NULL OR historical_job_ids IS NULL) AND
 NOT EXISTS(SELECT FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid WHERE r.rolname LIKE 'video_subset_%'
  AND NOT(a.member=current_user::regrole AND ((a.admin_option AND NOT a.inherit_option AND NOT a.set_option)
   OR (r.rolname='video_subset_owner' AND a.grantor=current_user::regrole AND NOT a.admin_option AND a.inherit_option AND NOT a.set_option))))"""


def reservation_map():
    return """NOT EXISTS(
 SELECT FROM (SELECT 'google_drive:'||source_file_id AS source_identity,array_agg(job_id ORDER BY job_id) AS ids FROM video_queue.job GROUP BY source_file_id) j
 FULL JOIN video_queue.source_reservation r USING(source_identity)
 WHERE j.source_identity IS NULL OR r.source_identity IS NULL OR j.ids IS DISTINCT FROM r.historical_job_ids)"""


def drain():
    return "NOT EXISTS(SELECT FROM video_queue.job WHERE status IN ('LEASED','QUEUED','PENDING_CANARY')) AND NOT EXISTS(SELECT FROM video_queue.batch WHERE status IN ('QUEUED_CANARY','RUNNING'))"


def history_matches(additions=None):
    registry = json.loads((ROOT / 'catalog-baseline.json').read_text())[0]['registry']
    expected = {row['migration_key']: row['checksum'] for row in registry}
    expected.update(additions or {})
    values = ','.join('('+recovery.quote(k)+','+recovery.quote(v)+')' for k,v in sorted(expected.items()))
    return f"NOT EXISTS(SELECT FROM (VALUES {values}) e(k,c) FULL JOIN public.schema_migration m ON m.migration_key=e.k WHERE e.k IS NULL OR m.migration_key IS NULL OR e.c IS DISTINCT FROM m.checksum)"


def install_payload():
    baseline = recovery.projection(json.loads((ROOT / "catalog-baseline.json").read_text()))
    fingerprint_file = ROOT / "semantic-catalog-baseline.json"
    if not fingerprint_file.exists():
        raise ValueError("exact pre-install semantic catalog baseline is missing")
    fingerprint = json.loads(fingerprint_file.read_text())
    if not re.fullmatch(r"[0-9a-f]{32}", fingerprint["catalog_md5"]):
        raise ValueError("invalid catalog baseline")
    queries = prefix()
    queries += ["LOCK TABLE video_queue.batch,video_queue.job,video_queue.job_event IN ACCESS EXCLUSIVE MODE",
                require(f"{catalog_digest()}={recovery.quote(fingerprint['catalog_md5'])}", "INSTALL_SEMANTIC_CATALOG_DRIFT"),
                require(recovery.legacy_catalog_sql(baseline), "INSTALL_LEGACY_BASELINE_DRIFT"),
                require(drain(), "INSTALL_NOT_DRAINED"),
                require("NOT EXISTS(SELECT FROM pg_roles WHERE rolname LIKE 'video_subset_%') AND NOT EXISTS(SELECT FROM public.schema_migration WHERE migration_key IN ('0059_universal_video_terminal_size_guard','0060_fixed_subset_admission','repair-0060-legacy-recovery-v1'))", "INSTALL_ALREADY_CHANGED"),
                "CREATE TEMP TABLE uv_receipt (data jsonb) ON COMMIT DROP"]
    # Exact registry inventory is rechecked, not bootstrapped.
    registry = json.loads((ROOT / "catalog-baseline.json").read_text())[0]["registry"]
    values = ",".join("(" + recovery.quote(r['migration_key']) + "," + recovery.quote(r['checksum']) + ")" for r in registry)
    queries.append(require(f"NOT EXISTS(SELECT FROM (VALUES {values}) expected(k,c) FULL JOIN public.schema_migration m ON m.migration_key=expected.k WHERE expected.k IS NULL OR m.migration_key IS NULL OR expected.c IS DISTINCT FROM m.checksum)", "INSTALL_HISTORY_DRIFT"))
    old_digest = recovery.data_digest_sql().replace(",\n'source_reservation',\n(SELECT jsonb_build_object('count',count(*),'sha256',encode(public.digest(convert_to(coalesce(jsonb_agg(to_jsonb(t) ORDER BY source_identity)::text,'[]'),'UTF8'),'sha256'),'hex')) FROM video_queue.source_reservation t)", "")
    assert "source_reservation" not in old_digest
    queries += [f"INSERT INTO pg_temp.uv_receipt SELECT jsonb_build_object('before_old_data',{old_digest})"]
    for key in PINNED:
        queries += migration_statements(key)
        queries += [f"UPDATE public.schema_migration SET checksum={recovery.quote(PINNED[key])} WHERE migration_key={recovery.quote(key)} AND checksum IS NULL",
                    require(f"(SELECT checksum={recovery.quote(PINNED[key])} FROM public.schema_migration WHERE migration_key={recovery.quote(key)})", "INSTALL_CHECKSUM")]
    managed_test = (ROOT / "source/database/tests/041_universal_video_queue.sql").read_text()
    managed_acl = managed_test[managed_test.index("DO $managed_acl$"):managed_test.index("END $managed_acl$;") + len("END $managed_acl$;")]
    queries += [managed_acl, require(f"{function_acl_matrix()} AND {function_default_acl_safe()}", "INSTALL_EXACT_FUNCTION_ACL"), require("NOT EXISTS(SELECT FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue' AND a.attacl IS NOT NULL)", "INSTALL_COLUMN_ACL"),
                require("NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a WHERE n.nspname='video_queue' AND c.relname IN ('subset_approval','source_observation','subset_admission','source_reservation') AND (c.relowner<>'video_subset_owner'::regrole OR a.grantee<>c.relowner))", "INSTALL_NEW_TABLE_OWNER_OR_ACL"),
                TEMP_GRANT, require(inert_subset(), "INSTALL_RUNTIME_NOT_INERT"), require(reservation_map(), "INSTALL_RESERVATION_MAP"),
                require(f"{old_digest}=(SELECT data->'before_old_data' FROM pg_temp.uv_receipt)", "INSTALL_OLD_ROWS_CHANGED"),
                f"UPDATE pg_temp.uv_receipt SET data=data||jsonb_build_object('data_digest',{recovery.data_digest_sql()})", TEMP_REVOKE,
                require(self_grant_absent(), "INSTALL_TEMP_GRANT_REMAINS"),
                f"UPDATE pg_temp.uv_receipt SET data=data||jsonb_build_object('catalog_md5',{catalog_digest()},'target',{recovery.quote(recovery.target()['branch_id'])},'state','INSTALLED_RUNTIME_HOLD')",
                "SELECT data AS receipt FROM pg_temp.uv_receipt"]
    return {**{k: v for k, v in recovery.target().items() if k != 'endpoint_id'}, "sql_statements": queries}


def verify_payload(receipt):
    """Same transient grant as recovery; no persistent reader/owner membership."""
    # Reuse all recovery admission checks, then stop before the recovery body.
    candidate = recover_payload(receipt)
    body = split_sql((ROOT / "recovery" / (recovery.KEY + ".sql")).read_text())
    index = candidate["sql_statements"].index(body[0])
    candidate["sql_statements"] = candidate["sql_statements"][:index] + [TEMP_REVOKE,
        require(self_grant_absent(), "VERIFIER_TEMP_GRANT_REMAINS"),
        "SELECT 'COMMITTED_INSTALL_RECEIPT_MATCHES' AS verification"]
    return candidate


def recover_payload(receipt):
    if set(receipt) != {"before_old_data", "data_digest", "catalog_md5", "target", "state"}:
        raise ValueError("invalid receipt shape")
    if receipt["state"] != "INSTALLED_RUNTIME_HOLD" or receipt["target"] != recovery.target()["branch_id"] or not re.fullmatch(r"[0-9a-f]{32}", receipt["catalog_md5"]):
        raise ValueError("wrong receipt state/target")
    raw = (ROOT / "recovery" / (recovery.KEY + ".sql")).read_bytes()
    expected_hash = json.loads((ROOT / "model-result.json").read_text())["recovery_sha256"]
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("recovery source changed")
    baseline = recovery.projection(json.loads((ROOT / "catalog-baseline.json").read_text()))
    data = recovery.quote(json.dumps(receipt["data_digest"], sort_keys=True)) + "::jsonb"
    queries = prefix() + ["LOCK TABLE video_queue.batch,video_queue.job,video_queue.job_event IN ACCESS EXCLUSIVE MODE",
                         require(f"{catalog_digest()}={recovery.quote(receipt['catalog_md5'])}", "RECOVERY_CATALOG_DRIFT"),
                         TEMP_GRANT,
                         "LOCK TABLE video_queue.subset_approval,video_queue.source_observation,video_queue.subset_admission,video_queue.source_reservation IN ACCESS EXCLUSIVE MODE",
                         require(f"{function_acl_matrix()} AND {function_default_acl_safe()}", "RECOVERY_EXACT_FUNCTION_ACL"),
                         require(inert_subset(), "RECOVERY_RUNTIME_USED"), require(drain(), "RECOVERY_NOT_DRAINED"),
                         require(reservation_map(), "RECOVERY_RESERVATION_DRIFT"),
                         require(f"{recovery.data_digest_sql()}={data}", "RECOVERY_DATA_DRIFT"),
                         "CREATE TEMP TABLE uv_receipt (data jsonb) ON COMMIT DROP"]
    for key, checksum in PINNED.items():
        queries.append(require(f"(SELECT checksum={recovery.quote(checksum)} FROM public.schema_migration WHERE migration_key={recovery.quote(key)})", "RECOVERY_MIGRATION_HISTORY"))
    queries.append(require(history_matches(PINNED), 'RECOVERY_FULL_HISTORY_DRIFT'))
    queries += split_sql(raw.decode())
    queries += [require(recovery.legacy_catalog_sql(baseline), "RECOVERY_LEGACY_CATALOG_MISMATCH"),
                require(f"{function_acl_matrix(recovered=True)} AND {function_default_acl_safe()}", "RECOVERY_SUBSET_NOT_DISABLED"),
                require(f"{recovery.data_digest_sql()}={data}", "RECOVERY_HISTORY_CHANGED"),
                f"INSERT INTO public.schema_migration(migration_key,checksum) VALUES({recovery.quote(recovery.KEY)},{recovery.quote(expected_hash)})",
                f"INSERT INTO pg_temp.uv_receipt SELECT jsonb_build_object('state','LEGACY_CAPABILITIES_RECOVERED_SUBSET_DISABLED','data_digest',{recovery.data_digest_sql()},'legacy_catalog_matches',true)",
                TEMP_REVOKE, require(self_grant_absent(), "RECOVERY_TEMP_GRANT_REMAINS"),
                "SELECT data AS receipt FROM pg_temp.uv_receipt"]
    return {**{k: v for k, v in recovery.target().items() if k != 'endpoint_id'}, "sql_statements": queries}


def main():
    payload = install_payload()
    text = json.dumps(payload, indent=2)
    (ROOT / "install-payload.json").write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"status": "BUILT_NOT_EXECUTED", "install_statement_count": len(payload["sql_statements"]),
                      "payload_sha256": hashlib.sha256(text.encode()).hexdigest(),
                      "recovery_requires": "exact successful install receipt; no guessed defaults",
                      "execution_allowed": False}))


if __name__ == "__main__":
    main()
