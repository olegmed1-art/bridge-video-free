"""Offline evidence gate; inputs must come from authenticated control-plane reads.

This validates normalized observations, not their authenticity. Preserve original
tool responses and query parameters beside the normalized evidence. Never treat
caller-created JSON alone as proof of a runtime-log observation.
"""
from datetime import datetime
import re

from . import vercel_validator as v


def verify(result, before, after, observations):
    """Every receipt needs exactly one request-filtered log match at the pinned deployment."""
    if (not v.READY_SHA or not v.DEPLOYMENT
            or result.get("schema") != v.INTENT
            or type(result.get("requests")) is not int or result["requests"] != 3
            or any(result.get(k) is not False for k in (
                "persisted", "queued", "finalized", "pilot_activated", "baseline_abstain_proven"))
            or result.get("status") != "pending_deployment_correlation"
            or result.get("ready_sha") != v.READY_SHA
            or result.get("deployment_id") != v.DEPLOYMENT
            or result.get("origin") != v.ORIGIN):
        raise v.Rejected("result_binding_rejected")
    required = dict(project_id=v.PROJECT, deployment_id=v.DEPLOYMENT,
                    sha=v.READY_SHA, origin=v.ORIGIN, state="READY")
    for snapshot in (before, after):
        if any(snapshot.get(k) != value for k, value in required.items()):
            raise v.Rejected("alias_binding_rejected")
    try:
        start, end = (datetime.fromisoformat(s["observed_at"]) for s in (before, after))
        if start.tzinfo is None or end.tzinfo is None or not 0 <= (end-start).total_seconds() <= 300:
            raise ValueError()
    except Exception:
        raise v.Rejected("observation_window_rejected") from None
    expected = [("/healthz", 200), ("/v1/overview", 200),
                (f"/v1/ai/positions/{v.POSITION}/teacher-evidence", 404)]
    receipts = result.get("receipts", [])
    if (len(receipts) != 3 or len(observations) != 3
            or any(not isinstance(r.get("request_id"), str)
                   or not re.fullmatch(v.REQUEST_ID, r["request_id"]) for r in receipts)
            or [(r.get("path"), r.get("status_code")) for r in receipts] != expected
            or len({r.get("request_id") for r in receipts}) != 3):
        raise v.Rejected("receipt_set_rejected")
    verify_receipts(receipts, before, after, observations)
    return {"status": "authenticated_refusal_verified", "sha": v.READY_SHA,
            "deployment_id": v.DEPLOYMENT, "requests_correlated": 3,
            "baseline_abstain_proven": False, "pilot_activated": False}


def verify_receipts(receipts, before, after, observations):
    """Shared correlation check for phase receipts and the complete polling audit."""
    required = dict(project_id=v.PROJECT, deployment_id=v.DEPLOYMENT,
                    sha=v.READY_SHA, origin=v.ORIGIN, state="READY")
    for snapshot in (before, after):
        if any(snapshot.get(k) != value for k, value in required.items()):
            raise v.Rejected("alias_binding_rejected")
    try:
        start, end = (datetime.fromisoformat(s["observed_at"]) for s in (before, after))
        if start.tzinfo is None or end.tzinfo is None or not 0 <= (end-start).total_seconds() <= 420:
            raise ValueError()
    except Exception:
        raise v.Rejected("observation_window_rejected") from None
    if (not 1 <= len(receipts) <= 40 or len(observations) != len(receipts)
            or any(not isinstance(r.get("request_id"), str) or not re.fullmatch(v.REQUEST_ID, r["request_id"]) for r in receipts)
            or len({r["request_id"] for r in receipts}) != len(receipts)):
        raise v.Rejected("receipt_set_rejected")
    for receipt in receipts:
        # query_request_id must be the actual get_runtime_logs filter, not a
        # full-text search or a timestamp-only match. A missing match fails closed.
        matches = [o for o in observations if o.get("query_request_id") == receipt["request_id"]]
        if len(matches) != 1:
            raise v.Rejected("request_correlation_rejected")
        obs = matches[0]
        if (obs.get("query_project_id") != v.PROJECT
                or obs.get("query_deployment_id") != v.DEPLOYMENT
                or type(obs.get("matched_count")) is not int or obs["matched_count"] != 1
                or obs.get("deployment_id") != v.DEPLOYMENT
                or obs.get("path") != receipt["path"]
                or obs.get("status_code") != receipt["status_code"]):
            raise v.Rejected("runtime_binding_rejected")
        try:
            at = datetime.fromisoformat(obs["request_at"])
            if at.tzinfo is None or not start <= at <= end:
                raise ValueError()
        except Exception:
            raise v.Rejected("runtime_time_rejected") from None
    return {"status": "receipts_correlated", "count": len(receipts)}


def verify_phase(result, before, after, observations, previous_ids):
    """Check one controller transition; caller preserves the cross-phase ID ledger."""
    expected = {
        "baseline": [{"call": "3H", "status": "ABSTAIN"}],
        "active": [{"call": "3H", "status": "SUPPORTED"}, {"call": "3S", "status": "CONTRADICTED"}],
        "revoked": [{"call": "3H", "status": "ABSTAIN"}],
        "reactivated": [{"call": "3H", "status": "SUPPORTED"}, {"call": "3S", "status": "CONTRADICTED"}]}
    phase = result.get("phase")
    if (phase not in expected or result.get("schema") != v.INTENT
            or result.get("status") != "pending_deployment_correlation"
            or result.get("ready_sha") != v.READY_SHA or result.get("deployment_id") != v.DEPLOYMENT
            or result.get("answers") != expected[phase]):
        raise v.Rejected("phase_binding_rejected")
    receipts = result.get("receipts", [])
    if (len(receipts) != len(expected[phase]) or any(r.get("request_id") in previous_ids for r in receipts)
            or any(r.get("path") != f"/v1/ai/positions/{v.POSITION}/teacher-evidence"
                   or r.get("status_code") != 200 for r in receipts)):
        raise v.Rejected("phase_receipt_rejected")
    verify_receipts(receipts, before, after, observations)
    return {"status": "phase_correlated", "phase": phase, "request_ids": [r["request_id"] for r in receipts]}
