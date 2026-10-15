"""Disposable-only positive 0329 rehearsal with real SQL role boundaries.

Requires a loopback maintenance-DB connection and an explicit disposable schema
template. The runner creates its own unique database before staging anything.
All inputs are fictional. Never use this on a shared PostgreSQL cluster.
"""
import os
import re
import secrets
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from bridge_school_api.knowledge import AuthorityLane, _version_query
from tests.fixtures.synthetic_video_canon_positive import (
    CAPABILITIES,
    RULE_ID,
    SCHOOL_ID,
    build_synthetic_positive_rehearsal,
)

DISPOSABLE_COMMENT_PREFIX = "canon-synthetic-disposable-v1:"
TEMPLATE_COMMENT = "canon-synthetic-schema-template-v1"
READER_CAPABILITY = "bridge_school_reader"
SCHEMA_MANIFEST = (Path(__file__).resolve().parents[2] / "tests/fixtures/"
                   "synthetic_canon_schema_pg18.json")


_CATALOG_SQL = """
WITH user_schemas AS (
  SELECT oid,nspname,nspacl,nspowner FROM pg_namespace
  WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
), entries AS (
  SELECT 'schema' AS kind,n.nspname AS name,
         jsonb_build_object('acl',n.nspacl::text,
           'owner',pg_get_userbyid(n.nspowner)) AS body
  FROM user_schemas n
  UNION ALL
  SELECT 'relation',n.nspname||'.'||c.relname,
         jsonb_build_object('kind',c.relkind,'owner',pg_get_userbyid(c.relowner),
           'persistence',c.relpersistence,
           'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
           'acl',c.relacl::text,'options',c.reloptions,
           'view',CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid) END,
           'index',CASE WHEN c.relkind IN ('i','I') THEN pg_get_indexdef(c.oid) END,
           'partition',pg_get_expr(c.relpartbound,c.oid))
  FROM pg_class c JOIN user_schemas n ON n.oid=c.relnamespace
  UNION ALL
  SELECT 'column',n.nspname||'.'||c.relname||'.'||a.attname,
         jsonb_build_object('type',format_type(a.atttypid,a.atttypmod),
           'not_null',a.attnotnull,'identity',a.attidentity,
           'generated',a.attgenerated,'acl',a.attacl::text,
           'collation',a.attcollation::regcollation::text,
           'default',pg_get_expr(d.adbin,d.adrelid))
  FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
  JOIN user_schemas n ON n.oid=c.relnamespace
  LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE a.attnum>0 AND NOT a.attisdropped
  UNION ALL
  SELECT 'function',n.nspname||'.'||p.proname||'('||
         pg_get_function_identity_arguments(p.oid)||')',
         jsonb_build_object('definition',pg_get_functiondef(p.oid),
           'owner',pg_get_userbyid(p.proowner),
           'acl',p.proacl::text,'security_definer',p.prosecdef,
           'config',p.proconfig)
  FROM pg_proc p JOIN user_schemas n ON n.oid=p.pronamespace
  WHERE p.prokind<>'a'
  UNION ALL
  SELECT 'constraint',n.nspname||'.'||
         CASE WHEN c.conrelid<>0 THEN c.conrelid::regclass::text
              ELSE c.contypid::regtype::text END||'.'||c.conname,
         jsonb_build_object('definition',pg_get_constraintdef(c.oid),
           'validated',c.convalidated,'deferrable',c.condeferrable)
  FROM pg_constraint c JOIN user_schemas n ON n.oid=c.connamespace
  UNION ALL
  SELECT 'trigger',n.nspname||'.'||c.relname||'.'||
         CASE WHEN t.tgisinternal THEN 'internal.'||co.conname||'.'||
              t.tgfoid::regprocedure::text||'.'||t.tgtype::text
              ELSE t.tgname END,
         jsonb_build_object('definition',CASE WHEN NOT t.tgisinternal
                                               THEN pg_get_triggerdef(t.oid) END,
           'enabled',t.tgenabled,'internal',t.tgisinternal,
           'function',t.tgfoid::regprocedure::text,'type',t.tgtype,
           'constraint',co.conname)
  FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
  JOIN user_schemas n ON n.oid=c.relnamespace
  LEFT JOIN pg_constraint co ON co.oid=t.tgconstraint
  UNION ALL
  SELECT 'policy',n.nspname||'.'||c.relname||'.'||p.polname,
         jsonb_build_object('cmd',p.polcmd,'roles',p.polroles::text,
           'qual',pg_get_expr(p.polqual,p.polrelid),
           'check',pg_get_expr(p.polwithcheck,p.polrelid))
  FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
  JOIN user_schemas n ON n.oid=c.relnamespace
  UNION ALL
  SELECT 'sequence',n.nspname||'.'||c.relname,
         jsonb_build_object('type',format_type(s.seqtypid,NULL),
           'start',s.seqstart,'increment',s.seqincrement,'min',s.seqmin,
           'max',s.seqmax,'cache',s.seqcache,'cycle',s.seqcycle)
  FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
  JOIN user_schemas n ON n.oid=c.relnamespace
  UNION ALL
  SELECT 'capability_role',r.rolname,
         jsonb_build_object('login',r.rolcanlogin,'inherit',r.rolinherit,
           'super',r.rolsuper,
           'createdb',r.rolcreatedb,'createrole',r.rolcreaterole,
           'replication',r.rolreplication,'bypassrls',r.rolbypassrls,
           'members',(SELECT coalesce(jsonb_agg(jsonb_build_object(
                          'name',member.rolname,'admin',m.admin_option,
                          'inherit',m.inherit_option,'set',m.set_option)
                          ORDER BY member.rolname),'[]'::jsonb)
                      FROM pg_auth_members m JOIN pg_roles member ON member.oid=m.member
                      WHERE m.roleid=r.oid AND member.rolname NOT LIKE 'canon_rehearsal_%'),
           'inherits',(SELECT coalesce(jsonb_agg(jsonb_build_object(
                           'name',granted.rolname,'admin',m.admin_option,
                           'inherit',m.inherit_option,'set',m.set_option)
                           ORDER BY granted.rolname),'[]'::jsonb)
                       FROM pg_auth_members m JOIN pg_roles granted ON granted.oid=m.roleid
                       WHERE m.member=r.oid))
  FROM pg_roles r WHERE r.rolname='bridge_school_reader'
    OR r.rolname LIKE 'bridge_school_canon_%'
)
SELECT kind,name,body::text FROM entries ORDER BY kind,name,body::text
"""


