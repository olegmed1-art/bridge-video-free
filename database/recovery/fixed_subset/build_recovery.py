"""Offline, fixed-baseline recovery candidate. Never connects to a database."""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OWNER = "neondb_owner"
SUBSET = "video_subset_owner"
CAPS = ["video_subset_approver", "video_subset_collector", "video_subset_admitter", "video_subset_worker_v2"]
CLAIM = "video_queue.claim_job(text,integer,text,text)"
KEY = "repair-0060-legacy-recovery-v1"
def target():
    """Private action-time target file; never infer a production destination."""
    value = json.loads((ROOT / "target.json").read_text())
    if set(value) != {"project_id", "branch_id", "database_name", "endpoint_id"}:
        raise ValueError("explicit target required")
    if not all(isinstance(v, str) and v for v in value.values()):
        raise ValueError("invalid target")
    return value


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def acl(value):
    return sorted(value.strip("{}").split(",")) if value else []


def projection(catalog):
    result = {}
    for row in catalog[1]["schemas"]:
        if row["nspname"] == "video_queue":
            result["schema"] = {"owner": row["owner"], "acl": acl(row["acl"])}
    for row in catalog[1]["relations"]:
        result[row["relname"]] = {"owner": row["owner"], "acl": acl(row["acl"])}
    for row in catalog[1]["functions"]:
        result[row["signature"]] = {"owner": row["owner"], "acl": acl(row["acl"]),
                                    "definition_md5": row["definition_md5"], "config": row["config"],
                                    "security_definer": row["security_definer"]}
    return result


def operations(baseline):
    """One explicit list drives SQL rendering and the privilege-state model."""
    ops = []
    for table in ("batch", "job"):
        for trigger in ("subset_guard", "subset_no_truncate", "subset_final"):
            ops.append({"kind": "drop_added_trigger", "table": table, "trigger": trigger})
    ops.append({"kind": "restore_claim", "signature": CLAIM})
    for signature in (CLAIM, "video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb)",
                      "video_queue.heartbeat_job(uuid,uuid,text,integer)",
                      "video_queue.retry_job(uuid,uuid,text,text,integer,integer)",
                      "video_queue.finish_job(uuid,uuid,text,text,jsonb,text)"):
        ops.append({"kind": "restore_function_acl", "signature": signature, "acl": baseline[signature]["acl"]})
    for table in ("batch", "job", "job_event", "job_event_event_id_seq", "schema"):
        ops.append({"kind": "remove_added_grants", "object": table})
    ops.append({"kind": "disable_subset_execute"})
    return ops


def expected_installed_model(baseline):
    state = copy.deepcopy(baseline)
    for key in ("batch", "job", "job_event"):
        state[key]["acl"].append(f"{SUBSET}=arw/{OWNER}")
    state["job_event_event_id_seq"]["acl"].append(f"{SUBSET}=rU/{OWNER}")
    state["schema"]["acl"] += [f"{role}=U/{OWNER}" for role in [SUBSET, *CAPS]]
    for op in operations(baseline):
        if op["kind"] == "restore_function_acl":
            key = op["signature"]
            state[key]["acl"] = [f"{OWNER}=X/{OWNER}", f"{SUBSET}=X/{OWNER}"]
    state[CLAIM].update(owner=SUBSET, definition_md5="0060_REPLACED_CLAIM",
                        acl=[f"{SUBSET}=X/{SUBSET}", f"video_subset_worker_v2=X/{SUBSET}"])
    state["added_triggers"] = {(table, trigger) for table in ("batch", "job")
                               for trigger in ("subset_guard", "subset_no_truncate", "subset_final")}
    state["subset_execute"] = True
    state["subset_roles_retained"] = [SUBSET, *CAPS]
    return state


