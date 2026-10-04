"""Dormant build-only validator. No app imports, public route or build hook.

Uses the official production API origin with normal application authentication.
A separate control-plane/log verifier must bind every request to the target SHA.
"""
import json
import os
import re
import signal
from datetime import datetime, timezone
from pathlib import Path
from tempfile import gettempdir
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

INTENT = "canon-auth-vercel-20261004-v1"
READY_SHA = "cf6091f09fa70afc4b25162fbbb2fea0dc36898a"
DEPLOYMENT = "dpl_9tC4HDYrX6WLxgQXpcMm32te8ue3"
ORIGIN = "https://bridge-video-free.vercel.app"
PROJECT = "prj_oF4SA0gA1PX6BuJEmJ1BiHVBXUGP"
DEADLINE = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)
POSITION = "00000000-0000-4000-8000-000000000001"
REQUEST_ID = r"[A-Za-z0-9]{3,16}-[0-9]{13}-[a-f0-9]{12}"


class Rejected(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_intent(env, now):
    if not (re.fullmatch(r"[0-9a-f]{40}", READY_SHA)
            and DEPLOYMENT.startswith("dpl_")
            and env.get("CANON_VALIDATION_INTENT") == INTENT
            and env.get("CANON_READY_SHA") == READY_SHA
            and env.get("CANON_READY_DEPLOYMENT") == DEPLOYMENT
            and env.get("CANON_READY_STATE") == "READY"
            and env.get("CANON_READY_ORIGIN") == ORIGIN
            and env.get("VERCEL_PROJECT_ID") == PROJECT
            and env.get("VERCEL_ENV") == "production"
            and now < DEADLINE):
        raise Rejected("intent_rejected")
    # A reviewed control-plane observation is mandatory, not inferred from health.
    try:
        observed = datetime.fromisoformat(env["CANON_READY_OBSERVED_AT"])
        age = (now - observed).total_seconds()
    except Exception:
        raise Rejected("attestation_rejected") from None
    if not 0 <= age <= 300:
        raise Rejected("attestation_rejected")


def request(opener, path, *, token=None, body=None, redact_token=None):
    if path not in ("/healthz", "/v1/overview", f"/v1/ai/positions/{POSITION}/teacher-evidence"):
        raise Rejected("path_rejected")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    req = Request(ORIGIN + path, data=json.dumps(body).encode() if body else None, headers=headers)
    try:
        response = opener.open(req, timeout=8)
    except HTTPError as exc:
        # 404 is consumed only as an expected typed refusal below; never follow 3xx.
        if exc.code != 404:
            raise Rejected("http_rejected") from None
        response = exc
    with response:
        if response.geturl() != ORIGIN + path:
            raise Rejected("response_origin_rejected")
        data = response.read(32769)
        if len(data) > 32768:
            raise Rejected("response_size_rejected")
        try:
            parsed = json.loads(data)
        except Exception:
            raise Rejected("response_contract_rejected") from None
        header = response.headers.get("x-vercel-id", "")
        match = re.fullmatch(r"(?:[a-z0-9]{3,12}::){1,3}(" + REQUEST_ID + ")", header)
        if not match or any(secret and secret in header for secret in (token, redact_token)):
            raise Rejected("request_id_rejected")
        receipt = {"request_id": match[1], "path": path, "status_code": response.status}
        return response.status, parsed, receipt


def run(env, *, now=None, opener=None, claim=None):
    validate_intent(env, now or datetime.now(timezone.utc))
    token = env.get("BRIDGE_API_TOKEN", "")
    if not token or "\r" in token or "\n" in token:
        raise Rejected("credential_missing_or_invalid")
    # Prevent accidental second invocation in the same ephemeral build, even on failure.
    claim = claim or Path(gettempdir()) / (INTENT + ".claimed")
    try:
        with claim.open("x", encoding="ascii") as fh:
            fh.write(INTENT)
    except FileExistsError:
        raise Rejected("already_attempted") from None
    opener = opener or build_opener(ProxyHandler({}), NoRedirect())
    # First request carries no credential: protection/redirect failure stops here.
    status, data, health_receipt = request(opener, "/healthz", redact_token=token)
    if status != 200 or data != {"status": "ok"}:
        raise Rejected("health_rejected")
    status, data, overview_receipt = request(opener, "/v1/overview", token=token)
    if status != 200 or not isinstance(data, dict) or "stable_name" not in data:
        raise Rejected("authenticated_overview_rejected")
    body = {"teacher_key": "school-tournament-shape", "teacher_version": "tour-1nt-shape-assessment-v1",
            "teacher_system": "SCHOOL_TOURNAMENT_CURRENT_V1", "canon_request": {
                "task": "assess_call", "scope_key": "school-tournament-1nt-shape-assessment-v1",
                "version": "tour-1nt-shape-assessment-v1", "proposed_call": "3H"}}
    status, data, teacher_receipt = request(opener, f"/v1/ai/positions/{POSITION}/teacher-evidence", token=token, body=body)
    if status != 404 or data != {"detail": {"code": "POSITION_NOT_FOUND"}}:
        raise Rejected("teacher_refusal_rejected")
    receipts = [health_receipt, overview_receipt, teacher_receipt]
    if len({r["request_id"] for r in receipts}) != 3:
        raise Rejected("duplicate_request_id")
    # HTTP success alone is not acceptance: external log correlation is mandatory.
    return {"schema": INTENT, "status": "pending_deployment_correlation", "ready_sha": READY_SHA,
            "deployment_id": DEPLOYMENT, "origin": ORIGIN, "receipts": receipts,
            "requests": 3, "pilot_activated": False, "baseline_abstain_proven": False,
            "persisted": False, "queued": False, "finalized": False}


def main():
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Rejected("deadline_exceeded")))
        signal.alarm(35)
    try:
        result = run(os.environ)
    except Rejected as exc:
        result = {"schema": INTENT, "status": str(exc)}
    except Exception:
        result = {"schema": INTENT, "status": "transport_or_contract_failure"}
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pending_deployment_correlation" else 1


if __name__ == "__main__":
    raise SystemExit(main())
