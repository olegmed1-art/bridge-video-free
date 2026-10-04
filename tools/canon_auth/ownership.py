"""All42 declared IDs checked under the caller's transaction and advisory lock."""
import json
from .resident_preflight import Refused
from .launch_contract import require

KEYS = {
 "public.source": ("source_id",), "ai.decision_position": ("position_id",),
 "public.knowledge_item": ("knowledge_item_id",), "public.knowledge_version": ("knowledge_version_id",),
 "public.knowledge_version_source": ("knowledge_version_id", "source_id", "relation_type"),
 "bidding.rule": ("rule_id",), "bidding.rule_test": ("rule_test_id",),
 "bidding.rule_test_run": ("rule_test_run_id",), "bidding.ingestion_run": ("ingestion_run_id",),
 "bidding.ingestion_event": ("ingestion_event_id",),
 "public.canon_activation": ("canon_activation_id",),
 "bidding.runtime_activation": ("runtime_activation_id",)}
EXTRA = {
 "bidding.ingestion_run": ("started_at", "status"),
 "bidding.rule_test": ("enabled",),
 "public.canon_activation": ("status", "valid_from", "valid_to"),
 "bidding.runtime_activation": ("status", "valid_from", "valid_to")}
ACTIVATION = ("public.canon_activation", "bidding.runtime_activation")
SIZES = {"baseline": 2, "initial": 32, "revoke": 1, "reactivate": 5, "emergency": 2}


def declared_values(spec):
    values = dict(spec["values"])
    if spec["table"] == "public.knowledge_version_source":
        # Original compiler omits this column and uses the reviewed 0010 default.
        # Inventory includes the FULL primary key without changing rendered SQL/P.
        values.setdefault("relation_type", "derived_from")
    return values


def key_predicate(table):
    return " AND ".join(key + ("=%s::pg_catalog.text" if key == "relation_type"
                              else "=%s::pg_catalog.uuid") for key in KEYS[table])


def rows(conn, compiled, *, lock=True):
    declared = compiled["declared_rows"]
    require(len(declared) == 42 and set(r["table"] for r in declared) == set(KEYS),
            "fixed_inventory_required")
    found = {}
    for index, spec in enumerate(declared):
        table, values = spec["table"], declared_values(spec)
        require(spec["birth"] in SIZES and all(k in values for k in KEYS[table]),
                "fixed_inventory_required")
        columns = list(dict.fromkeys((*values, *EXTRA.get(table, ()))))
        where = key_predicate(table)
        query = ("SELECT pg_catalog.to_jsonb(owned) FROM (SELECT " + ",".join(columns) +
                 " FROM " + table + " WHERE " + where + (" FOR UPDATE" if lock else "") + ") owned")
        result = conn.execute(query, tuple(values[key] for key in KEYS[table])).fetchone()
        if result is None:
            continue
        actual = result[0]
        # Compare PostgreSQL typed values; JSON/text/UUID/integer casts are not guessed.
        if table == "public.knowledge_version":
            values.update(authority_class="school_canon", review_status="reviewed")
        if table == "bidding.rule":
            values["lifecycle_status"] = "validated"
        expected = conn.execute("SELECT pg_catalog.to_jsonb(pg_catalog.jsonb_populate_record(NULL::" +
                                table + ",%s::pg_catalog.jsonb))",
                                (json.dumps(values, default=str),)).fetchone()[0]
        for key in values:
            observed = actual[key]
            if table == "bidding.ingestion_run" and key == "metadata" and spec["birth"] != "emergency":
                require(isinstance(observed, dict) and set(observed) == {"scope", "approval", "original_expiry"},
                        "owned_metadata_refused")
                observed = {k: v for k, v in observed.items() if k != "original_expiry"}
            require(observed == expected[key], "deterministic_id_ownership_refused")
        if table == "bidding.rule_test":
            require(actual["enabled"] is True, "owned_test_disabled")
        found[index] = actual
    return found


