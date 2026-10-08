#!/usr/bin/env python3
"""Bounded existing-instance trial and independent ordinary Stop.

Preparation locks released by parent; dispatch awaits the coordinated live window.
Stop-only is a separate process/run; neither path creates resources or forces Stop.
IBM exposes no GET instance-actions queue: never report that queue as reconciled.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
import json
import os
import signal
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from .ibm_vpc_oracle_probe import verify_identity
except ImportError:
    from ibm_vpc_oracle_probe import verify_identity

LIVE_START_ENABLED = True
INSTANCE_ID = "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2"
INSTANCE_NAME = "bridge-school-compute-ibm"
IMAGE_ID = "r010-25f84546-413c-4476-83ee-ae9580f554e5"
IMAGE_NAME = "bridge-ibm-before-start-20261001"
VOLUME_ID = "r010-430d0d70-3cdf-4f0c-8709-53e0ee0c4d7e"
ORIGIN = "https://eu-de.iaas.cloud.ibm.com"
INSTANCE_PATH = "/v1/instances/" + INSTANCE_ID
IMAGE_PATH = "/v1/images/" + IMAGE_ID
ACTION_PATH = INSTANCE_PATH + "/actions"
QUERY = "?version=2026-09-22&generation=2"
IAM_URL = "https://iam.cloud.ibm.com/identity/token"
POLL_SECONDS = 5
WINDOW_SECONDS = 600
RUNNING_SECONDS = 120  # stop even if admin works: no unverified extension signal
STOP_POLL_SECONDS = 180
HTTP_SECONDS = 10
START_TOKEN_MIN_SECONDS = WINDOW_SECONDS + STOP_POLL_SECONDS + 120
MAX_BYTES = 1024 * 1024
GUEST_DIAGNOSTIC_SECONDS = 38


class ControlError(Exception):
    """Fixed safe error code; provider bodies are never rendered."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ControlError("redirect_refused")


@contextmanager
def wall_deadline(seconds):
    # Operational runners are pinned to Ubuntu; fail closed elsewhere.
    if not hasattr(signal, "setitimer"):
        raise ControlError("wall_deadline_unavailable")
    if signal.getitimer(signal.ITIMER_REAL)[0] != 0:
        raise ControlError("existing_wall_timer")
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise ControlError("wall_deadline_expired")
    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def wire(request, *, timeout=HTTP_SECONDS):
    # The wall timer covers TLS/open AND the complete body, including trickle I/O.
    with wall_deadline(timeout):
        return _wire(request, timeout=timeout)


def _wire(request, *, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            allowed = {200, 201, 202} if request.method == "POST" else {200}
            if response.status not in allowed:
                raise ControlError("unexpected_http_status")
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ControlError("http_" + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ControlError("transport_failed") from None
    if len(raw) > MAX_BYTES:
        raise ControlError("response_too_large")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ControlError("invalid_json") from None
    if not isinstance(data, dict):
        raise ControlError("invalid_schema")
    return data


def authenticate():
    key = os.environ.get("IBM_CLOUD_API_KEY", "")
    if not key or any(c.isspace() for c in key):
        raise ControlError("api_key_invalid")
    data = urllib.parse.urlencode({"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": key}).encode()
    reply = wire(urllib.request.Request(IAM_URL, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "bridge-ibm-trial/1"}))
    token = reply.get("access_token")
    try:
        verify_identity(token)
    except Exception:
        raise ControlError("iam_identity_or_expiry_invalid") from None
    return token


def verify_start_ttl(token):
    # Recheck AFTER preflight, immediately before Start. No refresh/mutation retry.
    # Emergency Stop intentionally retains only the existing IAM expiry check.
    try:
        now = time.time()
        verify_identity(token, now=now)
        payload = token.split('.')[1]
        expiry = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))['exp']
        if expiry - now < START_TOKEN_MIN_SECONDS:
            raise ValueError
    except Exception:
        raise ControlError('start_token_lifetime_insufficient') from None


