#!/usr/bin/env python3
"""Import audited tournament facts into Neon without creating student skill observations.

This importer implements the v1.4 tournament evidence boundary:
- facts / source observations / deal records / table results are allowed;
- participant identity may be linked only from an explicit target student name;
- ErrorObservation, SuccessObservation, SkillAssessment and profile writes are forbidden here.

The input file is a compact, human-reviewable JSON extract of an already-audited
school tournament report. It deliberately does not treat recommended auctions or
DDS conclusions as observed student actions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from database.runtime_worker_preflight import normalize_dsn

SCHOOL_STABLE_NAME = "Школа спортивного бриджа"
EXPECTED_SCHEMA = "bridge-tournament-facts-v1"
RANKS = set("AKQJT98765432")
CONTRACT_RE = re.compile(r"^[1-7](?:C|D|H|S|NT)(?:X|XX)?$")
CARD_RE = re.compile(r"^[SHDC][AKQJT98765432]$")


def _stable_uuid(kind: str, *parts: object) -> uuid.UUID:
    seed = "|".join(str(x) for x in parts)
    return uuid.uuid5(uuid.NAMESPACE_URL, f"bridge-school:{kind}:{seed}")


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _split_hand(value: str) -> list[str]:
    suits = value.split(".")
    if len(suits) != 4:
        raise ValueError(f"hand must have four suits: {value}")
    cards: list[str] = []
    for suit, ranks in zip("SHDC", suits):
        if ranks in {"", "-"}:
            continue
        for rank in ranks:
            if rank not in RANKS:
                raise ValueError(f"invalid rank {rank!r} in {value}")
            cards.append(suit + rank)
    if len(cards) != 13:
        raise ValueError(f"hand must contain 13 cards: {value} -> {len(cards)}")
    return cards


def _parse_row(columns: list[str], row: str) -> dict[str, Any]:
    values = row.split("|")
    if len(values) != len(columns):
        raise ValueError(f"row has {len(values)} fields, expected {len(columns)}: {row}")
    out = dict(zip(columns, values))
    out["board"] = int(out["board"])
    out["slide"] = int(out["slide"])
    out["result_delta"] = int(out["result_delta"]) if out["result_delta"] else None
    out["pair_score"] = int(out["pair_score"]) if out["pair_score"] else None
    out["pair_percentage"] = float(out["pair_percentage"]) if out["pair_percentage"] else None
    return out


def load_and_validate(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != EXPECTED_SCHEMA:
        raise ValueError(f"unexpected schema: {payload.get('schema')}")
    policy = payload.get("policy") or {}
    if policy.get("mode") != "FACTS_ONLY":
        raise ValueError("tournament import must be FACTS_ONLY")
    if policy.get("student_observation_writes_allowed") is not False:
        raise ValueError("student observation writes must be explicitly disabled")

    columns = list(payload.get("columns") or [])
    rows = [_parse_row(columns, row) for row in payload.get("rows") or []]
    if [r["board"] for r in rows] != list(range(1, 25)):
        raise ValueError("expected exactly boards 1..24 in order")

    status_counts = {"played": 0, "average": 0, "unplayed": 0}
    for row in rows:
        if row["dealer"] not in set("NESW"):
            raise ValueError(f"invalid dealer on board {row['board']}")
        if row["vulnerability"] not in {"None", "NS", "EW", "Both"}:
            raise ValueError(f"invalid vulnerability on board {row['board']}")
        all_cards: list[str] = []
        for seat in "NESW":
            all_cards.extend(_split_hand(row[seat]))
        if len(all_cards) != 52 or len(set(all_cards)) != 52:
            raise ValueError(f"board {row['board']} does not contain 52 unique cards")

        status = row["status"]
        if status not in status_counts:
            raise ValueError(f"invalid result status on board {row['board']}: {status}")
        status_counts[status] += 1
        if status == "unplayed":
            if any(row.get(k) for k in ("pair_direction", "contract", "declarer", "opening_lead", "pair_percentage")):
                raise ValueError(f"unplayed board {row['board']} carries result data")
            continue
        if row["pair_direction"] not in {"N–S", "E–W"}:
            raise ValueError(f"missing pair direction on board {row['board']}")
        if row["pair_percentage"] is None or not 0 <= row["pair_percentage"] <= 100:
            raise ValueError(f"invalid percentage on board {row['board']}")
        if status == "average":
            if row["contract"] or row["pair_score"] is not None:
                raise ValueError(f"average board {row['board']} must not invent contract/score")
            continue
        if not row["contract"] or not CONTRACT_RE.fullmatch(row["contract"]):
            raise ValueError(f"invalid contract on board {row['board']}: {row['contract']}")
        if row["declarer"] not in set("NESW"):
            raise ValueError(f"invalid declarer on board {row['board']}")
        if not row["opening_lead"] or not CARD_RE.fullmatch(row["opening_lead"]):
            raise ValueError(f"invalid opening lead on board {row['board']}")
        tricks = int(row["contract"][0]) + 6 + int(row["result_delta"])
        if not 0 <= tricks <= 13:
            raise ValueError(f"invalid trick count on board {row['board']}")

    tournament = payload.get("tournament") or {}
    expected = {
        "played": int(tournament.get("played_boards")),
        "average": int(tournament.get("average_results")),
        "unplayed": int(tournament.get("unplayed_boards")),
    }
    if status_counts != expected:
        raise ValueError(f"status counts mismatch: {status_counts} != {expected}")
    if status_counts["played"] + status_counts["average"] != int(tournament.get("counted_results")):
        raise ValueError("counted result total mismatch")
    target = str(tournament.get("target_student_name") or "").strip()
    if not target:
        raise ValueError("explicit tournament.target_student_name is required for identity attribution")
    if target not in list(tournament.get("members") or []):
        raise ValueError("target_student_name is not one of the reported pair members")

    payload["parsed_rows"] = rows
    payload["validation"] = {
        "boards": len(rows),
        "status_counts": status_counts,
        "counted_results": status_counts["played"] + status_counts["average"],
        "student_observation_writes_allowed": False,
    }
    return payload


def _event_parts(provider_key: str) -> tuple[str, str]:
    m = re.fullmatch(r"bridge\.co\.il:event:([^:]+):round:([^:]+)", provider_key)
    if not m:
        raise ValueError(f"unsupported tournament provider key: {provider_key}")
    return m.group(1), m.group(2)


def import_facts(raw_dsn: str, data: dict[str, Any]) -> dict[str, Any]:
    dsn = normalize_dsn(raw_dsn)
    source = data["source"]
    tournament = data["tournament"]
    rows = data["parsed_rows"]
    event_no, round_no = _event_parts(tournament["provider_native_key"])
    pair_number = str(tournament["pair_number"])
    target_student_name = str(tournament["target_student_name"])

    source_id = _stable_uuid("source", "google-drive", source["drive_id"])
    source_asset_identity_key = f"google-drive:file:{source['drive_id']}"
    tournament_id = _stable_uuid("tournament", tournament["provider_native_key"])
    participation_id = _stable_uuid("tournament-participation", tournament_id, tournament["pair_key"])

    with psycopg.connect(dsn, connect_timeout=10, application_name="bridge-tournament-facts-import") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT school_id FROM public.school WHERE stable_name=%s", (SCHOOL_STABLE_NAME,))
            school_rows = cur.fetchall()
            if len(school_rows) != 1:
                raise RuntimeError("expected exactly one bridge school row")
            school_id = school_rows[0][0]

            cur.execute(
                """
                SELECT p.person_id, s.student_id
                  FROM public.person p
                  JOIN public.student s ON s.person_id=p.person_id
                 WHERE p.preferred_name=%s AND s.current_status='active'
                """,
                (target_student_name,),
            )
            student_rows = cur.fetchall()
            if len(student_rows) != 1:
                raise RuntimeError("target student exact-name identity must resolve to exactly one active student")
            target_person_id, target_student_id = student_rows[0]

            def profile_counts() -> dict[str, int]:
                cur.execute(
                    """
                    SELECT
                      (SELECT count(*) FROM public.error_observation WHERE student_id=%s),
                      (SELECT count(*) FROM public.success_observation WHERE student_id=%s),
                      (SELECT count(*) FROM public.skill_assessment WHERE student_id=%s)
                    """,
                    (target_student_id, target_student_id, target_student_id),
                )
                a, b, c = cur.fetchone()
                return {"error_observation": int(a), "success_observation": int(b), "skill_assessment": int(c)}

            profile_before = profile_counts()

            cur.execute(
                """
                INSERT INTO public.source
                    (source_id, school_id, source_type, title, canonical_locator, trust_class, rights_notes, status)
                VALUES (%s,%s,'tournament_report',%s,%s,'derived_checked',%s,'active')
                ON CONFLICT (source_id) DO NOTHING
                """,
                (
                    source_id,
                    school_id,
                    source["title"],
                    f"drive:{source['drive_id']}",
                    "Derived school tournament report; official bridge.co.il URLs are preserved in tournament/source-observation metadata.",
                ),
            )

            source_provider_identity_id = _stable_uuid("source-identity", source_id, source_asset_identity_key)
            cur.execute(
                """
                INSERT INTO public.source_identity
                    (source_identity_id, source_id, source_native_key, display_name, attributes)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (source_id, source_native_key) DO NOTHING
                """,
                (
                    source_provider_identity_id,
                    source_id,
                    source_asset_identity_key,
                    source["title"],
                    Jsonb({
                        "provider": "google_drive",
                        "drive_file_id": source["drive_id"],
                        "sha256": source["sha256"],
                        "size_bytes": source["size_bytes"],
                        "identity_scope": "source_asset",
                    }),
                ),
            )

            tournament_meta = {
                "location": tournament.get("location"),
                "event_date": tournament.get("date"),
                "official_session_url": source.get("official_session_url"),
                "official_personal_url": source.get("official_personal_url"),
                "source_drive_id": source["drive_id"],
                "source_sha256": source["sha256"],
                "report_algorithm_version": "1.2",
                "ingestion_policy_version": "tournament-facts-v1",
                "final_pair_percentage": tournament["final_percentage"],
                "rank": tournament["rank"],
                "field_size": tournament["field_size"],
                "counted_results": tournament["counted_results"],
                "played_boards": tournament["played_boards"],
                "average_results": tournament["average_results"],
                "unplayed_boards": tournament["unplayed_boards"],
                "student_observation_writes_allowed": False,
            }
            cur.execute(
                """
                INSERT INTO public.tournament
                    (tournament_id, school_id, source_id, provider_native_key, name,
                     event_format, scoring_type, temporal_precision, status, metadata)
                VALUES (%s,%s,%s,%s,%s,'pairs',%s,'day','active',%s)
                ON CONFLICT (source_id, provider_native_key) DO NOTHING
                """,
                (
                    tournament_id,
                    school_id,
                    source_id,
                    tournament["provider_native_key"],
                    f"Турнир №{event_no} · сессия {round_no}",
                    tournament["scoring"],
                    Jsonb(tournament_meta),
                ),
            )

            participation_meta = {
                "final_percentage": tournament["final_percentage"],
                "rank": tournament["rank"],
                "field_size": tournament["field_size"],
                "counted_results": tournament["counted_results"],
                "played_boards": tournament["played_boards"],
                "average_results": tournament["average_results"],
                "unplayed_boards": tournament["unplayed_boards"],
            }
            cur.execute(
                """
                INSERT INTO public.tournament_participation
                    (tournament_participation_id, tournament_id, source_native_key,
                     entry_type, entry_number, pair_number, entry_label, status, metadata)
                VALUES (%s,%s,%s,'pair',%s,%s,%s,'active',%s)
                ON CONFLICT (tournament_id, source_native_key) DO NOTHING
                """,
                (
                    participation_id,
                    tournament_id,
                    tournament["pair_key"],
                    pair_number,
                    pair_number,
                    " — ".join(tournament["members"]),
                    Jsonb(participation_meta),
                ),
            )

            member_rows: list[tuple[str, uuid.UUID, uuid.UUID]] = []
            for member_no, display_name in enumerate(tournament["members"], 1):
                native_key = f"bridge.co.il:event:{event_no}:round:{round_no}:seat:{pair_number}:member:{member_no}"
                source_identity_id = _stable_uuid("source-identity", source_id, native_key)
                cur.execute(
                    """
                    INSERT INTO public.source_identity
                        (source_identity_id, source_id, source_native_key, display_name, attributes)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (source_id, source_native_key) DO NOTHING
                    """,
                    (
                        source_identity_id,
                        source_id,
                        native_key,
                        display_name,
                        Jsonb({
                            "provider": "bridge.co.il",
                            "event": event_no,
                            "round": round_no,
                            "pair_number": pair_number,
                            "member_no": member_no,
                            "identity_scope": "tournament_participant",
                            "source_basis": "teacher_tournament_report_v1.2",
                        }),
                    ),
                )
                cur.execute(
                    "SELECT source_identity_id FROM public.source_identity WHERE source_id=%s AND source_native_key=%s",
                    (source_id, native_key),
                )
                actual_source_identity_id = cur.fetchone()[0]
                member_id = _stable_uuid("tournament-participant-member", participation_id, actual_source_identity_id)
                cur.execute(
                    """
                    INSERT INTO public.tournament_participant_member
                        (tournament_participant_member_id, tournament_participation_id,
                         source_identity_id, member_no, member_role, source_native_key, metadata)
                    VALUES (%s,%s,%s,%s,'player',%s,%s)
                    ON CONFLICT (tournament_participation_id, source_identity_id) DO NOTHING
                    """,
                    (
                        member_id,
                        participation_id,
                        actual_source_identity_id,
                        member_no,
                        native_key,
                        Jsonb({
                            "display_name": display_name,
                            "pair_number": pair_number,
                            "direction_varies_by_board": True,
                        }),
                    ),
                )
                cur.execute(
                    """
                    SELECT tournament_participant_member_id
                      FROM public.tournament_participant_member
                     WHERE tournament_participation_id=%s AND source_identity_id=%s
                    """,
                    (participation_id, actual_source_identity_id),
                )
                actual_member_id = cur.fetchone()[0]
                member_rows.append((display_name, actual_source_identity_id, actual_member_id))

            target_matches = [row for row in member_rows if row[0] == target_student_name]
            if len(target_matches) != 1:
                raise RuntimeError("target student must match exactly one tournament member")
            _, target_source_identity_id, target_member_id = target_matches[0]

            identity_evidence_id = _stable_uuid("evidence", "tournament-identity", tournament_id, target_source_identity_id)
            cur.execute(
                """
                INSERT INTO public.evidence
                    (evidence_id, school_id, evidence_type, source_id, locator,
                     confidence_class, quality_status)
                VALUES (%s,%s,'document_text',%s,%s,'HIGH','confirmed')
                ON CONFLICT (evidence_id) DO NOTHING
                """,
                (
                    identity_evidence_id,
                    school_id,
                    source_id,
                    Jsonb({
                        "drive_file_id": source["drive_id"],
                        "slide_no": 1,
                        "field": "pair_members",
                        "fact": " — ".join(tournament["members"]),
                        "official_personal_url": source.get("official_personal_url"),
                    }),
                ),
            )
            resolution_id = _stable_uuid("entity-resolution", target_source_identity_id, target_person_id)
            cur.execute(
                """
                INSERT INTO public.entity_resolution_decision
                    (resolution_id, source_identity_id, target_person_id, decision_type,
                     confidence_class, evidence_ids, status)
                VALUES (%s,%s,%s,'link','HIGH',%s,'active')
                ON CONFLICT (resolution_id) DO NOTHING
                """,
                (resolution_id, target_source_identity_id, target_person_id, [identity_evidence_id]),
            )
            attribution_id = _stable_uuid("tournament-identity-attribution", target_member_id, resolution_id)
            cur.execute(
                """
                INSERT INTO public.tournament_identity_attribution
                    (tournament_identity_attribution_id, tournament_participant_member_id,
                     entity_resolution_decision_id, person_id, student_id,
                     confidence_class, attribution_method)
                VALUES (%s,%s,%s,%s,%s,'HIGH','exact_full_name_in_teacher_tournament_report')
                ON CONFLICT (tournament_participant_member_id, entity_resolution_decision_id) DO NOTHING
                """,
                (attribution_id, target_member_id, resolution_id, target_person_id, target_student_id),
            )

            result_rows_written = 0
            for row in rows:
                board = row["board"]
                stable_key = f"bridge.co.il:event:{event_no}:round:{round_no}:board:{board}"
                official_board_url = (
                    f"https://www.bridge.co.il/viewer/board.php?event={event_no}&round={round_no}"
                    f"&board={board}&seat={pair_number}"
                )
                obs_payload = {
                    "board_number": str(board),
                    "slide_no": row["slide"],
                    "dealer": row["dealer"],
                    "vulnerability": row["vulnerability"],
                    "hands": {seat: row[seat] for seat in "NESW"},
                    "result_status": row["status"],
                    "pair_direction": row["pair_direction"] or None,
                    "contract": row["contract"] or None,
                    "declarer": row["declarer"] or None,
                    "result_delta": row["result_delta"],
                    "opening_lead": row["opening_lead"] or None,
                    "pair_score": row["pair_score"],
                    "pair_percentage": row["pair_percentage"],
                    "official_board_url": official_board_url,
                    "source_status": "confirmed",
                    "auction_status": "recommended",
                    "play_status": "absent",
                    "decision_status": "recommended",
                    "personal_observation_eligible": False,
                }
                obs_hash = _digest(obs_payload)
                source_observation_id = _stable_uuid("source-observation", source_id, stable_key, obs_hash)
                cur.execute(
                    """
                    INSERT INTO public.source_observation
                        (source_observation_id, source_id, provider_native_key, provider_revision,
                         payload_hash, payload, status)
                    VALUES (%s,%s,%s,'report-v1.2',%s,%s,'observed')
                    ON CONFLICT (source_id, provider_native_key, payload_hash) DO NOTHING
                    """,
                    (source_observation_id, source_id, stable_key, obs_hash, Jsonb(obs_payload)),
                )
                cur.execute(
                    """
                    SELECT source_observation_id FROM public.source_observation
                     WHERE source_id=%s AND provider_native_key=%s AND payload_hash=%s
                    """,
                    (source_id, stable_key, obs_hash),
                )
                source_observation_id = cur.fetchone()[0]

                deal_payload = {
                    "N": row["N"], "E": row["E"], "S": row["S"], "W": row["W"],
                    "dealer": row["dealer"], "vulnerability": row["vulnerability"],
                }
                deal_id = _stable_uuid("deal", stable_key)
                cur.execute(
                    """
                    INSERT INTO public.deal
                        (deal_id, school_id, canonical_pbn, hand_n, hand_e, hand_s, hand_w,
                         dealer, vulnerability, reconstruction_status, deal_fingerprint,
                         source_id, stable_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'verified_52',%s,%s,%s)
                    ON CONFLICT (school_id, stable_key) DO NOTHING
                    """,
                    (
                        deal_id,
                        school_id,
                        f"N:{row['N']} E:{row['E']} S:{row['S']} W:{row['W']}",
                        row["N"], row["E"], row["S"], row["W"],
                        row["dealer"], row["vulnerability"], _digest(deal_payload), source_id, stable_key,
                    ),
                )
                cur.execute("SELECT deal_id FROM public.deal WHERE school_id=%s AND stable_key=%s", (school_id, stable_key))
                deal_id = cur.fetchone()[0]

                tournament_board_id = _stable_uuid("tournament-board", tournament_id, stable_key)
                board_meta = {
                    "slide_no": row["slide"],
                    "official_board_url": official_board_url,
                    "source_status": "confirmed",
                    "auction_status": "recommended",
                    "play_status": "absent",
                    "decision_status": "recommended",
                    "slide_mode": "tournament",
                    "personal_observation_eligible": False,
                }
                cur.execute(
                    """
                    INSERT INTO public.tournament_board
                        (tournament_board_id, tournament_id, source_observation_id,
                         source_native_key, board_number, board_sequence_no, deal_id,
                         dealer_override, vulnerability_override, metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tournament_id, source_native_key) DO NOTHING
                    """,
                    (
                        tournament_board_id, tournament_id, source_observation_id,
                        stable_key, str(board), board, deal_id,
                        row["dealer"], row["vulnerability"], Jsonb(board_meta),
                    ),
                )
                cur.execute(
                    "SELECT tournament_board_id FROM public.tournament_board WHERE tournament_id=%s AND source_native_key=%s",
                    (tournament_id, stable_key),
                )
                tournament_board_id = cur.fetchone()[0]

                if row["status"] == "unplayed":
                    continue
                pair_pct = float(row["pair_percentage"])
                pair_direction = row["pair_direction"]
                if pair_direction == "E–W":
                    pct_ew, pct_ns = pair_pct, 100.0 - pair_pct
                    ew_participation_id, ns_participation_id = participation_id, None
                    raw_score_ns = -row["pair_score"] if row["pair_score"] is not None else None
                else:
                    pct_ns, pct_ew = pair_pct, 100.0 - pair_pct
                    ns_participation_id, ew_participation_id = participation_id, None
                    raw_score_ns = row["pair_score"]
                tricks_taken = None
                if row["contract"] and row["result_delta"] is not None:
                    tricks_taken = int(row["contract"][0]) + 6 + int(row["result_delta"])

                result_payload = {
                    "board_number": str(board),
                    "result_status": row["status"],
                    "pair_direction": pair_direction,
                    "contract": row["contract"] or None,
                    "declarer": row["declarer"] or None,
                    "result_delta": row["result_delta"],
                    "opening_lead": row["opening_lead"] or None,
                    "pair_score": row["pair_score"],
                    "pair_percentage": pair_pct,
                    "source_status": "confirmed",
                    "auction_status": "recommended",
                    "play_status": "absent",
                    "decision_status": "recommended",
                    "personal_observation_eligible": False,
                }
                result_hash = _digest(result_payload)
                provider_result_key = f"{stable_key}:seat:{pair_number}:result"
                result_id = _stable_uuid("table-result", source_id, provider_result_key, result_hash)
                result_meta = {
                    "slide_no": row["slide"],
                    "official_board_url": official_board_url,
                    "pair_direction": pair_direction,
                    "result_status": row["status"],
                    "source_status": "confirmed",
                    "auction_status": "recommended",
                    "play_status": "absent",
                    "decision_status": "recommended",
                    "personal_observation_eligible": False,
                    "student_observation_write": "DENY",
                }
                cur.execute(
                    """
                    INSERT INTO public.table_result
                        (result_id, school_id, tournament_board_id, source_id, source_observation_id,
                         provider_native_key, payload_hash, record_kind,
                         ns_participation_id, ew_participation_id,
                         contract, declarer, opening_lead, tricks_taken, result_delta, raw_score_ns,
                         percentage_ns, percentage_ew, scoring_payload, metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'observed',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (source_id, provider_native_key, payload_hash) DO NOTHING
                    """,
                    (
                        result_id, school_id, tournament_board_id, source_id, source_observation_id,
                        provider_result_key, result_hash, ns_participation_id, ew_participation_id,
                        row["contract"] or None, row["declarer"] or None, row["opening_lead"] or None,
                        tricks_taken, row["result_delta"], raw_score_ns,
                        pct_ns, pct_ew, Jsonb(result_payload), Jsonb(result_meta),
                    ),
                )
                result_rows_written += 1

            profile_after = profile_counts()
            if profile_after != profile_before:
                raise RuntimeError(f"facts-only import changed student learning observations: {profile_before} -> {profile_after}")

            cur.execute("SELECT count(*) FROM public.tournament_board WHERE tournament_id=%s", (tournament_id,))
            board_count = int(cur.fetchone()[0])
            cur.execute("SELECT count(*) FROM public.table_result WHERE source_id=%s", (source_id,))
            result_count = int(cur.fetchone()[0])
            cur.execute("SELECT count(*) FROM public.tournament_participant_member WHERE tournament_participation_id=%s", (participation_id,))
            member_count = int(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM public.tournament_identity_attribution WHERE tournament_participant_member_id=%s AND student_id=%s",
                (target_member_id, target_student_id),
            )
            attribution_count = int(cur.fetchone()[0])
            if (board_count, result_count, member_count, attribution_count) != (24, 22, 2, 1):
                raise RuntimeError(
                    f"tournament import verification failed: boards={board_count} results={result_count} "
                    f"members={member_count} attributions={attribution_count}"
                )

        conn.commit()

    return {
        "status": "TOURNAMENT_FACTS_IMPORTED",
        "source_id": str(source_id),
        "tournament_id": str(tournament_id),
        "participation_id": str(participation_id),
        "boards": 24,
        "table_results": 22,
        "members": 2,
        "target_student_attributions": 1,
        "student_observation_writes": 0,
        "profile_counts_before": profile_before,
        "profile_counts_after": profile_after,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    data = load_and_validate(args.data)
    if args.validate_only:
        print(json.dumps({"status": "VALID", **data["validation"]}, ensure_ascii=False, sort_keys=True))
        return
    raw_dsn = os.getenv("BRIDGE_WORKER_DATABASE_URL", "").strip()
    if not raw_dsn:
        raise SystemExit("BRIDGE_WORKER_DATABASE_URL is required")
    print(json.dumps(import_facts(raw_dsn, data), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
