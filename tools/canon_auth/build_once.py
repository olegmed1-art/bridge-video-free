"""One explicitly marked merge build; HTTP reads only, never SQL or credentials export."""
import json
import os
import re
import signal
import time
from datetime import datetime, timezone
from urllib.request import ProxyHandler, build_opener

from . import vercel_validator as v

MARKER = "CANON_ACCEPTANCE_20261004_AUTHORIZED_RETRY1"
MESSAGE = re.compile(r"\A" + MARKER + r"\n{1,2}observed_at=(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\nbase=" + v.READY_SHA + r"\n?\Z")
HASHES = {"3H": "4127a40bfd62d7fa9d5d5b6e1f5b72b3c751881f2765cef5f690265382dcdb43",
          "3S": "2bb321fc675343a8dd3228cd06497d54ba66727be1a104512808338d9ffd2989"}
PATH = f"/v1/ai/positions/{v.POSITION}/teacher-evidence"


def intent(env):
    # Ordinary future builds return immediately, without reading any credential.
    message = env.get("VERCEL_GIT_COMMIT_MESSAGE", "")
    if not message.startswith("CANON_ACCEPTANCE_"):
        return None
    match = MESSAGE.fullmatch(message)
    if not match:
        raise v.Rejected("build_intent_malformed")
    if env.get("VERCEL_GIT_COMMIT_REF") != "main":
        raise v.Rejected("build_context_rejected")
    if env.get("VERCEL_ENV") != "production" or env.get("VERCEL_PROJECT_ID") != v.PROJECT:
        raise v.Rejected("build_context_rejected")
    return dict(env, CANON_VALIDATION_INTENT=v.INTENT, CANON_READY_SHA=v.READY_SHA,
                CANON_READY_DEPLOYMENT=v.DEPLOYMENT, CANON_READY_STATE="READY",
                CANON_READY_ORIGIN=v.ORIGIN, CANON_READY_OBSERVED_AT=match[1])


class Budget:
    def __init__(self, opener):
        self.opener, self.count = opener, 0

    def open(self, *args, **kwargs):
        if self.count >= 40:
            raise v.Rejected("request_budget_exceeded")
        self.count += 1
        return self.opener.open(*args, **kwargs)


def body(call):
    return {"teacher_key": "school-tournament-shape", "teacher_version": "tour-1nt-shape-assessment-v1",
            "teacher_system": "SCHOOL_TOURNAMENT_CURRENT_V1", "canon_request": {
                "task": "assess_call", "scope_key": "school-tournament-1nt-shape-assessment-v1",
                "version": "tour-1nt-shape-assessment-v1", "proposed_call": call}}


def assessment(data, call):
    if (not isinstance(data, dict) or data.get("position_id") != v.POSITION
            or data.get("teacher_key") != "school-tournament-shape"
            or data.get("teacher_version") != "tour-1nt-shape-assessment-v1"
            or data.get("teacher_system") != "SCHOOL_TOURNAMENT_CURRENT_V1"
            or data.get("scope_key") != "school-tournament-1nt-shape-assessment-v1"
            or data.get("assessment_scope") != "shape_meaning_only"
            or data.get("proposed_call") != call or "action" not in data or data["action"] is not None
            or any(data.get(k) is not False for k in ("persisted", "queued", "finalized"))):
        raise v.Rejected("assessment_contract_rejected")
    if data.get("status") == "ABSTAIN":
        if data.get("reason") != "NO_UNIQUE_ELIGIBLE_CANON_BINDING" or data.get("source_bindings") != []:
            raise v.Rejected("baseline_contract_rejected")
        return "ABSTAIN"
    expected = "SUPPORTED" if call == "3H" else "CONTRADICTED"
    sources = data.get("source_bindings")
    if (data.get("status") != expected or data.get("observed_shape") != {"S": 3, "H": 1, "D": 4, "C": 5}
            or data.get("point_threshold", "missing") is not None or data.get("forcing_status") != "FG"
            or not isinstance(sources, list) or len(sources) != 1
            or sources[0].get("payload_sha256") != HASHES[call]
            or sources[0].get("rule_key") != "RULE-TOUR-1NT-RSP-" + call
            or sources[0].get("decision_ids") != ["TDEC-20261003-002"]):
        raise v.Rejected("active_binding_rejected")
    return expected