class Client:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, body=None):
        if body is not None and (not isinstance(body, dict) or type(body.get("force")) is not bool):
            raise ControlError("request_not_allowed")
        if not ((method == "GET" and path in {INSTANCE_PATH, IMAGE_PATH} and body is None)
                or (method == "POST" and path == ACTION_PATH and
                    body in ({"type": "start", "force": False}, {"type": "stop", "force": False}))):
            raise ControlError("request_not_allowed")
        if method == "POST" and body["type"] == "start" and not LIVE_START_ENABLED:
            raise ControlError("live_start_locked")
        if method == "POST" and body["type"] == "start":
            verify_start_ttl(self.token)
        req = urllib.request.Request(ORIGIN + path + QUERY, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/json",
                     "Content-Type": "application/json", "User-Agent": "bridge-ibm-trial/1"})
        return wire(req)

    def state(self):
        data = self.call("GET", INSTANCE_PATH)
        if (data.get("id") != INSTANCE_ID or data.get("name") != INSTANCE_NAME
                or data.get("zone", {}).get("name") != "eu-de-2"):
            raise ControlError("instance_mismatch")
        status = data.get("status")
        if status not in {"stopped", "starting", "running", "stopping", "restarting", "pending", "failed"}:
            raise ControlError("instance_state_unknown")
        startable = data.get("startable")
        if type(startable) is not bool:
            raise ControlError("startable_missing")
        return status, startable

    def backup(self):
        data = self.call("GET", IMAGE_PATH)
        if (data.get("id") != IMAGE_ID or data.get("name") != IMAGE_NAME
                or data.get("status") != "available" or data.get("source_volume", {}).get("id") != VOLUME_ID
                or data.get("encryption") not in {"none", "provider_managed"} or "encryption_key" in data):
            raise ControlError("backup_not_verified")
        # Start-only binding: never apply this additional gate to emergency Stop.
        instance = self.call("GET", INSTANCE_PATH)
        if (instance.get("id") != INSTANCE_ID
                or instance.get("boot_volume_attachment", {}).get("volume", {}).get("id") != VOLUME_ID
                or instance.get("profile", {}).get("name") != "bx3dc-8x40"):
            raise ControlError("boot_or_quoted_profile_mismatch")

    def action(self, action):
        if action not in {"start", "stop"}:
            raise ControlError("action_not_allowed")
        reply = self.call("POST", ACTION_PATH, {"type": action, "force": False})
        # Deprecated action IDs/status are not used as proof of completion.
        if reply.get("type") != action or reply.get("status") not in {None, "pending", "running", "completed"}:
            raise ControlError("action_response_unverified")


def emit(event, **safe_fields):
    print(json.dumps({"event": event, **safe_fields}, sort_keys=True), flush=True)


def ordinary_stop(client, *, clock=time.monotonic, sleep=time.sleep, out=emit, start_uncertain=False):
    """One Stop at most per invocation, including STARTING; no mutation retries.

    A STOPPED read while startable=false or following unknown Start is not success.
    No unsupported GET /actions is fabricated. Limited polling reports uncertainty.
    """
    deadline = clock() + STOP_POLL_SECONDS
    submitted = False
    unknown_post = False
    active_seen = False
    stable_stopped = 0
    last_error = None
    post_refused = False
    while clock() < deadline:
        try:
            state, startable = client.state()
        except ControlError as exc:
            last_error = str(exc)
            out("STOP_READ_BLOCKED", reason=last_error)
            break  # e.g. 403 is explicit, not treated as success or retried
        out("STOP_STATE", status=state, startable=startable, action_queue="NOT_EXPOSED_BY_API")
        if state in {"running", "starting", "restarting"}:
            active_seen = True
        if state == "stopped" and startable:
            stable_stopped += 1
            if stable_stopped >= 3:
                if start_uncertain and not active_seen:
                    if stable_stopped == 3:
                        out("STOPPED_BUT_START_OUTCOME_UNRESOLVED")
                    # Keep watching the full containment window for delayed Start.
                else:
                    out("STOPPED_OBSERVED", action_queue="NOT_EXPOSED_BY_API", stop_post_unknown=unknown_post)
                    return 4 if post_refused else 0
        else:
            stable_stopped = 0
        if state in {"running", "starting", "restarting"} and not submitted:
            submitted = True  # set BEFORE IO; never blindly resubmit
            out("ORDINARY_STOP_SUBMITTING")
            try:
                client.action("stop")
                out("ORDINARY_STOP_ACCEPTED")
            except ControlError as exc:
                last_error = str(exc)
                post_refused = last_error.startswith("http_4")
                unknown_post = not post_refused
                out("ORDINARY_STOP_REFUSED" if post_refused else "ORDINARY_STOP_OUTCOME_UNKNOWN", reason=last_error)
        sleep(min(POLL_SECONDS, max(0, deadline-clock())))
    out("STOP_NOT_CONFIRMED", reason=last_error or "poll_deadline", action_queue="NOT_EXPOSED_BY_API")
    return 4


