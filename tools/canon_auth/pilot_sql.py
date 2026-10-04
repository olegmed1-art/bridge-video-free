"""Pure SQL plan compiler; no connections, env, production IDs or credentials.

The controller must verify each authenticated HTTP phase before the next stage.
Never execute this module or the disposable rehearsal as a production runner.
"""
import json
import re
from uuid import UUID, uuid5

from bridge_school_api import tournament_teacher as t
from tools.tournament_pilot.package import formal_package, position, catalog, cases, check_schema
from .vercel_validator import POSITION

NAMESPACE = UUID("c00f1fd7-befd-4b1d-bf38-16a4d9af6d84")


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=True, default=str)
    return "'" + str(value).replace("'", "''") + "'"


def insert(table, values):
    return "INSERT INTO " + table + "(" + ",".join(values) + ") VALUES (" + ",".join(literal(v) for v in values.values()) + ")"


def guard(condition, code):
    return "DO $guard$ BEGIN IF (" + condition + ") IS NOT TRUE THEN RAISE EXCEPTION '" + code + "'; END IF; END $guard$"


def plan(school_id, code_sha):
    """Return stage transactions bound to one school, fixed package and reviewed SHA."""
    school = str(UUID(str(school_id)))
    if not re.fullmatch(r"[0-9a-f]{40}", code_sha):
        raise ValueError("REVIEWED_CODE_SHA_REQUIRED")
    check_schema()
    pkg = formal_package()
    uid = lambda name: str(uuid5(NAMESPACE, school + ":" + name))
    source, run = uid("source"), uid("run")
    emergency_run = uid("emergency_run")
    url = pkg["rules"][0]["payload"]["source_rule"]["source_url"]
    approval = {"authority": "delegated_canon_steward",
                "authorization": "owner-approved bounded 24h pilot 2026-10-04",
                "decision_ids": ["TDEC-20261003-002"], "scope_key": t.SCOPE, "code_sha": code_sha}
    bindings = [{"call": e["payload"]["source_rule"]["call"], "rule_key": e["rule_key"],
                 "rule_id": uid(e["rule_key"] + ":rule"),
                 "knowledge_item_id": uid(e["rule_key"] + ":item"),
                 "knowledge_version_id": uid(e["rule_key"] + ":version")} for e in pkg["rules"]]
    prefix = ["SET LOCAL statement_timeout='15s'", "SELECT pg_advisory_xact_lock(20261004,201)"]
    not_stopped = guard("NOT EXISTS(SELECT 1 FROM bidding.ingestion_run WHERE ingestion_run_id=" +
                        literal(emergency_run) + ")", "PILOT_EMERGENCY_TERMINAL")
    def event(number, action):
        return insert("bidding.ingestion_event", dict(ingestion_event_id=uid("event:" + str(number)),
            ingestion_run_id=run, event_no=number, role_key="canon_steward", action_key=action,
            details={"bindings": bindings, "approval": approval}))
    def stage_guard(number):
        return guard("(SELECT max(event_no) FROM bidding.ingestion_event WHERE ingestion_run_id=" +
            literal(run) + ")=" + str(number), "PILOT_STAGE_MISMATCH")
    expiry = "(SELECT (metadata->>'original_expiry')::timestamptz FROM bidding.ingestion_run WHERE ingestion_run_id=" + literal(run) + ")"
    def activation(generation):
        statements = []
        for b in bindings:
            ca, ra = uid(b["call"] + ":canon:" + str(generation)), uid(b["call"] + ":runtime:" + str(generation))
            statements += [
                "INSERT INTO public.canon_activation(canon_activation_id,knowledge_version_id,scope_key,valid_from,valid_to,status,approval_provenance) VALUES (" +
                ",".join([literal(ca), literal(b["knowledge_version_id"]), literal(t.SCOPE), "now()", expiry, "'active'", literal(approval)]) + ")",
                "INSERT INTO bidding.runtime_activation(runtime_activation_id,school_id,rule_id,authority_lane,canon_activation_id,scope_key,valid_from,valid_to,status,activation_provenance) VALUES (" +
                ",".join([literal(ra), literal(school), literal(b["rule_id"]), "'school_canon'", literal(ca),
                          literal(t.SCOPE), "now()", expiry, "'active'", literal(approval)]) + ")"]
        return statements
    def revoke_owned():
        statements = []
        for table, column, kind in (("bidding.runtime_activation", "runtime_activation_id", "runtime"),
                                    ("public.canon_activation", "canon_activation_id", "canon")):
            ids = [uid(b["call"] + ":" + kind + ":" + str(g)) for b in bindings for g in (1, 2)]
            statements.append("UPDATE " + table + " SET status='revoked',valid_to=clock_timestamp() WHERE " +
                column + " IN (" + ",".join(map(literal, ids)) + ") AND status='active'")
        return statements
    baseline = prefix + [not_stopped,
        guard("EXISTS(SELECT 1 FROM public.school WHERE school_id=" + literal(school) + " AND status='active')", "PILOT_SCHOOL_MISMATCH"),
        guard("NOT EXISTS(SELECT 1 FROM ai.decision_position WHERE position_id=" + literal(POSITION) +
              " OR (school_id=" + literal(school) + " AND stable_key=" + literal(t.CANARY_KEY) + "))", "PILOT_POSITION_EXISTS"),
        guard("NOT EXISTS(SELECT 1 FROM public.source WHERE school_id=" + literal(school) +
              " AND canonical_locator=" + literal(url) + ")", "PILOT_SOURCE_EXISTS"),
        insert("public.source", dict(source_id=source, school_id=school, source_type="document",
            title="SRC-0096 approved tournament excerpts", canonical_locator=url, status="active")),
        insert("ai.decision_position", dict(position_id=POSITION, school_id=school, source_id=source,
            stable_key=t.CANARY_KEY, decision_type="BIDDING", seat="S", dealer="N", hand_pbn=t.CANARY_HAND,
            auction_json=["1NT", "PASS"], cards_played_json=[], system_us=t.PROFILE, input_status="COMPLETE"))]
    initial = prefix + [not_stopped,
        guard("EXISTS(SELECT 1 FROM public.source s JOIN public.school sc USING(school_id) WHERE s.source_id=" +
              literal(source) + " AND s.school_id=" + literal(school) + " AND s.canonical_locator=" + literal(url) +
              " AND s.status='active' AND sc.status='active')", "PILOT_SOURCE_MISMATCH"),
        guard("EXISTS(SELECT 1 FROM ai.decision_position WHERE position_id=" + literal(POSITION) +
              " AND source_id=" + literal(source) + " AND school_id=" + literal(school) +
              " AND stable_key=" + literal(t.CANARY_KEY) + " AND hand_pbn=" + literal(t.CANARY_HAND) + ")", "PILOT_POSITION_MISMATCH")]
    keys = ",".join(literal(b["rule_key"]) for b in bindings)
    initial += [
        guard("NOT EXISTS(SELECT 1 FROM bidding.rule WHERE school_id=" + literal(school) +
              " AND rule_key IN (" + keys + "))", "PILOT_RULE_EXISTS"),
        guard("NOT EXISTS(SELECT 1 FROM public.knowledge_item WHERE school_id=" + literal(school) +
              " AND stable_key IN (" + keys + "))", "PILOT_KNOWLEDGE_EXISTS"),
        insert("bidding.ingestion_run", dict(ingestion_run_id=run, school_id=school, source_id=source,
            source_manifest_key=t.VERSION, source_sha256=t.digest(pkg), metadata={"scope": t.SCOPE, "approval": approval})).replace(
                literal({"scope": t.SCOPE, "approval": approval}),
                literal({"scope": t.SCOPE, "approval": approval}) + "::jsonb||jsonb_build_object('original_expiry',now()+interval '24 hours')")]
    observations = []
    for entry, b in zip(pkg["rules"], bindings):
        payload = entry["payload"]
        r = payload["source_rule"]
        initial += [
            insert("public.knowledge_item", dict(knowledge_item_id=b["knowledge_item_id"], school_id=school,
                stable_key=b["rule_key"], knowledge_type="bidding_rule", title=r["meaning"], status="candidate")),
            insert("public.knowledge_version", dict(knowledge_version_id=b["knowledge_version_id"],
                knowledge_item_id=b["knowledge_item_id"], version_no=1, content=payload, authority_class="research_candidate",
                review_status="unreviewed", bidding_system_key=t.PROFILE, agreement_scope={"scope_key": t.SCOPE},
                method_version=t.VERSION, provenance={"payload_sha256": entry["payload_sha256"], "approval": approval}, status="candidate")),
            insert("public.knowledge_version_source", dict(knowledge_version_id=b["knowledge_version_id"], source_id=source,
                source_locator={k: r[k] for k in ("sheet_row", "rules_url", "original_excerpt", "teacher_excerpt")})),
            insert("bidding.rule", dict(rule_id=b["rule_id"], school_id=school, knowledge_version_id=b["knowledge_version_id"],
                rule_key=b["rule_key"], compiled_payload=payload, lifecycle_status="candidate", **payload["catalog"]))]
        for kind in ("positive", "negative", "boundary", "hidden_information"):
            fixtures, observed = [], []
            for name, case_kind, shape, change, expected in cases(b["call"]):
                if kind != case_kind:
                    continue
                pos = position(UUID(school), shape, change)
                row = catalog(b["call"]) | {"school_id": UUID(school)}
                result = t.evaluate(pos, b["call"], [row])
                if result["status"] != expected:
                    raise ValueError("CANDIDATE_CASE_FAILED")
                fixtures.append({"case": name, "position": pos, "expected": expected})
                observed.append({"case": name, "observed": result["status"], "expected": expected})
            test = uid(b["call"] + ":test:" + kind)
            initial += [
                insert("bidding.rule_test", dict(rule_test_id=test, school_id=school, rule_id=b["rule_id"], test_key=kind,
                    test_type=kind, fixture={"cases": fixtures}, expected={"all_cases_match": True}, method_version=t.VERSION)),
                insert("bidding.rule_test_run", dict(rule_test_run_id=uid(b["call"] + ":test_run:" + kind), school_id=school,
                    rule_test_id=test, result="pass", result_details={"evidence_class": "synthetic_candidate_evaluator",
                    "cases": observed, "code_sha": code_sha}, method_version=t.VERSION))]
            observations.extend(observed)
    if len(observations) != 18:
        raise ValueError("CANDIDATE_CASE_COUNT_MISMATCH")
    initial.append(event(1, "candidates_created"))
    for b in bindings:
        initial += [
            "UPDATE public.knowledge_version SET authority_class='school_canon',review_status='reviewed' WHERE knowledge_version_id=" + literal(b["knowledge_version_id"]),
            "UPDATE bidding.rule SET lifecycle_status='validated' WHERE rule_id=" + literal(b["rule_id"]),
            guard("bidding.rule_passes_activation_gates(" + literal(b["rule_id"]) + ")", "PILOT_ACTIVATION_GATE_FAILED")]
    initial += [event(2, "owner_approved_meaning_reviewed")]
    initial += activation(1) + [event(3, "first_activation")]
    revoke = prefix + [stage_guard(3)] + revoke_owned() + [event(4, "owned_bindings_revoked")]
    reactivate = prefix + [not_stopped, stage_guard(4), guard(expiry + ">now()", "PILOT_ORIGINAL_EXPIRY_ELAPSED")]
    reactivate += activation(2) + [event(5, "reactivated_same_expiry"),
        "UPDATE bidding.ingestion_run SET status='completed',finished_at=clock_timestamp() WHERE ingestion_run_id=" + literal(run)]
    # Emergency history uses a separate deterministic run+event: at most two extra
    # rows. A repeated request revokes again but does not append unbounded history.
    emergency_audit = [
        insert("bidding.ingestion_run", dict(ingestion_run_id=emergency_run, school_id=school, source_id=source,
            source_manifest_key=t.VERSION, source_sha256=t.digest(pkg), metadata={"scope": t.SCOPE, "operation": "emergency_revoke", "approval": approval})),
        insert("bidding.ingestion_event", dict(ingestion_event_id=uid("emergency_event"), ingestion_run_id=emergency_run,
            event_no=1, role_key="canon_steward", action_key="owned_bindings_emergency_revoked", details={"bindings": bindings})),
        "UPDATE bidding.ingestion_run SET status='completed',finished_at=clock_timestamp() WHERE ingestion_run_id=" + literal(emergency_run)]
    emergency = prefix + revoke_owned() + ["DO $audit$ BEGIN IF NOT EXISTS(SELECT 1 FROM bidding.ingestion_run WHERE ingestion_run_id=" +
        literal(emergency_run) + ") THEN " + ";".join(emergency_audit) + "; END IF; END $audit$"]
    return {"baseline": baseline, "initial": initial, "revoke": revoke, "reactivate": reactivate, "emergency": emergency,
            "ids": {"source": source, "run": run, "position": POSITION, "bindings": bindings},
            "semantic_cases": observations}