def emit(phase, receipts, answers=None):
    print(json.dumps({"schema": v.INTENT, "phase": phase, "status": "pending_deployment_correlation",
                      "ready_sha": v.READY_SHA, "deployment_id": v.DEPLOYMENT,
                      "receipts": receipts, "answers": answers or []}, sort_keys=True), flush=True)


def lifecycle(env, *, opener=None, sleep=time.sleep, now=None, claim=None):
    client = Budget(opener or build_opener(ProxyHandler({}), v.NoRedirect()))
    auth = v.run(env, now=now, opener=client, claim=claim)
    seen = set()
    def accept(receipt):
        if receipt["request_id"] in seen:
            raise v.Rejected("duplicate_request_id")
        seen.add(receipt["request_id"])
    for receipt in auth["receipts"]:
        accept(receipt)
    print(json.dumps(auth, sort_keys=True), flush=True)
    # The external controller verifies auth logs before creating source+position.
    # It then verifies each stage before importing/activating/revoking owned rows.
    token = env["BRIDGE_API_TOKEN"]
    all_receipts = list(auth["receipts"])
    for phase, desired, waiting in (("baseline", "ABSTAIN", "MISSING"),
            ("active", "SUPPORTED", "ABSTAIN"), ("revoked", "ABSTAIN", "SUPPORTED"),
            ("reactivated", "SUPPORTED", "ABSTAIN")):
        reached = False
        for attempt in range(10 if phase == "baseline" else 8):
            status, data, receipt = v.request(client, PATH, token=token, body=body("3H"))
            accept(receipt)
            all_receipts.append(receipt)
            if status == 404 and data == {"detail": {"code": "POSITION_NOT_FOUND"}} and phase == "baseline":
                observed = "MISSING"
            elif status == 200:
                observed = assessment(data, "3H")
            else:
                raise v.Rejected("phase_response_rejected")
            if observed == desired:
                receipts, answers = [receipt], [{"call": "3H", "status": observed}]
                if desired == "SUPPORTED":
                    status, data, other = v.request(client, PATH, token=token, body=body("3S"))
                    accept(other)
                    all_receipts.append(other)
                    if status != 200 or assessment(data, "3S") != "CONTRADICTED":
                        raise v.Rejected("negative_case_rejected")
                    receipts.append(other)
                    answers.append({"call": "3S", "status": "CONTRADICTED"})
                emit(phase, receipts, answers)
                reached = True
                break
            if observed != waiting:
                raise v.Rejected("phase_order_rejected")
            emit("poll", [receipt])
            sleep(16 if phase == "baseline" else 8)
        if not reached:
            raise v.Rejected("phase_deadline_exceeded")
    if len({r["request_id"] for r in all_receipts}) != len(all_receipts):
        raise v.Rejected("duplicate_request_id")
    emit("all_requests", all_receipts)
    return {"status": "pending_external_final_acceptance", "requests": client.count}


def main():
    try:
        env = intent(os.environ)
        if env is None:
            print("canon-acceptance: inactive ordinary build")
            return 0
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(v.Rejected("build_deadline_exceeded")))
        signal.alarm(420)
        result = lifecycle(env)
        print(json.dumps(result, sort_keys=True), flush=True)
        # This is a validation-only build. Never promote on an unverified HTTP
        # result or on an operator timing window. The existing READY stays live.
        print("canon-acceptance: validation_complete_no_promotion", flush=True)
        return 1
    except v.Rejected as exc:
        print(json.dumps({"status": "STOP", "code": str(exc)}), flush=True)
        return 1
    except Exception:
        print(json.dumps({"status": "STOP", "code": "unexpected_failure"}), flush=True)
        return 1
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