def model_recover(installed, baseline, history, reservations):
    state = copy.deepcopy(installed)
    for op in operations(baseline):
        kind = op["kind"]
        if kind == "drop_added_trigger":
            state["added_triggers"].remove((op["table"], op["trigger"]))
        elif kind == "restore_claim":
            state[CLAIM].update(owner=OWNER, definition_md5=baseline[CLAIM]["definition_md5"])
        elif kind == "restore_function_acl":
            state[op["signature"]]["acl"] = copy.deepcopy(op["acl"])
        elif kind == "remove_added_grants":
            state[op["object"]]["acl"] = [entry for entry in state[op["object"]]["acl"]
                                         if entry.split("=", 1)[0] not in [SUBSET, *CAPS]]
        elif kind == "disable_subset_execute":
            state["subset_execute"] = False
    for key in baseline:
        state[key]["acl"].sort()
    return state, copy.deepcopy(history), copy.deepcopy(reservations)


def legacy_catalog_sql(baseline):
    predicates = []
    for key, row in baseline.items():
        expected_acl = "ARRAY[" + ",".join(quote(x) for x in row["acl"]) + "]::text[]"
        if key == "schema":
            query = "pg_namespace o WHERE o.nspname='video_queue'"
            owner, aclfield = "nspowner", "nspacl"
        elif key.startswith("video_queue."):
            query = f"pg_proc o WHERE o.oid={quote(key)}::regprocedure"
            owner, aclfield = "proowner", "proacl"
        else:
            query = f"pg_class o WHERE o.oid={quote('video_queue.' + key)}::regclass"
            owner, aclfield = "relowner", "relacl"
        condition = (f"pg_get_userbyid(o.{owner})={quote(row['owner'])} AND "
                     f"ARRAY(SELECT a::text FROM unnest(o.{aclfield}) a ORDER BY a::text COLLATE \"C\")={expected_acl}")
        if key.startswith("video_queue."):
            condition += f" AND md5(pg_get_functiondef(o.oid))={quote(row['definition_md5'])}"
            condition += " AND o.prosecdef=" + str(row["security_definer"]).lower()
            condition += " AND o.proconfig=ARRAY[" + ",".join(quote(v) for v in row["config"]) + "]::text[]"
        predicates.append(f"coalesce((SELECT {condition} FROM {query}),false)")
    return " AND\n".join(predicates)


def data_digest_sql():
    parts = []
    for table, order in [("batch", "batch_id"), ("job", "job_id"), ("job_event", "event_id"),
                         ("source_reservation", "source_identity")]:
        parts += [quote(table), f"(SELECT jsonb_build_object('count',count(*),'sha256',"
                  f"encode(public.digest(convert_to(coalesce(jsonb_agg(to_jsonb(t) ORDER BY {order})::text,'[]'),'UTF8'),'sha256'),'hex')) "
                  f"FROM video_queue.{table} t)"]
    parts += ["'sequence'", "(SELECT jsonb_build_array(last_value,is_called) FROM video_queue.job_event_event_id_seq)"]
    return "jsonb_build_object(" + ",\n".join(parts) + ")"


