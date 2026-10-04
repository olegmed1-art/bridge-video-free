"""One marked build performs one authenticated book identity GET, never promotes.

Prepared for review only. No app imports, SQL, arbitrary URL or canon client use.
External control-plane and request-log correlation remain mandatory.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
from tempfile import gettempdir
from datetime import datetime, timezone
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MARKER = "BOOK_RUNTIME_OBSERVATION_20261004_ONCE"
ORIGIN = "https://bridge-video-free.vercel.app"
PATH = "/v1/knowledge/validation/runtime-identity"
PROJECT = "prj_oF4SA0gA1PX6BuJEmJ1BiHVBXUGP"
READY_SHA = "efe59fc153aca09d83b8d5b4f9595d0fb2dc5e73"
READY_DEPLOYMENT = "dpl_9vn5iGMYbe8Vi3AGegAcnUMWufXP"
DEADLINE = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)
MESSAGE = re.compile(r"\A" + MARKER + r"\n{1,2}observed_at=(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)"
                     r"\nbase=" + READY_SHA + r"\ndeployment=" + READY_DEPLOYMENT
                     + r"\nnonce=([0-9a-f]{32})\n?\Z")
CONNECTION = {"host": "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech",
              "port": 5432, "database": "neondb", "principal": "bridge_school_app_principal",
              "tls_in_use": True, "read_only": True}


class Rejected(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def intent(env, now):
    message = env.get("VERCEL_GIT_COMMIT_MESSAGE", "")
    if not message.startswith(MARKER):
        return None
    match = MESSAGE.fullmatch(message)
    if not match:
        raise Rejected("INTENT_MALFORMED")
    observed = datetime.fromisoformat(match[1].replace("Z", "+00:00"))
    if not 0 <= (now - observed).total_seconds() <= 300 or now >= DEADLINE:
        raise Rejected("INTENT_EXPIRED")
    revision = env.get("VERCEL_GIT_COMMIT_SHA", "")
    if (env.get("VERCEL_ENV") != "production" or env.get("VERCEL_GIT_COMMIT_REF") != "main"
            or env.get("VERCEL_PROJECT_ID") != PROJECT
            or not re.fullmatch(r"[0-9a-f]{40}", revision) or revision == READY_SHA):
        raise Rejected("BUILD_CONTEXT_REJECTED")
    return {"build_sha": revision, "nonce": match[2], "control_plane_observed_at": observed.isoformat()}


def claim_once(binding):
    # Only a local build-process claim, not a distributed replay guarantee.
    key = hashlib.sha256((binding["build_sha"] + binding["nonce"]).encode()).hexdigest()
    path = Path(gettempdir()) / ("book-runtime-observation-" + key)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError:
        raise Rejected("BUILD_ALREADY_CLAIMED") from None


def observe(env, binding, *, opener=None, now=None):
    token = env.get("BRIDGE_API_TOKEN", "")
    if (not token or len(token) > 8192 or not token.isascii()
            or any(ord(c) < 32 or ord(c) == 127 for c in token)):
        raise Rejected("RESIDENT_TOKEN_UNAVAILABLE")
    request = Request(ORIGIN + PATH, method="GET", headers={"Authorization": "Bearer " + token})
    client = opener or build_opener(ProxyHandler({}), NoRedirect())
    try:
        with client.open(request, timeout=10) as response:
            if (response.status != 200 or response.geturl() != ORIGIN + PATH
                    or response.headers.get("Cache-Control") != "private, no-store, max-age=0"):
                raise Rejected("HTTP_RESPONSE_REJECTED")
            request_id = response.headers.get("x-vercel-id", "")
            if not re.fullmatch(r"[A-Za-z0-9:-]{3,128}", request_id):
                raise Rejected("REQUEST_CORRELATION_MISSING")
            raw = response.read(8193)
            if len(raw) > 8192 or token.encode() in raw or token in request_id:
                raise Rejected("RESPONSE_REJECTED")
            data = json.loads(raw)
        if set(data) != {"contract_version", "status", "observed_at", "deployment_revision", "connection", "neon"}:
            raise Rejected("RESPONSE_SHAPE_REJECTED")
        connection = data["connection"]
        if (data["contract_version"] != "book-runtime-identity-v1"
                or data["status"] != "OBSERVED_NOT_ADMITTED" or data["deployment_revision"] != READY_SHA
                or connection != CONNECTION or type(connection["port"]) is not int
                or connection["tls_in_use"] is not True or connection["read_only"] is not True):
            raise Rejected("RUNTIME_BINDING_REJECTED")
        tags = data["neon"]
        if (set(tags) != {"project_id", "branch_id", "endpoint_id", "source", "reset_matches", "pending_restart"}
                or tags["project_id"] != "misty-poetry-18012774" or tags["endpoint_id"] != "ep-noisy-pine-b1pe30sf"
                or not re.fullmatch(r"br-[a-z0-9-]{3,100}", tags["branch_id"])
                or tags["source"] != "configuration file" or tags["reset_matches"] is not True
                or tags["pending_restart"] is not False):
            raise Rejected("NEON_OBSERVATION_REJECTED")
        observed = datetime.fromisoformat(data["observed_at"])
        if not 0 <= ((now or datetime.now(timezone.utc)) - observed).total_seconds() <= 60:
            raise Rejected("RUNTIME_OBSERVATION_STALE")
        receipt = {"schema": "book-runtime-observation-receipt-v1", "status": "PENDING_DEPLOYMENT_CORRELATION",
                **binding, "expected_ready_sha": READY_SHA, "expected_ready_deployment": READY_DEPLOYMENT,
                "origin": ORIGIN, "path": PATH, "method": "GET", "request_id": request_id,
                "response_sha256": hashlib.sha256(raw).hexdigest(), "runtime_observed_at": observed.isoformat(),
                "neon": {k: tags[k] for k in ("project_id", "endpoint_id", "branch_id")}}
        if token in json.dumps(receipt, sort_keys=True):
            raise Rejected("RECEIPT_REJECTED")
        return receipt
    except Rejected:
        raise
    except Exception:
        raise Rejected("RUNTIME_OBSERVATION_UNAVAILABLE") from None


def main():
    armed = False
    try:
        binding = intent(os.environ, datetime.now(timezone.utc))
        if binding is None:
            print("book-runtime-observation: inactive ordinary build")
            return 0
        if not hasattr(signal, "SIGALRM"):
            raise Rejected("HARD_DEADLINE_UNAVAILABLE")
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Rejected("BUILD_DEADLINE_EXCEEDED")))
        signal.alarm(30)
        armed = True
        claim_once(binding)
        receipt = observe(os.environ, binding)
        print(json.dumps(receipt, sort_keys=True), flush=True)
        print("book-runtime-observation: observation_complete_no_promotion", flush=True)
        return 1
    except Rejected as exc:
        print(json.dumps({"status": "STOP", "code": str(exc)}), flush=True)
        return 1
    except Exception:
        print('{"status":"STOP","code":"UNEXPECTED_FAILURE"}', flush=True)
        return 1
    finally:
        if armed:
            signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