def _schema_catalog_digest(conn: psycopg.Connection) -> str:
    """Snapshot definitions and ACLs, independent of table row fingerprints."""
    rows = conn.execute(_CATALOG_SQL).fetchall()
    serialized = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_schema_manifest(conn: psycopg.Connection) -> str:
    manifest = json.loads(SCHEMA_MANIFEST.read_text(encoding="utf-8"))
    major = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    digest = _schema_catalog_digest(conn)
    if major != manifest["postgres_major"] or digest != manifest["catalog_sha256"]:
        raise ValueError("schema template DDL/ACL differs from reviewed PG18 manifest")
    return digest


def _rehearsal_principals(database_id: str) -> dict[str, str]:
    """Distinct cluster-global names for each disposable database."""
    prefix = "canon_rehearsal_" + database_id[:16] + "_"
    return {kind: prefix + kind + "_login" for kind in CAPABILITIES}


def _reader_principal(database_id: str) -> str:
    return "canon_rehearsal_" + database_id[:16] + "_reader_login"


def _validate_disposable_target(args: dict, database_id: str, server_addr: str,
                                database_name: str, comment: str | None,
                                changed_tables: list[str], rehearsal_roles: int) -> None:
    """Fail before any target write unless it still matches the cloned template."""
    if (args.get("host") != "127.0.0.1" or args.get("hostaddr") not in (None, "127.0.0.1")
            or server_addr != "127.0.0.1" or not database_name.startswith("canon_positive_")
            or database_name != args.get("dbname")):
        raise ValueError("fresh loopback canon_positive_* database required")
    if not re.fullmatch(r"[0-9a-f]{32}", database_id):
        raise ValueError("a 32-character disposable database ID is required")
    if comment != DISPOSABLE_COMMENT_PREFIX + database_id:
        raise ValueError("database lacks the matching disposable identity marker")
    if changed_tables or rehearsal_roles:
        raise ValueError("disposable rehearsal database differs from template: " +
                         ", ".join(changed_tables or ["rehearsal roles exist"]))