def render_recovery(baseline, claim):
    statements = []
    for op in operations(baseline):
        kind = op["kind"]
        if kind == "drop_added_trigger":
            statements.append(f"DROP TRIGGER {op['trigger']} ON video_queue.{op['table']};")
        elif kind == "restore_claim":
            statements += [claim.rstrip() + ";", f"ALTER FUNCTION {CLAIM} OWNER TO {OWNER};"]
        elif kind == "restore_function_acl":
            # Admission gate requires no unreviewed non-owner grantee. Restore
            # the observed grantors and no grant-option, not all historical users.
            sig = op["signature"]
            statements.append(f"REVOKE ALL ON FUNCTION {sig} FROM PUBLIC, {', '.join([SUBSET, *CAPS, 'bridge_school_app', 'bridge_school_worker'])};")
            for item in op["acl"]:
                grantee, right = item.split("=", 1)
                privileges, grantor = right.split("/", 1)
                assert privileges == "X" and grantor == OWNER
                if grantee != OWNER:
                    statements.append(f"GRANT EXECUTE ON FUNCTION {sig} TO {grantee} GRANTED BY CURRENT_USER;")
        elif kind == "remove_added_grants":
            obj = op["object"]
            if obj == "schema":
                statements.append(f"REVOKE ALL ON SCHEMA video_queue FROM {', '.join([SUBSET, *CAPS])};")
            else:
                typ = "SEQUENCE" if obj.endswith("_seq") else "TABLE"
                statements.append(f"REVOKE ALL ON {typ} video_queue.{obj} FROM {SUBSET};")
        elif kind == "disable_subset_execute":
            statements.append("""DO $disable$
DECLARE f record;
BEGIN
 FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p
 JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='video_queue' AND p.proowner='video_subset_owner'::regrole LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, video_subset_approver, video_subset_collector, video_subset_admitter, video_subset_worker_v2',f.signature);
 END LOOP;
END $disable$;""")
    body = "\n\n".join(statements)
    assert "DELETE FROM" not in body and "TRUNCATE TABLE" not in body
    return """-- LOCAL UNEXECUTED RECOVERY CANDIDATE. Run only in the guarded connector
-- transaction envelope; never as standalone SQL. Preserves0059 and all history.
-- The envelope provides target/receipt/drain checks, temporary owner membership,
-- before/after data digests, postconditions and registry checksum insertion.
""" + body + "\n"


def main():
    catalog = json.loads((ROOT / "catalog-baseline.json").read_text(encoding="utf-8"))
    captured = json.loads((ROOT / "claim-baseline.json").read_text(encoding="utf-8"))
    claim = (ROOT / "restore_claim_job.sql").read_text(encoding="utf-8")
    assert claim == captured["claim_definition"]
    assert hashlib.md5(claim.encode()).hexdigest() == captured["claim_definition_md5"]
    baseline = projection(catalog)
    assert baseline[CLAIM]["definition_md5"] == captured["claim_definition_md5"]
    sql = render_recovery(baseline, claim)
    folder = ROOT / "recovery"; folder.mkdir(exist_ok=True)
    (folder / (KEY + ".sql")).write_text(sql, encoding="utf-8", newline="\n")
    (ROOT / "legacy-postcondition.sql").write_text("SELECT " + legacy_catalog_sql(baseline) + " AS legacy_catalog_matches;\n", encoding="utf-8")
    (ROOT / "data-digest.sql").write_text("SELECT " + data_digest_sql() + " AS private_data_digest;\n", encoding="utf-8")
    (ROOT / "recovery-operations.json").write_text(json.dumps(operations(baseline), indent=2) + "\n", encoding="utf-8")
    sample_history = [{"job_id": "J1", "source": "S1", "status": "FAILED"},
                      {"job_id": "J2", "source": "S1", "status": "REVIEW_READY"},
                      {"job_id": "J3", "source": "S2", "status": "FAILED"}]
    reservations = {"S1": ["J1", "J2"], "S2": ["J3"]}
    result, after_history, after_reservations = model_recover(expected_installed_model(baseline), baseline,
                                                            sample_history, reservations)
    assert {key: result[key] for key in baseline} == baseline
    assert after_history == sample_history and after_reservations == reservations
    assert not result["added_triggers"] and not result["subset_execute"]
    report = {"status": "LOCAL_MODEL_PASS_SQL_NOT_RUN", "legacy_objects": len(baseline),
              "recovery_operations": len(operations(baseline)), "recovery_sha256": hashlib.sha256(sql.encode()).hexdigest(),
              "claim_md5": captured["claim_definition_md5"], "rows_and_duplicate_reservations_preserved_in_model": True,
              "limits": ["Privilege state algebra is not PostgreSQL permission/execution proof", "No SQL migrations/grants executed",
                         "Retained subset objects/roles and0059 mean full catalog is not pre0059-identical"]}
    (ROOT / "model-result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
