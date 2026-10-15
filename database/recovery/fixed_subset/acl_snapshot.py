"""Read-only, credential-free ACL/owner evidence for the fixed subset recovery.

The caller supplies an already-open psycopg connection. This module never opens a
connection, accepts a DSN, or exports SQL bodies, row data, role passwords or GUCs.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path

SCHEMA = "video_queue"
LEGACY_WRITERS = frozenset({"bridge_school_app", "bridge_school_worker"})
LEGACY_TABLES = frozenset({"batch", "job", "job_event"})
LEGACY_MUTATORS = frozenset({"enqueue_drive_batch", "claim_job", "heartbeat_job", "retry_job", "finish_job"})
SUBSET_ROLES = frozenset({
    "video_subset_owner", "video_subset_approver", "video_subset_collector",
    "video_subset_admitter", "video_subset_worker_v2",
})


def _rows(cursor, sql, params=()):
    cursor.execute(sql, params)
    names = [column.name for column in cursor.description]
    result = []
    for row in cursor.fetchall():
        if isinstance(row, Mapping):
            if set(row) != set(names):
                raise ValueError("catalog row keys differ from selected columns")
            result.append({name: row[name] for name in names})
        else:
            if len(row) != len(names):
                raise ValueError("catalog row width differs from selected columns")
            result.append(dict(zip(names, row)))
    return result


def collect(connection):
    """Collect one consistent pg_catalog snapshot in a read-only transaction."""
    if not connection.autocommit:
        raise ValueError("collector requires an idle autocommit connection")
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            result = {"format": "fixed-subset-acl-owner-v1", "schema": SCHEMA}
            result["objects"] = _rows(cur, """
                SELECT 'schema' AS kind, n.nspname AS name, pg_get_userbyid(n.nspowner) AS owner,
                       n.nspacl::text AS raw_acl, NULL::text AS definition_md5
                  FROM pg_namespace n WHERE n.nspname = %s
                UNION ALL
                SELECT CASE c.relkind WHEN 'S' THEN 'sequence' ELSE 'relation' END,
                       c.relname, pg_get_userbyid(c.relowner), c.relacl::text, NULL::text
                  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m','S')
                UNION ALL
                SELECT 'function', p.oid::regprocedure::text, pg_get_userbyid(p.proowner),
                       p.proacl::text, md5(pg_get_functiondef(p.oid))
                  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = %s
                ORDER BY kind, name
            """, (SCHEMA, SCHEMA, SCHEMA))
            result["columns"] = _rows(cur, """
                SELECT c.relname AS relation, a.attname AS column_name, a.attacl::text AS raw_acl
                  FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m')
                   AND a.attnum > 0 AND NOT a.attisdropped
                 ORDER BY c.relname, a.attnum
            """, (SCHEMA,))
            result["grants"] = _rows(cur, """
                WITH objects AS (
                  SELECT 'schema' AS kind,n.nspname AS name,n.nspowner AS owner,
                         aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) AS acl_item
                    FROM pg_namespace n WHERE n.nspname=%s
                  UNION ALL
                  SELECT CASE c.relkind WHEN 'S' THEN 'sequence' ELSE 'relation' END,
                         c.relname,c.relowner,
                         aclexplode(coalesce(c.relacl,acldefault((CASE WHEN c.relkind='S' THEN 'S' ELSE 'r' END)::"char",c.relowner)))
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','S')
                  UNION ALL
                  SELECT 'function',p.oid::regprocedure::text,p.proowner,
                         aclexplode(coalesce(p.proacl,acldefault('f',p.proowner)))
                    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s
                )
                SELECT kind,name,CASE WHEN (acl_item).grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid((acl_item).grantee) END AS grantee,
                       pg_get_userbyid((acl_item).grantor) AS grantor,(acl_item).privilege_type AS privilege,
                       (acl_item).is_grantable AS grantable
                  FROM objects ORDER BY kind,name,grantee,grantor,privilege,grantable
            """, (SCHEMA, SCHEMA, SCHEMA))
            result["column_grants"] = _rows(cur, """
                SELECT c.relname AS relation,a.attname AS column_name,
                       CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END AS grantee,
                       pg_get_userbyid(x.grantor) AS grantor,x.privilege_type AS privilege,x.is_grantable AS grantable
                  FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                  CROSS JOIN LATERAL aclexplode(a.attacl) x
                 WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped
                 ORDER BY relation,column_name,grantee,grantor,privilege,grantable
            """, (SCHEMA,))
            result["default_privileges"] = _rows(cur, """
                SELECT pg_get_userbyid(d.defaclrole) AS creator,
                       coalesce(n.nspname,'<global>') AS schema,d.defaclobjtype AS object_type,
                       d.defaclacl::text AS raw_acl
                  FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
                 WHERE d.defaclrole IN (SELECT oid FROM pg_roles WHERE rolname !~ '^pg_')
                   AND (d.defaclnamespace=0 OR n.nspname=%s)
                 ORDER BY creator,schema,object_type,raw_acl
            """, (SCHEMA,))
            result["default_grants"] = _rows(cur, """
                SELECT pg_get_userbyid(d.defaclrole) AS creator,coalesce(n.nspname,'<global>') AS schema,
                       d.defaclobjtype AS object_type,
                       CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END AS grantee,
                       pg_get_userbyid(x.grantor) AS grantor,x.privilege_type AS privilege,x.is_grantable AS grantable
                  FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
                  CROSS JOIN LATERAL aclexplode(d.defaclacl) x
                 WHERE d.defaclrole IN (SELECT oid FROM pg_roles WHERE rolname !~ '^pg_')
                   AND (d.defaclnamespace=0 OR n.nspname=%s)
                 ORDER BY creator,schema,object_type,grantee,grantor,privilege,grantable
            """, (SCHEMA,))
            result["effective_global_function_grants"] = _rows(cur, """
                SELECT r.rolname AS creator,
                       CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END AS grantee,
                       pg_get_userbyid(x.grantor) AS grantor,x.privilege_type AS privilege,x.is_grantable AS grantable
                  FROM pg_roles r
                  LEFT JOIN pg_default_acl d ON d.defaclrole=r.oid AND d.defaclnamespace=0 AND d.defaclobjtype='f'
                  CROSS JOIN LATERAL aclexplode(coalesce(d.defaclacl,acldefault('f',r.oid))) x
                 WHERE r.rolname !~ '^pg_'
                 ORDER BY creator,grantee,grantor,privilege,grantable
            """)
            result["memberships"] = _rows(cur, """
                SELECT role.rolname AS role,member.rolname AS member,
                       pg_get_userbyid(m.grantor) AS grantor,m.admin_option,m.inherit_option,m.set_option
                  FROM pg_auth_members m JOIN pg_roles role ON role.oid=m.roleid
                  JOIN pg_roles member ON member.oid=m.member
                 WHERE role.rolname !~ '^pg_' AND member.rolname !~ '^pg_'
                 ORDER BY role.rolname,member.rolname,grantor
            """)
            result["roles"] = _rows(cur, """
                SELECT rolname AS name,rolcanlogin AS can_login,rolsuper AS superuser,
                       rolcreaterole AS create_role,rolcreatedb AS create_db,
                       rolinherit AS inherit,rolbypassrls AS bypass_rls,rolreplication AS replication
                  FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname
            """)
            return result


def unsafe_writer_paths(snapshot, *, allowed_memberships=frozenset()):
    """Fail closed on broad writers and unreviewed inherited writer roles."""
    issues = []
    for grant in snapshot["grants"]:
        if grant["kind"] == "relation" and grant["name"] in LEGACY_TABLES and grant["privilege"] in {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}:
            if grant["grantee"] not in LEGACY_WRITERS | {grant["grantor"]}:
                issues.append("broad table writer: " + grant["name"] + ":" + grant["grantee"])
        if grant["kind"] == "function" and any(grant["name"].startswith(SCHEMA+"."+fn+"(") for fn in LEGACY_MUTATORS) and grant["privilege"] == "EXECUTE":
            if grant["grantee"] not in LEGACY_WRITERS | {grant["grantor"]}:
                issues.append("broad function execute: " + grant["name"] + ":" + grant["grantee"])
    for grant in snapshot["column_grants"]:
        if grant["relation"] in LEGACY_TABLES and grant["privilege"] in {"INSERT", "UPDATE"} and grant["grantee"] not in LEGACY_WRITERS | {grant["grantor"]}:
            issues.append("broad column writer: " + grant["relation"] + ":" + grant["grantee"])
    for grant in snapshot["default_grants"]:
        if grant["object_type"] == "f" and grant["privilege"] == "EXECUTE" and grant["grantee"] == "PUBLIC":
            issues.append("PUBLIC default function EXECUTE: " + grant["creator"])
        if grant["object_type"] in {"r", "S"} and grant["privilege"] in {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "USAGE"} and grant["grantee"] not in LEGACY_WRITERS | {grant["grantor"]}:
            issues.append("broad default writer: " + grant["creator"] + ":" + grant["grantee"])
    creators = {x["owner"] for x in snapshot["objects"] if x["kind"] == "function"}
    for grant in snapshot["effective_global_function_grants"]:
        if grant["creator"] in creators and grant["privilege"] == "EXECUTE" and grant["grantee"] == "PUBLIC":
            issues.append("PUBLIC default function EXECUTE: " + grant["creator"])
    for member in snapshot["memberships"]:
        edge = (member["role"], member["member"])
        if member["role"] in LEGACY_WRITERS and edge not in allowed_memberships:
            issues.append("unreviewed writer membership: " + "/".join(edge))
    return sorted(set(issues))


def assert_restored(before, after, *, allowed_memberships=frozenset()):
    """Prove every pre-install ACL/owner object and entitlement was restored."""
    schema_owners = {x["owner"] for x in before["objects"] if x["kind"] == "schema" and x["name"] == SCHEMA}
    if len(schema_owners) != 1:
        raise ValueError("exactly one pre-install video_queue schema owner is required")
    bootstrap_creator = schema_owners.pop()
    old_roles = {x["name"]: x for x in before["roles"]}
    if bootstrap_creator not in old_roles:
        raise ValueError("pre-install schema owner is absent from role snapshot")
    bootstrap_grantors = {
        edge["grantor"] for edge in before["memberships"]
        if edge["member"] == bootstrap_creator and edge["role"] in old_roles
        and edge["role"] != bootstrap_creator and edge["admin_option"] is True
        and edge["inherit_option"] is False and edge["set_option"] is False
    }
    if len(bootstrap_grantors) == 1:
        bootstrap_grantor = bootstrap_grantors.pop()
    elif not bootstrap_grantors and old_roles[bootstrap_creator]["superuser"] is True:
        bootstrap_grantor = bootstrap_creator
    else:
        raise ValueError("pre-install role graph does not identify one bootstrap grantor")
    if bootstrap_grantor not in old_roles:
        raise ValueError("bootstrap grantor is absent from pre-install role snapshot")
    for name in ("objects", "columns", "grants", "column_grants", "default_privileges", "default_grants", "effective_global_function_grants", "memberships"):
        old = before[name]
        new = after[name]
        if name in {"objects", "grants"}:
            legacy = {(x["kind"], x["name"]) for x in before["objects"]}
            new = [x for x in new if (x["kind"], x["name"]) in legacy]
        elif name in {"columns", "column_grants"}:
            legacy = {x["name"] for x in before["objects"] if x["kind"] == "relation"}
            new = [x for x in new if x["relation"] in legacy]
        elif name == "memberships":
            old_roles = {x["name"] for x in before["roles"]}
            retained = []
            for membership in new:
                if membership["role"] not in old_roles and membership["member"] in old_roles:
                    if (membership["role"] in SUBSET_ROLES
                            and membership["member"] == bootstrap_creator
                            and membership["grantor"] == bootstrap_grantor
                            and membership["admin_option"] is True
                            and membership["inherit_option"] is False
                            and membership["set_option"] is False):
                        continue
                if membership["role"] in old_roles or membership["member"] in old_roles:
                    retained.append(membership)
            new = retained
        elif name == "effective_global_function_grants":
            old_roles = {x["name"] for x in before["roles"]}
            new = [x for x in new if x["creator"] in old_roles]
        elif name in {"default_privileges", "default_grants"}:
            old_roles = {x["name"] for x in before["roles"]}
            new = [x for x in new if x["creator"] in old_roles]
        if old != new:
            raise AssertionError(name + " differs from pre-install ACL/owner snapshot")
    original_roles = {x["name"]: x for x in before["roles"]}
    current_roles = {x["name"]: x for x in after["roles"]}
    if any(current_roles.get(name) != role for name, role in original_roles.items()):
        raise AssertionError("pre-install role attributes changed")
    for name, role in current_roles.items():
        if name not in original_roles and any(role[x] for x in ("can_login", "superuser", "create_role", "create_db", "bypass_rls", "replication")):
            raise AssertionError("new subset role has elevated attributes: " + name)
    old_functions = {x["name"] for x in before["objects"] if x["kind"] == "function"}
    for grant in after["grants"]:
        if (grant["kind"] == "function" and grant["name"] not in old_functions
                and grant["privilege"] == "EXECUTE" and grant["grantee"] != grant["grantor"]):
            raise AssertionError("new subset API remains executable: " + grant["name"])
    baseline_admin_edges = {
        (edge["role"], edge["member"]) for edge in before["memberships"]
        if edge["member"] == bootstrap_creator and edge["grantor"] == bootstrap_grantor
        and edge["admin_option"] is True and edge["inherit_option"] is False
        and edge["set_option"] is False
    }
    issues = unsafe_writer_paths(
        after, allowed_memberships=allowed_memberships | baseline_admin_edges
    )
    if issues:
        raise AssertionError("unsafe writer paths: " + "; ".join(issues))


def write_snapshot(path: Path, snapshot):
    """Persist only the fixed collector schema; callers choose a local path."""
    if snapshot.get("format") != "fixed-subset-acl-owner-v1" or set(snapshot) != {
        "format", "schema", "objects", "columns", "grants", "column_grants",
        "default_privileges", "default_grants", "effective_global_function_grants", "memberships", "roles"
    }:
        raise ValueError("unexpected snapshot schema")
    path.write_text(json.dumps(snapshot, sort_keys=True, indent=2) + "\n", encoding="utf-8")