def inventory(conn, compiled, school, *, lock=True):
    found = rows(conn, compiled, lock=lock)
    groups = {birth: {i for i, r in enumerate(compiled["declared_rows"]) if r["birth"] == birth}
              for birth in SIZES}
    require({k: len(v) for k, v in groups.items()} == SIZES, "fixed_inventory_required")
    present = set(found)
    normal, prefix, state = set(), set(), None
    states = [("absent", ()), ("baseline", ("baseline",)),
              ("initial", ("baseline", "initial")),
              ("revoked", ("baseline", "initial", "revoke")),
              ("reactivated", ("baseline", "initial", "revoke", "reactivate"))]
    terminal = bool(present & groups["emergency"])
    ordinary = present - groups["emergency"]
    for candidate, births in states:
        expected = set().union(*(groups[b] for b in births)) if births else set()
        if ordinary == expected:
            state = candidate
            break
    require(state is not None, "partial_or_future_state_refused")
    require(not terminal or (present & groups["emergency"] == groups["emergency"] and state != "absent"),
            "partial_emergency_refused")
    # Natural keys must not be occupied by a different primary ID.
    for spec in compiled["declared_rows"]:
        table, v = spec["table"], declared_values(spec)
        natural = (("school_id", "canonical_locator") if table == "public.source" else
                   ("school_id", "stable_key") if table in ("public.knowledge_item", "ai.decision_position") else
                   ("school_id", "rule_key") if table == "bidding.rule" else
                   ("rule_id", "test_key") if table == "bidding.rule_test" else
                   ("ingestion_run_id", "event_no") if table == "bidding.ingestion_event" else ())
        if natural:
            predicate = " AND ".join(key + "=%s" for key in natural)
            pk = key_predicate(table)
            foreign = conn.execute("SELECT EXISTS(SELECT 1 FROM " + table + " WHERE " + predicate +
                                   " AND NOT(" + pk + "))",
                                   tuple(v[k] for k in natural) + tuple(v[k] for k in KEYS[table])).fetchone()
            require(foreign == (False,), "natural_key_collision_refused")
    # Refuse extra related rows: a sibling version/source/test run/event or
    # activation must not silently change eligibility or stage history.
    relations = {"public.knowledge_version": "knowledge_item_id",
        "public.knowledge_version_source": "knowledge_version_id",
        "bidding.rule": "knowledge_version_id", "bidding.rule_test": "rule_id",
        "bidding.rule_test_run": "rule_test_id", "bidding.ingestion_event": "ingestion_run_id",
        "public.canon_activation": "knowledge_version_id", "bidding.runtime_activation": "rule_id"}
    for table, relation in relations.items():
        specs = [s for s in compiled["declared_rows"] if s["table"] == table]
        parents = sorted({str(s["values"][relation]) for s in specs})
        allowed = {tuple(str(declared_values(s)[k]) for k in KEYS[table]) for s in specs}
        observed = conn.execute("SELECT " + ",".join(KEYS[table]) + " FROM " + table +
            " WHERE " + relation + "=ANY(%s::pg_catalog.uuid[])", (parents,)).fetchall()
        require(all(tuple(str(v) for v in row) in allowed for row in observed),
                "foreign_related_row_refused")
    expiry = None
    active = 0
    for index, actual in found.items():
        spec = compiled["declared_rows"][index]
        table = spec["table"]
        if table == "bidding.ingestion_run":
            expected_status = "completed" if spec["birth"] == "emergency" or state == "reactivated" else "running"
            require(actual["status"] == expected_status, "owned_run_state_refused")
            if spec["birth"] != "emergency":
                expiry = actual["metadata"]["original_expiry"]
                verified = conn.execute("SELECT (metadata->>'original_expiry')::pg_catalog.timestamptz="
                    "started_at+interval '24 hours' FROM bidding.ingestion_run WHERE ingestion_run_id=%s",
                    (spec["values"]["ingestion_run_id"],)).fetchone()
                require(verified == (True,), "original_expiry_refused")
    for index, actual in found.items():
        spec = compiled["declared_rows"][index]
        if spec["table"] not in ACTIVATION:
            continue
        should_active = not terminal and ((state == "initial" and spec["generation"] == 1)
                                         or (state == "reactivated" and spec["generation"] == 2))
        require(actual["status"] == ("active" if should_active else "revoked"), "owned_activation_state_refused")
        if should_active:
            require(expiry is not None, "original_expiry_refused")
            key = KEYS[spec["table"]][0]
            verified = conn.execute("SELECT valid_to=%s::pg_catalog.timestamptz AND "
                "valid_from<valid_to FROM " + spec["table"] + " WHERE " + key + "=%s",
                (expiry, spec["values"][key])).fetchone()
            require(verified == (True,), "owned_activation_expiry_refused")
            active += 1
    return {"state": "emergency" if terminal else state, "prior_state": state,
            "rows": len(found), "active": active, "original_expiry": expiry}
