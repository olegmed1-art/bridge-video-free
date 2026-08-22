from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.request

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

PREFIX = "PILOT100-20260822-"
EXPECTED_POSITIONS = 100
SEATS = "NESW"


def connect():
    return psycopg.connect(os.environ["BRIDGE_APP_DATABASE_URL"], row_factory=dict_row)


def api(path: str, method: str = "GET", payload=None):
    base = os.environ.get("BRIDGE_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.environ["BRIDGE_API_TOKEN"]
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{base}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fingerprint(*parts: object) -> str:
    text = "|".join(json.dumps(part, ensure_ascii=False, sort_keys=True) if isinstance(part, (list, dict)) else str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_source_deals(cur):
    cur.execute(
        """
        SELECT
          tb.board_sequence_no,
          d.stable_key AS deal_key,
          d.dealer,
          d.vulnerability,
          d.hand_n, d.hand_e, d.hand_s, d.hand_w,
          t.scoring_type
        FROM public.tournament_board tb
        JOIN public.deal d ON d.deal_id=tb.deal_id
        JOIN public.tournament t ON t.tournament_id=tb.tournament_id
        WHERE t.provider_native_key='bridge.co.il:event:30041:round:2'
          AND d.reconstruction_status='verified_52'
          AND d.dealer IS NOT NULL
          AND d.vulnerability IS NOT NULL
          AND d.hand_n IS NOT NULL AND d.hand_e IS NOT NULL
          AND d.hand_s IS NOT NULL AND d.hand_w IS NOT NULL
        ORDER BY tb.board_sequence_no
        """
    )
    rows = cur.fetchall()
    if len(rows) != 24:
        raise RuntimeError(f"Pilot-100 expects exactly 24 verified tournament boards, found {len(rows)}")
    return rows


def configurations(rows):
    out = []
    for row in rows:
        dealer = row["dealer"].upper()
        if dealer not in SEATS:
            raise RuntimeError(f"unsupported dealer {dealer!r} on {row['deal_key']}")
        start = SEATS.index(dealer)
        hands = {
            "N": row["hand_n"],
            "E": row["hand_e"],
            "S": row["hand_s"],
            "W": row["hand_w"],
        }
        board = int(row["board_sequence_no"])
        for offset in range(4):
            seat = SEATS[(start + offset) % 4]
            auction = ["PASS"] * offset
            stable_key = f"{PREFIX}B{board:02d}-{seat}-MP"
            out.append({
                "stable_key": stable_key,
                "decision_type": "BIDDING_COUNTERFACTUAL_OPENING_ROUND",
                "seat": seat,
                "dealer": dealer,
                "vulnerability": row["vulnerability"],
                "scoring": "mp",
                "hand": hands[seat],
                "auction": auction,
                "system_us": "BEN_DEFAULT",
                "system_them": "BEN_DEFAULT",
                "source_deal": row["deal_key"],
                "provenance": "VERIFIED_DEAL_COUNTERFACTUAL_PRIOR_PASSES",
            })

    # Four scoring-sensitivity controls. Same verified deals and actual dealer/hand,
    # but evaluated under IMP scoring to test scoring routing independently of deal data.
    for row in rows[:4]:
        dealer = row["dealer"].upper()
        hands = {"N": row["hand_n"], "E": row["hand_e"], "S": row["hand_s"], "W": row["hand_w"]}
        board = int(row["board_sequence_no"])
        out.append({
            "stable_key": f"{PREFIX}B{board:02d}-{dealer}-IMP",
            "decision_type": "BIDDING_SCORING_SENSITIVITY_CONTROL",
            "seat": dealer,
            "dealer": dealer,
            "vulnerability": row["vulnerability"],
            "scoring": "imps",
            "hand": hands[dealer],
            "auction": [],
            "system_us": "BEN_DEFAULT",
            "system_them": "BEN_DEFAULT",
            "source_deal": row["deal_key"],
            "provenance": "VERIFIED_DEAL_COUNTERFACTUAL_SCORING",
        })

    if len(out) != EXPECTED_POSITIONS:
        raise RuntimeError(f"Pilot-100 configuration error: produced {len(out)} positions")
    return out


def seed() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT school_id FROM public.school WHERE stable_name=%s", ("Школа спортивного бриджа",))
        school = cur.fetchone()
        if not school:
            raise RuntimeError("school row not found")
        rows = load_source_deals(cur)
        configs = configurations(rows)

        # Idempotent isolated benchmark namespace; children cascade by schema contract.
        cur.execute("DELETE FROM ai.decision_position WHERE school_id=%s AND stable_key LIKE %s", (school["school_id"], PREFIX + "%"))

        position_ids = []
        for cfg in configs:
            meta = {
                "source_deal": cfg["source_deal"],
                "provenance": cfg["provenance"],
                "benchmark": "PILOT100-20260822",
            }
            fp = fingerprint(cfg["source_deal"], cfg["seat"], cfg["dealer"], cfg["vulnerability"], cfg["scoring"], cfg["hand"], cfg["auction"], cfg["system_us"], meta)
            cur.execute(
                """
                INSERT INTO ai.decision_position (
                    school_id, stable_key, decision_type, seat, dealer,
                    vulnerability, scoring, hand_pbn, auction_json,
                    system_us, system_them, input_status, position_fingerprint
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'COMPLETE',%s)
                RETURNING position_id
                """,
                (
                    school["school_id"], cfg["stable_key"], cfg["decision_type"], cfg["seat"], cfg["dealer"],
                    cfg["vulnerability"], cfg["scoring"], cfg["hand"], Jsonb(cfg["auction"]),
                    cfg["system_us"], cfg["system_them"], fp,
                ),
            )
            position_ids.append(cur.fetchone()["position_id"])
        conn.commit()

    queued = 0
    for position_id in position_ids:
        result = api(f"/v1/ai/positions/{position_id}/process", method="POST", payload={})
        if result.get("status") not in {"QUEUED", "SEARCH_PENDING", "CACHE_HIT"}:
            raise RuntimeError(f"unexpected process result for {position_id}: {result}")
        if result.get("status") == "QUEUED":
            queued += 1

    print(json.dumps({
        "seeded": len(position_ids),
        "queued": queued,
        "prefix": PREFIX,
        "source": "24 verified bridge.co.il tournament deals",
        "composition": {"MP_prior_pass_counterfactuals": 96, "IMP_scoring_controls": 4},
    }, ensure_ascii=False))


def wait_terminal(timeout_seconds: int = 900) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  count(*) AS total,
                  count(*) FILTER (WHERE sr.status IN ('QUEUED','RUNNING')) AS pending
                FROM ai.decision_position p
                LEFT JOIN LATERAL (
                  SELECT status FROM ai.search_run s
                  WHERE s.position_id=p.position_id
                  ORDER BY s.created_at DESC LIMIT 1
                ) sr ON true
                WHERE p.stable_key LIKE %s
                """,
                (PREFIX + "%",),
            )
            row = cur.fetchone()
        if int(row["total"]) == EXPECTED_POSITIONS and int(row["pending"]) == 0:
            return
        time.sleep(5)
    raise RuntimeError("Pilot-100 did not reach terminal search state before timeout")


def verify() -> None:
    wait_terminal()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH p AS (
              SELECT * FROM ai.decision_position WHERE stable_key LIKE %s
            ), latest_search AS (
              SELECT DISTINCT ON (s.position_id) s.position_id, s.status
              FROM ai.search_run s JOIN p ON p.position_id=s.position_id
              ORDER BY s.position_id, s.created_at DESC
            )
            SELECT
              (SELECT count(*) FROM p) AS positions,
              (SELECT count(*) FROM p WHERE input_status='COMPLETE') AS complete_positions,
              (SELECT count(*) FROM latest_search WHERE status='COMPLETED') AS search_completed,
              (SELECT count(*) FROM latest_search WHERE status='FAILED') AS search_failed,
              (SELECT count(*) FROM latest_search WHERE status IN ('QUEUED','RUNNING')) AS search_pending,
              (SELECT count(DISTINCT position_id) FROM ai.teacher_output WHERE position_id IN (SELECT position_id FROM p)) AS teacher_positions,
              (SELECT count(DISTINCT position_id) FROM ai.policy_run WHERE position_id IN (SELECT position_id FROM p)) AS policy_positions,
              (SELECT count(DISTINCT position_id) FROM ai.candidate_action WHERE position_id IN (SELECT position_id FROM p)) AS candidate_positions,
              (SELECT count(DISTINCT position_id) FROM ai.final_decision WHERE position_id IN (SELECT position_id FROM p)) AS finalized_positions
            """,
            (PREFIX + "%",),
        )
        summary = cur.fetchone()
        if int(summary["positions"]) != EXPECTED_POSITIONS:
            raise RuntimeError(f"expected {EXPECTED_POSITIONS} positions, got {summary['positions']}")
        if int(summary["complete_positions"]) != EXPECTED_POSITIONS:
            raise RuntimeError(f"not all Pilot-100 positions are COMPLETE: {summary}")
        if int(summary["search_pending"]) != 0:
            raise RuntimeError(f"Pilot-100 still has pending search jobs: {summary}")

        cur.execute(
            """
            SELECT decision_path, count(*) AS n
            FROM ai.final_decision f
            JOIN ai.decision_position p ON p.position_id=f.position_id
            WHERE p.stable_key LIKE %s
            GROUP BY decision_path ORDER BY n DESC
            """,
            (PREFIX + "%",),
        )
        paths = {row["decision_path"]: int(row["n"]) for row in cur.fetchall()}

        cur.execute(
            """
            SELECT chosen_action, count(*) AS n
            FROM ai.final_decision f
            JOIN ai.decision_position p ON p.position_id=f.position_id
            WHERE p.stable_key LIKE %s
            GROUP BY chosen_action ORDER BY n DESC, chosen_action
            """,
            (PREFIX + "%",),
        )
        actions = {row["chosen_action"]: int(row["n"]) for row in cur.fetchall()}

        cur.execute(
            """
            SELECT scoring, count(*) AS positions,
                   count(f.final_decision_id) AS finalized
            FROM ai.decision_position p
            LEFT JOIN ai.final_decision f ON f.position_id=p.position_id
            WHERE p.stable_key LIKE %s
            GROUP BY scoring ORDER BY scoring
            """,
            (PREFIX + "%",),
        )
        scoring = [dict(row) for row in cur.fetchall()]

    output = {
        "verified": True,
        "benchmark": "PILOT100-20260822",
        "summary": {key: int(value) for key, value in summary.items()},
        "decision_paths": paths,
        "chosen_actions": actions,
        "scoring_breakdown": scoring,
        "interpretation": "Terminal processing is required; non-finalized positions are retained as insufficient-evidence outcomes, not fabricated decisions.",
    }
    print(json.dumps(output, ensure_ascii=False, default=str))


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
