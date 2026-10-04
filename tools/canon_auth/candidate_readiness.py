"""Cancellation for the fixed midnight candidate; no live admission/factories.

This candidate's required installations are absent. Source/CI receipts must not
turn them into live facts. No option accepts a proof dictionary or overrides this
decision. A future operational installation needs a separately reviewed candidate.
"""
import json
from datetime import datetime, timezone

CUTOFF = datetime(2026, 10, 4, 23, 55, tzinfo=timezone.utc)
WINDOW = ("2026-10-05T00:00:00Z", "2026-10-05T00:05:00Z")
BLOCKERS = (
    "unattended_exact_request_vercel_observer_uninstalled",
    "authoritative_create_only_claim_namespace_unqualified",
    "independent_24h_recovery_host_uninstalled",
    "cancellation_independent_recovery_qualification_missing",
    "complete_transitive_source_review_missing",
    "final_pg17_and_current_app_identity_qualification_missing",
)
MINIMUM_SETTINGS = {
    "claims": "Existing owner token is contents:read. Create-only Git references require contents:write; this broadens repository access and is not applied. Qualify fixed non-head/non-tag namespace and all event side effects before use.",
    "observer": "Provision an unattended existing authorized Vercel OAuth/CLI context with exact requestId filtering. Interactive MCP access is not transferable proof. No new secret/token binding is installed.",
    "recovery": "Install a separately supervised managed-credential service outside the controller and GitHub runner cancellation domain, supporting original immutable24h expiry. Existing IBM/HOLD/Oracle services cannot be repurposed in this scope.",
    "dispatch": "Automated GitHub workflow dispatch requires actions:write. Current owner workflow has no stage command. Neither permission nor workflow is changed.",
}

def cancellation(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset().total_seconds() != 0:
        raise ValueError("utc_required")
    return {"status": "CANDIDATE_CANCELLED", "candidate_window": list(WINDOW),
        "readiness_cutoff": CUTOFF.isoformat().replace("+00:00", "Z"),
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "reason": "required_installations_unavailable_within_authorized_scope",
        "missing": list(BLOCKERS), "admission": False, "automatic_reschedule": False,
        "settings_changed": False, "production_mutations": False}

def main():
    print(json.dumps(cancellation(), sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
