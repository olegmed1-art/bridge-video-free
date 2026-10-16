"""Offline proposal for the exact installed legacy 0060; no executor or DSN.

Uses the original positively acknowledged receipt and pre-install baseline.
The current candidate's builder continues to refuse this historical checksum.
"""
import hashlib
import json
import re

import connector_packet as c
import build_recovery as r

KEY = "repair-0060-legacy-installed-v2"
BODY_SHA256 = "30ba592da8f2924092df699d7501f704fd020690b135d5a422944b0b76adaa03"
PINNED = {**c.PINNED, "0060_fixed_subset_admission": c.LEGACY_0060_SHA256}


def admission(receipt, *, installed_0060_checksum):
    if installed_0060_checksum != c.LEGACY_0060_SHA256:
        raise ValueError("NOT_SUPPORTED_LEGACY_0060")
    if c.ROOT != r.ROOT:
        raise ValueError("LEGACY_INPUT_DIRECTORIES_DIFFER")
    if set(receipt) != {"before_old_data", "data_digest", "catalog_md5", "target", "state"}:
        raise ValueError("invalid receipt shape")
    if (receipt["state"] != "INSTALLED_RUNTIME_HOLD"
            or receipt["target"] != r.target()["branch_id"]
            or not re.fullmatch(r"[0-9a-f]{32}", receipt["catalog_md5"])):
        raise ValueError("wrong receipt state/target")
    data = r.quote(json.dumps(receipt["data_digest"], sort_keys=True)) + "::jsonb"
    checks = c.prefix() + [
        "LOCK TABLE video_queue.batch,video_queue.job,video_queue.job_event IN ACCESS EXCLUSIVE MODE",
        c.require(f"{c.catalog_digest()}={r.quote(receipt['catalog_md5'])}", "RECOVERY_CATALOG_DRIFT"),
        c.TEMP_GRANT,
        "LOCK TABLE video_queue.subset_approval,video_queue.source_observation,video_queue.subset_admission,video_queue.source_reservation IN ACCESS EXCLUSIVE MODE",
        c.require(c.function_acl_matrix(scoped_claim=False), "LEGACY_EXACT_FUNCTION_ACL"),
        c.require("NOT EXISTS(SELECT FROM pg_default_acl WHERE defaclrole='video_subset_owner'::regrole)",
                  "LEGACY_DEFAULT_ACL_DRIFT"),
        c.require(c.inert_subset(), "RECOVERY_RUNTIME_USED"),
        c.require(c.drain(), "RECOVERY_NOT_DRAINED"),
        c.require(c.reservation_map(), "RECOVERY_RESERVATION_DRIFT"),
        c.require(f"{r.data_digest_sql()}={data}", "RECOVERY_DATA_DRIFT"),
        c.require(c.history_matches(PINNED), "RECOVERY_FULL_HISTORY_DRIFT")]
    return checks, data


def payload(statements):
    return {**{k: v for k, v in r.target().items() if k != "endpoint_id"},
            "sql_statements": statements}


def verify_payload(receipt, *, installed_0060_checksum):
    checks, _ = admission(receipt, installed_0060_checksum=installed_0060_checksum)
    return payload(checks + [c.TEMP_REVOKE,
        c.require(c.self_grant_absent(), "VERIFIER_TEMP_GRANT_REMAINS"),
        "SELECT 'LEGACY_COMMITTED_INSTALL_RECEIPT_MATCHES' AS verification"])


def recover_payload(receipt, *, installed_0060_checksum):
    checks, data = admission(receipt, installed_0060_checksum=installed_0060_checksum)
    body = (r.ROOT / "recovery" / (KEY + ".sql")).read_bytes()
    if hashlib.sha256(body).hexdigest() != BODY_SHA256:
        raise ValueError("legacy recovery source changed")
    baseline = r.projection(json.loads((r.ROOT / "catalog-baseline.json").read_text()))
    checks += c.split_sql(body.decode("utf-8"))
    checks += [
        c.require(r.legacy_catalog_sql(baseline), "RECOVERY_LEGACY_CATALOG_MISMATCH"),
        c.require(f"{c.function_acl_matrix(recovered=True, scoped_claim=False)} AND {c.function_default_acl_safe()}",
                  "RECOVERY_SUBSET_NOT_DISABLED"),
        c.require(f"{r.data_digest_sql()}={data}", "RECOVERY_HISTORY_CHANGED"),
        f"INSERT INTO public.schema_migration(migration_key,checksum) VALUES({r.quote(KEY)},{r.quote(BODY_SHA256)})",
        c.require(c.history_matches({**PINNED, KEY: BODY_SHA256}), "RECOVERY_FULL_HISTORY_DRIFT"),
        "CREATE TEMP TABLE uv_receipt(data jsonb) ON COMMIT DROP",
        f"INSERT INTO pg_temp.uv_receipt SELECT jsonb_build_object('state','LEGACY_CAPABILITIES_RECOVERED_SUBSET_DISABLED','installed_0060_checksum',{r.quote(c.LEGACY_0060_SHA256)},'repair_key',{r.quote(KEY)},'data_digest',{r.data_digest_sql()},'legacy_catalog_matches',true)",
        c.TEMP_REVOKE, c.require(c.self_grant_absent(), "RECOVERY_TEMP_GRANT_REMAINS"),
        "SELECT data AS receipt FROM pg_temp.uv_receipt"]
    return payload(checks)