def trial(client, *, mode="trial", guest_probe=None, clock=time.monotonic, sleep=time.sleep, out=emit):
    if mode not in {"trial", "manual_console_trial"}:
        raise ControlError("trial_mode_invalid")
    if guest_probe is not None and mode != "trial":
        raise ControlError("guest_diagnostic_mode_invalid")
    running_limit = RUNNING_SECONDS if mode == "trial" else None
    if not LIVE_START_ENABLED:
        raise ControlError("live_start_locked")
    client.backup()
    state, startable = client.state()
    if state != "stopped" or not startable:
        raise ControlError("start_preflight_not_stopped_startable")
    began = clock()
    start_uncertain = True
    start_transition_seen = False
    start_attempted = False
    running_at = None
    reason = "window_expired"
    diagnostic_failed = guest_probe is not None
    try:
        start_attempted = True
        out("START_SUBMITTING", mode=mode, max_window_seconds=WINDOW_SECONDS, running_window_seconds=running_limit)
        client.action("start")
        start_uncertain = False
        out("START_ACCEPTED")
        # Reserve a trial GET, poll sleep, containment GET and Stop POST.
        reserve = 3*HTTP_SECONDS + POLL_SECONDS
        stop_request_by = began + WINDOW_SECONDS - reserve
        while clock() < stop_request_by:
            state, _ = client.state()
            if state in {"starting", "running", "restarting"}:
                start_transition_seen = True
            out("TRIAL_STATE", status=state)
            if state == "running":
                if running_at is None:
                    running_at = clock()
                    if running_limit is not None:
                        out("RUNNING_GUEST_WINDOW", seconds=running_limit)
                    else:
                        out("RUNNING_MANUAL_CONSOLE_WINDOW", remaining_seconds=max(0, int(began + WINDOW_SECONDS - clock())))
                # The optional probe is run by this same controller after Running.
                # Preparation finished before Start; no chat trigger or waiting process.
                if guest_probe is not None:
                    remaining = min(stop_request_by - clock(),
                                    running_at + running_limit - reserve - clock())
                    if remaining < GUEST_DIAGNOSTIC_SECONDS + 2:
                        out("GUEST_DIAGNOSTIC_SKIPPED", reason="insufficient_stop_reserve")
                        reason = "guest_diagnostic_budget"
                        break
                    out("GUEST_DIAGNOSTIC_STARTED", **guest_probe.binding,
                        max_seconds=GUEST_DIAGNOSTIC_SECONDS)
                    try:
                        with wall_deadline(GUEST_DIAGNOSTIC_SECONDS):
                            verified = guest_probe.run_once()
                    except Exception:
                        # Do not render exception arguments, child output or credentials.
                        verified = False
                        out("GUEST_DIAGNOSTIC_FAILED", reason="bounded_probe_failed")
                    diagnostic_failed = verified is not True
                    out("GUEST_DIAGNOSTIC_RESULT", authenticated=verified is True)
                    reason = "guest_diagnostic_complete" if verified is True else "guest_diagnostic_failed"
                    break  # success and failure both lead immediately to finally Stop
                # Only the explicit manual console mode omits the short RDC timer.
                # Its absolute deadline remains anchored BEFORE the sole Start POST.
                if running_limit is not None and clock() >= running_at + running_limit - reserve:
                    reason = "guest_window_expired"
                    break
            elif state in {"stopping", "stopped"} and running_at is not None:
                reason = "external_stop_observed"
                break
            elif state in {"failed", "pending", "restarting"}:
                reason = "unexpected_instance_state"
                break
            sleep(POLL_SECONDS)
    except ControlError as exc:
        reason = str(exc)
        out("TRIAL_ABORT", reason=reason, start_outcome_unknown=start_uncertain)
    finally:
        if start_attempted:
            out("TRIAL_CONTAINMENT", reason=reason)
            # Accepted POST is NOT proof that its queued Start has taken effect.
            result = ordinary_stop(client, clock=clock, sleep=sleep, out=out,
                                   start_uncertain=not start_transition_seen)
    return result if result != 0 else (3 if diagnostic_failed else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["trial", "manual_console_trial", "stop"])
    parser.add_argument("--ack", required=True)
    parser.add_argument("--guest-diagnostic", action="store_true", help="One run-bound ubuntu SSH probe; trial only.")
    args = parser.parse_args(argv)
    expected = {"trial": "OWNER_APPROVED_10MIN_10USD", "manual_console_trial": "OWNER_APPROVED_MANUAL_CONSOLE_10MIN_10USD", "stop": "ORDINARY_STOP_EXACT_IBM"}[args.mode]
    if args.ack != expected:
        emit("BLOCKED", reason="ack_mismatch")
        return 3
    if args.guest_diagnostic and args.mode != "trial":
        emit("BLOCKED", reason="guest_diagnostic_mode_invalid")
        return 3
    if args.mode != "stop" and not LIVE_START_ENABLED:
        emit("BLOCKED", reason="live_start_locked")
        return 3  # before credentials/auth/network
    guest_probe = None
    try:
        if args.guest_diagnostic:
            try:
                from . import ibm_trial_guest_probe as guest
            except ImportError:
                import ibm_trial_guest_probe as guest
            with wall_deadline(guest.PREPARE_SECONDS):
                guest_probe = guest.prepare()  # before IBM authentication or Start
        client = Client(authenticate())
        return trial(client, mode=args.mode, guest_probe=guest_probe) if args.mode != "stop" else ordinary_stop(client, start_uncertain=True)
    except ControlError as exc:
        emit("BLOCKED", reason=str(exc))
        return 3
    except Exception:
        emit("BLOCKED", reason="unexpected_local_error")
        return 4
    finally:
        if guest_probe is not None:
            guest_probe.close()


if __name__ == "__main__":
    raise SystemExit(main())
