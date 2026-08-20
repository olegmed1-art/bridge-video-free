from __future__ import annotations

import argparse
import json
import os
import urllib.request

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

STABLE_KEY = "E2E-BEN-CANARY-0001"
HAND = "AK97543.K.T3.AK7"
EXPECTED_BID = "1S"


def api(path: str, method: str = "GET", payload=None):
    base = os.environ.get("BRIDGE_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.environ["BRIDGE_API_TOKEN"]
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def connect():
    return psycopg.connect(os.environ["BRIDGE_APP_DATABASE_URL"], row_factory=dict_row)


def seed() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT school_id FROM public.school WHERE stable_name=%s", ("Школа спортивного бриджа",))
        school = cur.fetchone()
        if not school:
            raise RuntimeError("school row not found")

        cur.execute("DELETE FROM ai.decision_position WHERE school_id=%s AND stable_key=%s", (school["school_id"], STABLE_KEY))
        cur.execute(
            """
            INSERT INTO ai.decision_position (
                school_id, stable_key, decision_type, seat, dealer,
                vulnerability, scoring, hand_pbn, auction_json,
                system_us, system_them, input_status, position_fingerprint
            ) VALUES (%s,%s,'BIDDING','S','N','', 'imps', %s, %s, 'BEN_DEFAULT', 'BEN_DEFAULT', 'COMPLETE', %s)
            RETURNING position_id
            """,
            (
                school["school_id"],
                STABLE_KEY,
                HAND,
                Jsonb(["PASS", "PASS"]),
                "e2e-ben-canary-v1",
            ),
        )
        position_id = cur.fetchone()["position_id"]
        conn.commit()

    queued = api(
        f"/v1/ai/positions/{position_id}/search-runs",
        method="POST",
        payload={"sampler_key": "ben-e2e", "sampler_version": "v1", "rollout_policy": "ben", "evaluator_key": "dds", "scoring": "imps"},
    )
    print(json.dumps({"seeded": True, "position_id": str(position_id), "search_run_id": str(queued["search_run"]["search_run_id"])}, ensure_ascii=False))


def verify() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT position_id FROM ai.decision_position WHERE stable_key=%s ORDER BY created_at DESC LIMIT 1", (STABLE_KEY,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError("canary position missing")
        position_id = row["position_id"]

    first = api(f"/v1/ai/positions/{position_id}/finalize", method="POST", payload={})
    if first.get("status") not in {"FINALIZED", "CACHE_HIT"}:
        raise RuntimeError(f"finalizer did not finalize canary: {first}")
    decision = first["decision"]
    if decision["chosen_action"] != EXPECTED_BID:
        raise RuntimeError(f"BEN canary drift: expected {EXPECTED_BID}, got {decision['chosen_action']}")
    if decision["decision_path"] not in {"POLICY_ONLY", "SEARCH"}:
        raise RuntimeError(f"unexpected finalization path: {decision['decision_path']}")

    second = api(f"/v1/ai/positions/{position_id}/finalize", method="POST", payload={})
    if second.get("status") != "CACHE_HIT":
        raise RuntimeError(f"second finalization was not cache hit: {second}")
    if second["decision"]["final_decision_id"] != decision["final_decision_id"]:
        raise RuntimeError("cache returned a different final decision")

    route = api(f"/v1/ai/positions/{position_id}/route")
    if route.get("route") != "CACHE_HIT":
        raise RuntimeError(f"position route is not cache hit after finalization: {route}")

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM ai.teacher_output WHERE position_id=%s) AS teachers,
              (SELECT count(*) FROM ai.policy_run WHERE position_id=%s) AS policies,
              (SELECT count(*) FROM ai.candidate_action WHERE position_id=%s) AS candidates,
              (SELECT status FROM ai.search_run WHERE position_id=%s ORDER BY created_at DESC LIMIT 1) AS search_status
            """,
            (position_id, position_id, position_id, position_id),
        )
        evidence = cur.fetchone()
    if int(evidence["teachers"]) < 1 or int(evidence["policies"]) < 1 or int(evidence["candidates"]) < 1:
        raise RuntimeError(f"incomplete BEN evidence chain: {evidence}")
    if evidence["search_status"] not in {"FAILED", "COMPLETED"}:
        raise RuntimeError(f"search run did not terminate: {evidence}")

    print(json.dumps({
        "verified": True,
        "position_id": str(position_id),
        "chosen_action": decision["chosen_action"],
        "decision_path": decision["decision_path"],
        "search_status": evidence["search_status"],
        "teachers": int(evidence["teachers"]),
        "policies": int(evidence["policies"]),
        "candidates": int(evidence["candidates"]),
        "cache": "PASS",
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["seed", "verify"])
    args = parser.parse_args()
    if args.mode == "seed":
        seed()
    else:
        verify()


if __name__ == "__main__":
    main()
