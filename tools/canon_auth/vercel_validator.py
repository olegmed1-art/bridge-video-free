"""Dormant build-only validator. No app imports, public route or build hook.

Uses the immutable same-project origin verified READY by control-plane preflight.
Protection redirects are a terminal blocker; never replace with a mutable alias.
"""
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from tempfile import gettempdir
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

INTENT = "canon-auth-vercel-20261004-v1"
READY_SHA = "57fcfb319692b5117bca052101432c0eb25b9d15"
DEPLOYMENT = "dpl_FYDYpJrZ1cFSy89xsWUirTw6eR2k"
ORIGIN = "https://bridge-video-free-35mns6cbb-olegmed1-4368s-projects.vercel.app"
PROJECT = "prj_oF4SA0gA1PX6BuJEmJ1BiHVBXUGP"
DEADLINE = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)
POSITION = "00000000-0000-4000-8000-000000000001"


class Rejected(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_intent(env, now):
    if not (env.get("CANON_VALIDATION_INTENT") == INTENT
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


def request(opener, path, *, token=None, body=None):
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
            return response.status, json.loads(data)
        except Exception:
            raise Rejected("response_contract_rejected") from None


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
    status, data = request(opener, "/healthz")
    if status != 200 or data != {"status": "ok"}:
        raise Rejected("health_rejected")
    status, data = request(opener, "/v1/overview", token=token)
    if status != 200 or not isinstance(data, dict) or "stable_name" not in data:
        raise Rejected("authenticated_overview_rejected")
    body = {"teacher_key": "school-tournament-shape", "teacher_version": "tour-1nt-shape-assessment-v1",
            "teacher_system": "SCHOOL_TOURNAMENT_CURRENT_V1", "canon_request": {
                "task": "assess_call", "scope_key": "school-tournament-1nt-shape-assessment-v1",
                "version": "tour-1nt-shape-assessment-v1", "proposed_call": "3H"}}
    status, data = request(opener, f"/v1/ai/positions/{POSITION}/teacher-evidence", token=token, body=body)
    if status != 404 or data != {"detail": {"code": "POSITION_NOT_FOUND"}}:
        raise Rejected("teacher_refusal_rejected")
    # This proves authenticated refusal, NOT baseline ABSTAIN or activated meaning.
    return {"schema": INTENT, "status": "authenticated_refusal_pass", "ready_sha": READY_SHA,
            "requests": 3, "pilot_activated": False, "baseline_abstain_proven": False}


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
    return 0 if result["status"] == "authenticated_refusal_pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