def _user_table_fingerprints(conn: psycopg.Connection) -> dict[str, tuple[int, str]]:
    """Include every non-system table, partition and materialized view, even seeds."""
    tables = conn.execute(
        "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind IN ('r','p','m') AND n.nspname NOT LIKE 'pg_%' "
        "AND n.nspname <> 'information_schema' ORDER BY n.nspname,c.relname"
    ).fetchall()
    fingerprints = {}
    for schema, table in tables:
        key = schema + "." + table
        fingerprints[key] = conn.execute(sql.SQL(
            "SELECT count(*), md5(coalesce(string_agg(md5(to_jsonb(t)::text), '' "
            "ORDER BY md5(to_jsonb(t)::text)), '')) FROM {} AS t"
        ).format(sql.Identifier(schema, table))).fetchone()
    return fingerprints


def _validate_admin_source(admin_dsn: str, template_name: str) -> tuple[dict, dict]:
    """Read-only authorization check before any test helper creates a database."""
    args = conninfo_to_dict(admin_dsn)
    if (args.get("host") != "127.0.0.1" or args.get("hostaddr") not in (None, "127.0.0.1")
            or args.get("dbname") != "postgres"
            or not re.fullmatch(r"canon_schema_template_[a-z0-9_]+", template_name)):
        raise ValueError("loopback postgres and canon_schema_template_* required")
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        address = admin.execute("SELECT host(inet_server_addr())").fetchone()[0]
        if address != "127.0.0.1":
            raise ValueError("actual PostgreSQL server is not loopback")
        comment = admin.execute(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=%s",
            (template_name,),
        ).fetchone()
        if comment is None or comment[0] != TEMPLATE_COMMENT:
            raise ValueError("schema template lacks disposable identity marker")
    template_args = {**args, "dbname": template_name}
    with psycopg.connect(make_conninfo(**template_args)) as template:
        template.execute("SET TRANSACTION READ ONLY")
        schema_digest = _validate_schema_manifest(template)
        baseline = _user_table_fingerprints(template)
    return args, {"rows": baseline, "schema": schema_digest}


def _create_disposable_database(admin_dsn: str, template_name: str) -> tuple[str, str, dict]:
    """Clone only an explicitly marked local schema template to a new random DB."""
    args, baseline = _validate_admin_source(admin_dsn, template_name)
    database_id = secrets.token_hex(16)
    database_name = "canon_positive_" + database_id
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} WITH TEMPLATE {}").format(
            sql.Identifier(database_name), sql.Identifier(template_name)))
        admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
            sql.Identifier(database_name)))
        admin.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
            sql.Identifier(database_name), sql.Literal(DISPOSABLE_COMMENT_PREFIX + database_id)))
    args["dbname"] = database_name
    return make_conninfo(**args), database_id, baseline


def _connect_as(owner_dsn: str, user: str, password: str):
    args = conninfo_to_dict(owner_dsn)
    args["user"] = user
    args["password"] = password
    return psycopg.connect(make_conninfo(**args))


