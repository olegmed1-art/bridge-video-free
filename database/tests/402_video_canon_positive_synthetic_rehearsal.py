"""Disposable-only positive 0329 rehearsal with real SQL role boundaries.

Requires CANON_SYNTHETIC_PG_DSN pointing to a fresh local database named
canon_positive_* and CANON_SYNTHETIC_DB_ID matching that database's explicit
disposable marker. All inputs are fictional. Never use this on a shared DB.
"""
import os
import re

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
    SYNTHETIC_LOGIN_PRINCIPALS,
    build_synthetic_positive_rehearsal,
)

DISPOSABLE_COMMENT_PREFIX = "canon-synthetic-disposable-v1:"


def _validate_disposable_target(args: dict, database_id: str, server_addr: str,
                                database_name: str, comment: str | None,
                                row_counts: tuple[int, ...]) -> None:
    """Fail before any write unless this is an explicitly marked empty test DB."""
    if (args.get("host") != "127.0.0.1" or args.get("hostaddr") not in (None, "127.0.0.1")
            or server_addr != "127.0.0.1" or not database_name.startswith("canon_positive_")
            or database_name != args.get("dbname")):
        raise ValueError("fresh loopback canon_positive_* database required")
    if not re.fullmatch(r"[0-9a-f]{32}", database_id):
        raise ValueError("a 32-character disposable database ID is required")
    if comment != DISPOSABLE_COMMENT_PREFIX + database_id:
        raise ValueError("database lacks the matching disposable identity marker")
    if any(row_counts):
        raise ValueError("disposable rehearsal database is not empty")


def _connect_as(owner_dsn: str, user: str):
    args = conninfo_to_dict(owner_dsn)
    args["user"] = user
    return psycopg.connect(make_conninfo(**args))


def rehearse(owner_dsn: str, database_id: str) -> dict:
    args = conninfo_to_dict(owner_dsn)
    if not re.fullmatch(r"[0-9a-f]{32}", database_id):
        raise ValueError("a 32-character disposable database ID is required")
    with psycopg.connect(owner_dsn) as owner:
        owner.execute("SET TRANSACTION READ ONLY")
        server_addr, database_name, comment = owner.execute(
            "SELECT inet_server_addr()::text, current_database(), "
            "shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"
        ).fetchone()
        row_counts = owner.execute(
            "SELECT (SELECT count(*) FROM public.school), (SELECT count(*) FROM public.source), "
            "(SELECT count(*) FROM public.analysis_candidate), "
            "(SELECT count(*) FROM public.knowledge_item), "
            "(SELECT count(*) FROM public.knowledge_version), "
            "(SELECT count(*) FROM bidding.rule), "
            "(SELECT count(*) FROM bidding.video_canon_ai_verification), "
            "(SELECT count(*) FROM pg_roles WHERE rolname LIKE 'canon_rehearsal_%_login')"
        ).fetchone()
        _validate_disposable_target(args, database_id, server_addr, database_name,
                                    comment, row_counts)
        migrations = owner.execute(
            "SELECT migration_key FROM public.schema_migration WHERE migration_key IN "
            "('0329_workflow_video_canon_ai_promotion','0402_video_canon_firewall_unicode_boundary')"
        ).fetchall()
        if {row[0] for row in migrations} != {
            "0329_workflow_video_canon_ai_promotion",
            "0402_video_canon_firewall_unicode_boundary",
        }:
            raise ValueError("required migrations are not installed")

        fixture = build_synthetic_positive_rehearsal(
            "c" * 64, "b" * 64, SYNTHETIC_LOGIN_PRINCIPALS
        )
        ids = fixture["ids"]
        for kind, login in SYNTHETIC_LOGIN_PRINCIPALS.items():
            owner.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT").format(sql.Identifier(login)))
            owner.execute(sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(CAPABILITIES[kind]), sql.Identifier(login)
            ))
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
    fixture = build_synthetic_positive_rehearsal(snapshot, rule_state, SYNTHETIC_LOGIN_PRINCIPALS)
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
    for kind, login in SYNTHETIC_LOGIN_PRINCIPALS.items():
        with _connect_as(owner_dsn, login) as verifier:
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
    with psycopg.connect(owner_dsn, row_factory=dict_row) as reader:
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


if __name__ == "__main__":
    dsn = os.environ.get("CANON_SYNTHETIC_PG_DSN", "")
    if not dsn:
        raise SystemExit("CANON_SYNTHETIC_PG_DSN is required")
    database_id = os.environ.get("CANON_SYNTHETIC_DB_ID", "")
    print(rehearse(dsn, database_id))
