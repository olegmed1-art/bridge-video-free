"""Explicit reviewed compatibility transition for fixed owned revoke ONLY.

No automatic compatibility inference, new C/P/N, target, SQL or TTL. This does
not authenticate supplied JSON: the protected owner invocation must pin its
externally reviewed digest and independently authenticate current main.
"""
from datetime import timedelta
import re
from .launch_contract import require, timestamp, digest


def validate(value, launch, env, git, now):
    fields={"purpose","code_sha","runtime_sha","plan_hash","contract_hash",
            "checkout_tree","current_main","module_hash","reviewed_by","authorized_at","expires_at"}
    require(isinstance(value,dict) and set(value)==fields,"recovery_transition_fields_refused")
    require(env.get("EXPECTED_RECOVERY_TRANSITION")==digest(value)
        and value["purpose"]=="owned-emergency-revoke-only"
        and value["reviewed_by"]=="olegmed1-art"
        and value["code_sha"]==launch.code_sha and value["runtime_sha"]==launch.runtime_sha
        and value["plan_hash"]==launch.plan_hash and value["contract_hash"]==launch.fingerprint
        and value["module_hash"]==launch.module_hash,
        "reviewed_recovery_transition_required")
    require(all(isinstance(value[k],str) and re.fullmatch(r"[a-f0-9]{40}",value[k])
                for k in ("checkout_tree","current_main"))
        and value["current_main"]!=launch.runtime_sha,"explicit_main_transition_required")
    start,end=timestamp(value["authorized_at"]),timestamp(value["expires_at"])
    require(start<=now<end and timedelta(0)<end-start<=timedelta(minutes=15),
            "recovery_transition_stale")
    # Exact immutable checkout commit AND entire Git tree pin the transitive
    # repository source closure; installation must separately review dependency
    # versions and trusted runner/import path. No updated main source is run.
    require(git("rev-parse","HEAD")==launch.runtime_sha
        and git("rev-parse","HEAD^{tree}")==value["checkout_tree"]
        and git("diff","--name-only","HEAD")=="","pinned_recovery_checkout_required")
    return value["current_main"]