def _rehearse_created(owner_dsn: str, database_id: str, baseline: dict) -> dict:
    args = conninfo_to_dict(owner_dsn)
    if not re.fullmatch(r"[0-9a-f]{32}", database_id):
        raise ValueError("a 32-character disposable database ID is required")
    with psycopg.connect(owner_dsn) as owner:
        owner.execute("SET TRANSACTION READ ONLY")
        if owner.execute("SHOW transaction_read_only").fetchone()[0] != "on":
            raise AssertionError("preflight transaction is not read-only")
        server_addr, database_name, comment = owner.execute(
            "SELECT host(inet_server_addr()), current_database(), "
            "shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"
        ).fetchone()
        current = _user_table_fingerprints(owner)
        changed_tables = sorted(key for key in baseline["rows"].keys() | current.keys()
                                if baseline["rows"].get(key) != current.get(key))
        if _schema_catalog_digest(owner) != baseline["schema"]:
            changed_tables.append("schema DDL/ACL")
        rehearsal_roles = owner.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)",
            (list(_rehearsal_principals(database_id).values()) +
             [_reader_principal(database_id)],),
        ).fetchone()[0]
        _validate_disposable_target(args, database_id, server_addr, database_name,
                                    comment, changed_tables, rehearsal_roles)
        migrations = owner.execute(
            "SELECT migration_key FROM public.schema_migration WHERE migration_key IN "
            "('0329_workflow_video_canon_ai_promotion','0402_video_canon_firewall_unicode_boundary')"
        ).fetchall()
        if {row[0] for row in migrations} != {
            "0329_workflow_video_canon_ai_promotion",
            "0402_video_canon_firewall_unicode_boundary",
        }:
            raise ValueError("required migrations are not installed")
    # The read-only preflight transaction is closed. This new connection starts a
    # separate write transaction on the database created in this process.
    with psycopg.connect(owner_dsn) as owner:
        if owner.execute("SHOW transaction_read_only").fetchone()[0] != "off":
            raise AssertionError("write transaction remains read-only")
        principals = _rehearsal_principals(database_id)
        reader_principal = _reader_principal(database_id)
        role_passwords = {login: secrets.token_urlsafe(32)
                          for login in (*principals.values(), reader_principal)}
        fixture = build_synthetic_positive_rehearsal("c" * 64, "b" * 64, principals)
        ids = fixture["ids"]
        for kind, login in principals.items():
            owner.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                sql.Identifier(login), sql.Literal(role_passwords[login])))
            owner.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(args["dbname"]), sql.Identifier(login)))
            owner.execute(sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(CAPABILITIES[kind]), sql.Identifier(login)
            ))
        owner.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
            sql.Identifier(reader_principal), sql.Literal(role_passwords[reader_principal])))
        owner.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(args["dbname"]), sql.Identifier(reader_principal)))
        owner.execute(sql.SQL("GRANT {} TO {}").format(
            sql.Identifier(READER_CAPABILITY), sql.Identifier(reader_principal)))
        owner.execute("INSERT INTO public.school(school_id,stable_name) VALUES (%s,%s)",
                      (SCHOOL_ID, fixture["school_claim"]["stable_name"]))
        source = fixture["source_claim"]
        owner.execute("INSERT INTO public.source(source_id,school_id,source_type,title,status) VALUES (%s,%s,%s,%s,%s)",
                      tuple(source[key] for key in ("source_id", "school_id", "source_type", "title", "status")))
        policy = fixture["source_policy_claim"]
        policy_keys = ("school_id", "source_id", "source_sha256", "video_file_id", "teacher_ids",
                       "semantic_scopes", "system_profile", "learner_level", "policy_version",
                       "authorization_evidence_sha256", "status", "valid_from")
        owner.execute(
            "INSERT INTO bidding.video_canon_source_policy(" + ",".join(policy_keys) + ") VALUES (" + ",".join(["%s"] * len(policy_keys)) + ")",
            tuple(policy[key] for key in policy_keys),
        )
        candidate = fixture["analysis_candidate_claim"]
        candidate_keys = ("analysis_candidate_id", "school_id", "candidate_type", "stable_key",
                          "input_fingerprint", "quality_status", "promotion_status", "payload",
                          "payload_hash", "method_version", "source_id")
        owner.execute(
            "INSERT INTO public.analysis_candidate(" + ",".join(candidate_keys) + ") VALUES (" + ",".join(["%s"] * len(candidate_keys)) + ")",
            tuple(Jsonb(candidate[key]) if key == "payload" else candidate[key] for key in candidate_keys),
        )
        item = fixture["knowledge_item_claim"]
        owner.execute(
            "INSERT INTO public.knowledge_item(knowledge_item_id,school_id,stable_key,knowledge_type,title,status) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (item["knowledge_item_id"], item["school_id"], item["stable_key"],
             item["knowledge_type"], "Synthetic rule, not a real lesson", item["status"]),
        )
        version = fixture["knowledge_version_claim"]
        version_keys = ("knowledge_version_id", "knowledge_item_id", "version_no", "content",
                        "authority_class", "review_status", "bidding_system_key", "agreement_scope",
                        "level_scope", "effective_from", "effective_to", "method_version", "provenance", "status")
        owner.execute(
            "INSERT INTO public.knowledge_version(" + ",".join(version_keys) + ") VALUES (" + ",".join(["%s"] * len(version_keys)) + ")",
            tuple(Jsonb(version[key]) if key in {"content", "agreement_scope", "level_scope", "provenance"}
                  else 1 if key == "version_no" else version[key] for key in version_keys),
        )
        link = fixture["knowledge_version_source_claim"]
        owner.execute(
            "INSERT INTO public.knowledge_version_source(knowledge_version_id,source_id,relation_type,source_locator) VALUES (%s,%s,%s,%s)",
            (link["knowledge_version_id"], link["source_id"], link["relation_type"], Jsonb(link["source_locator"])),
        )
        rule = fixture["rule_claim"]
        rule_keys = ("rule_id", "school_id", "knowledge_version_id", "rule_key", "rule_kind",
                     "auction_pattern", "hand_constraints", "public_context_constraints", "action",
                     "meaning", "public_inference", "alert_semantics", "forcing_semantics", "priority",
                     "specificity", "explanation", "condition_schema_version", "compiled_payload",
                     "lifecycle_status", "method_version")
        json_keys = {"auction_pattern", "hand_constraints", "public_context_constraints", "action",
                     "meaning", "public_inference", "alert_semantics", "forcing_semantics", "explanation",
                     "compiled_payload"}
        owner.execute(
            "INSERT INTO bidding.rule(" + ",".join(rule_keys) + ") VALUES (" + ",".join(["%s"] * len(rule_keys)) + ")",
            tuple(Jsonb(rule[key]) if key in json_keys else rule[key] for key in rule_keys),
        )
        for test in fixture["rule_test_claims"]:
            test_id = owner.execute(
                "INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,enabled) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING rule_test_id",
                (SCHOOL_ID, RULE_ID, test["test_key"], test["test_type"], Jsonb(test["fixture"]),
                 Jsonb(test["expected"]), test["enabled"]),
            ).fetchone()[0]
            owner.execute(
                "INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version) "
                "VALUES (%s,%s,'pass','{}'::jsonb,'synthetic-rehearsal-v1')",
                (SCHOOL_ID, test_id),
            )
        snapshot, rule_state = owner.execute(
            "SELECT bidding.current_school_canon_snapshot_sha256(%s), "
            "bidding.video_canon_rule_test_state_sha256(%s)", (SCHOOL_ID, RULE_ID)
        ).fetchone()
    # Re-seal after committing all state that the SQL gate hashes.
    fixture = build_synthetic_positive_rehearsal(snapshot, rule_state, principals)
    bundle = fixture["verification_bundle_claim"]
    with psycopg.connect(owner_dsn) as owner:
        owner.execute("SET ROLE bridge_school_canon_verifier")
        owner.execute(
            "INSERT INTO bidding.video_canon_ai_verification_bundle("
            "video_canon_ai_verification_bundle_id,school_id,analysis_candidate_id,candidate_payload_hash,"
            "verification_bundle_sha256,bundle_canonical_json,bundle_payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (bundle["video_canon_ai_verification_bundle_id"], bundle["school_id"],
             bundle["analysis_candidate_id"], bundle["candidate_payload_hash"],
             bundle["verification_bundle_sha256"], bundle["bundle_canonical_json"],
             Jsonb(bundle["bundle_payload"])),
        )
    for kind, login in principals.items():
        with _connect_as(owner_dsn, login, role_passwords[login]) as verifier:
            verifier.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(CAPABILITIES[kind])))
            for check in fixture["verification_rows"]:
                if check["execution_principal"] != login:
                    continue
                verifier.execute(
                    "INSERT INTO bidding.video_canon_ai_verification("
                    "school_id,analysis_candidate_id,video_canon_ai_verification_bundle_id,"
                    "candidate_payload_hash,verification_bundle_sha256,check_id,result,verifier_family,"
                    "verifier_version,execution_principal,assurance_level,evidence_sha256,canon_snapshot_sha256) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (SCHOOL_ID, ids["analysis_candidate_id"], ids["verification_bundle_id"],
                     fixture["candidate"]["payload_hash"], bundle["verification_bundle_sha256"],
                     check["check_id"], check["result"], check["verifier_family"], check["verifier_version"],
                     check["execution_principal"], check["assurance_level"], check["evidence_sha256"],
                     check["canon_snapshot_sha256"]),
                )
    with psycopg.connect(owner_dsn) as promoter:
        promoter.execute("SET ROLE bridge_school_canon_promoter")
        receipt_id = promoter.execute(
            "SELECT bidding.activate_ai_verified_video_canon(%s,%s,%s)",
            (ids["analysis_candidate_id"], RULE_ID, bundle["verification_bundle_sha256"]),
        ).fetchone()[0]
        replay_id = promoter.execute(
            "SELECT bidding.activate_ai_verified_video_canon(%s,%s,%s)",
            (ids["analysis_candidate_id"], RULE_ID, bundle["verification_bundle_sha256"]),
        ).fetchone()[0]
        if receipt_id != replay_id:
            raise AssertionError("promotion replay produced a different receipt")
    with _connect_as(owner_dsn, reader_principal, role_passwords[reader_principal]) as reader:
        reader.row_factory = dict_row
        reader.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(READER_CAPABILITY)))
        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        rows = reader.execute(
            _version_query(AuthorityLane.ACTIVE_SCHOOL_CANON),
            (fixture["school_claim"]["stable_name"], "school_canon",
             fixture["review_input"]["system_profile"], None, None,
             fixture["review_input"]["activation_scope"], 10, 0),
        ).fetchall()
        if len(rows) != 1 or str(rows[0]["item_id"]) != ids["knowledge_version_id"]:
            raise AssertionError("ACTIVE_SCHOOL_CANON readback did not find the synthetic version")
        activation_id = str(rows[0]["canon_activation_id"])
    return {"fixture_class": "SYNTHETIC_NON_AUTHORITATIVE", "receipt_id": str(receipt_id),
            "canon_activation_id": activation_id, "readback_rows": len(rows), "replay_same_receipt": True}


def rehearse(admin_dsn: str, template_name: str) -> dict:
    owner_dsn, database_id, baseline = _create_disposable_database(admin_dsn, template_name)
    try:
        result = _rehearse_created(owner_dsn, database_id, baseline)
    except Exception as exc:
        raise RuntimeError("disposable rehearsal retained for inspection: " +
                           conninfo_to_dict(owner_dsn)["dbname"]) from exc
    return {**result, "disposable_database": conninfo_to_dict(owner_dsn)["dbname"]}


if __name__ == "__main__":
    dsn = os.environ.get("CANON_SYNTHETIC_PG_ADMIN_DSN", "")
    if not dsn:
        raise SystemExit("CANON_SYNTHETIC_PG_ADMIN_DSN is required")
    template_name = os.environ.get("CANON_SYNTHETIC_PG_TEMPLATE", "")
    print(rehearse(dsn, template_name))
