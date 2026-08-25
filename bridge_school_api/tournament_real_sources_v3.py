from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .tournament_adapters_v3 import NormalizedTournamentBatch, TournamentAdapterError, normalize_structured_batch
from .tournament_analyzer_v3 import (
    AnalysisFinding,
    Evidence,
    EvidenceKind,
    Observability,
    TournamentAnalysis,
    TournamentDeal,
    analyze_tournament,
)
from .tournament_longitudinal_v3 import LongitudinalReport, build_longitudinal_report

RANKS = "AKQJT98765432"
PBN_SUITS = "SHDC"
EXPECTED_30041_SOURCE_SHA256 = "9285e27c56906bdc18f729045927c9bbbcc45146127d99420bdd4e1834264e9f"
EXPECTED_30041_PROVIDER_KEY = "bridge.co.il:event:30041:round:2"
EXPECTED_29912_SESSIONS = (1, 2, 4, 5, 6)
EXPECTED_29912_SOURCE_INCONSISTENCY = (1, 5)

PAIR_SAME_CONTRACT_REPEAT_KEY = "DDS3_PAIR_SAME_CONTRACT_DELTA_V1"
DIANA_OPENING_LEAD_REPEAT_KEY = "DDS3_DIANA_OPENING_LEAD_REGRET_V1"
DIANA_DECLARER_POST_LEAD_REPEAT_KEY = "DDS3_DIANA_DECLARER_POST_LEAD_SHORTFALL_V1"


@dataclass(frozen=True)
class RealTournamentEvidence:
    analysis_30041: TournamentAnalysis
    analysis_29912: TournamentAnalysis
    longitudinal: LongitudinalReport
    source_summary: Mapping[str, Any]


def pbn_hand_to_cards(value: str) -> tuple[str, ...]:
    """Convert a bridge.co.il/PBN-style S.H.D.C hand into core rank+suit tokens."""
    parts = str(value).strip().upper().split(".")
    if len(parts) != 4:
        raise TournamentAdapterError(f"PBN hand must have four suit fields: {value!r}")
    cards: list[str] = []
    for suit, ranks in zip(PBN_SUITS, parts, strict=True):
        ranks = "" if ranks == "-" else ranks
        for rank in ranks:
            if rank not in RANKS:
                raise TournamentAdapterError(f"invalid PBN rank {rank!r} in {value!r}")
            cards.append(rank + suit)
    if len(cards) != 13 or len(set(cards)) != 13:
        raise TournamentAdapterError(f"PBN hand must contain 13 unique cards: {value!r}")
    return tuple(cards)


def _deal_hands_from_pbn(hands: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    if set(str(k).upper() for k in hands) != {"N", "E", "S", "W"}:
        raise TournamentAdapterError("hands must contain exactly N/E/S/W")
    return {seat: pbn_hand_to_cards(str(hands[seat])) for seat in ("N", "E", "S", "W")}


def _split_source_row(columns: Sequence[str], row: str) -> dict[str, str]:
    values = str(row).split("|")
    if len(values) != len(columns):
        raise TournamentAdapterError(f"source row has {len(values)} fields, expected {len(columns)}")
    return dict(zip(columns, values, strict=True))


def normalize_30041_facts(source: Mapping[str, Any]) -> NormalizedTournamentBatch:
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise TournamentAdapterError("unsupported 30041 facts schema")
    tournament = source.get("tournament")
    policy = source.get("policy")
    if not isinstance(tournament, Mapping) or tournament.get("provider_native_key") != EXPECTED_30041_PROVIDER_KEY:
        raise TournamentAdapterError("unexpected 30041 tournament identity")
    if not isinstance(policy, Mapping) or policy.get("mode") != "FACTS_ONLY":
        raise TournamentAdapterError("30041 source must remain FACTS_ONLY")
    if policy.get("student_observation_writes_allowed") is not False:
        raise TournamentAdapterError("30041 source must forbid student observation writes")
    columns = source.get("columns")
    rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise TournamentAdapterError("30041 columns must be a sequence")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or len(rows) != 24:
        raise TournamentAdapterError("30041 audited extract must contain exactly 24 boards")

    normalized_rows: list[dict[str, Any]] = []
    for raw in rows:
        item = _split_source_row(list(columns), str(raw))
        board_number = int(item["board"])
        hands = {seat: pbn_hand_to_cards(item[seat]) for seat in ("N", "E", "S", "W")}
        normalized_rows.append(
            {
                "board_number": board_number,
                "hands": hands,
                "dealer": item.get("dealer") or None,
                "vulnerability": item.get("vulnerability") or None,
                "contract": item.get("contract") or None,
                "declarer": item.get("declarer") or None,
                "opening_lead": item.get("opening_lead") or None,
                "score": int(item["pair_score"]) if item.get("pair_score") else None,
                "auction": None,
                "play_record": None,
                "_source": {
                    "status": item.get("status") or None,
                    "pair_direction": item.get("pair_direction") or None,
                    "pair_percentage": float(item["pair_percentage"]) if item.get("pair_percentage") else None,
                    "result_delta": int(item["result_delta"]) if item.get("result_delta") else None,
                    "slide": int(item["slide"]) if item.get("slide") else None,
                },
            }
        )

    batch = normalize_structured_batch(
        normalized_rows,
        event_id="30041",
        session_id="round-2",
        scoring="MP",
        source={"kind": "audited_extract", "provider_native_key": EXPECTED_30041_PROVIDER_KEY},
    )
    # normalize_structured_batch intentionally keeps only generic provenance; append source-row facts
    deals: list[TournamentDeal] = []
    for deal, row in zip(batch.deals, normalized_rows, strict=True):
        deals.append(
            TournamentDeal(
                **{
                    **deal.__dict__,
                    "source_provenance": {**dict(deal.source_provenance), **dict(row["_source"])},
                }
            )
        )
    return NormalizedTournamentBatch(
        event_id=batch.event_id,
        session_id=batch.session_id,
        deals=tuple(deals),
        scoring=batch.scoring,
        source=batch.source,
    )


def validate_30041_dds3_report(report: Mapping[str, Any], *, source_sha256: str) -> None:
    if source_sha256 != EXPECTED_30041_SOURCE_SHA256:
        raise TournamentAdapterError("30041 source SHA-256 is not the audited source")
    if report.get("schema") != "bridge-dds3-tournament-baseline-v1" or report.get("mode") != "FACTS_ONLY_DDS3_BASELINE":
        raise TournamentAdapterError("unsupported 30041 DDS3 evidence schema")
    if report.get("source_sha256") != source_sha256:
        raise TournamentAdapterError("30041 DDS3 evidence is not bound to the source bytes")
    policy = report.get("policy")
    summary = report.get("summary")
    boards = report.get("boards")
    if not isinstance(policy, Mapping) or policy.get("engine") != "DDS3" or policy.get("fallback_used") is not False:
        raise TournamentAdapterError("30041 DDS3 evidence must be canonical and no-fallback")
    if policy.get("card_level_attribution_allowed") is not False or policy.get("student_skill_writes_allowed") is not False:
        raise TournamentAdapterError("30041 evidence boundary was weakened")
    if not isinstance(summary, Mapping) or summary.get("boards_total") != 24 or summary.get("played_contracts_compared") != 21:
        raise TournamentAdapterError("30041 DDS3 summary counts changed")
    if not isinstance(boards, Sequence) or isinstance(boards, (str, bytes)) or len(boards) != 24:
        raise TournamentAdapterError("30041 DDS3 evidence must contain 24 boards")
    for board in boards:
        dds = board.get("dds3") if isinstance(board, Mapping) else None
        if not isinstance(dds, Mapping) or dds.get("engine") != "DDS3" or dds.get("fallback_used") is not False or dds.get("input_validated") is not True:
            raise TournamentAdapterError("non-canonical 30041 board DDS3 provenance")


def validate_29912_report_contract(report: Mapping[str, Any]) -> None:
    if report.get("schema") != "diana-29912-multi-session-dds3-v2":
        raise TournamentAdapterError("unsupported 29912 evidence schema")
    policy = report.get("policy")
    aggregate = report.get("aggregate")
    sessions = report.get("sessions")
    if not isinstance(policy, Mapping):
        raise TournamentAdapterError("missing 29912 policy")
    if policy.get("engine") != "DDS3" or policy.get("fallback_used") is not False or policy.get("site_dd_used") is not False:
        raise TournamentAdapterError("29912 evidence must be canonical DDS3, no fallback, no site DD")
    if policy.get("full_play_records_available") is not False or policy.get("auction_records_available") is not False:
        raise TournamentAdapterError("29912 observability boundary changed")
    if not isinstance(aggregate, Mapping):
        raise TournamentAdapterError("missing 29912 aggregate")
    if tuple(aggregate.get("sessions", ())) != EXPECTED_29912_SESSIONS:
        raise TournamentAdapterError("29912 session set changed")
    if aggregate.get("played_boards") != 100 or aggregate.get("decision_analyzable_boards") != 99:
        raise TournamentAdapterError("29912 audited board counts changed")
    bad = aggregate.get("source_inconsistencies")
    if not isinstance(bad, Sequence) or len(bad) != 1:
        raise TournamentAdapterError("29912 source inconsistency set changed")
    if (int(bad[0].get("round")), int(bad[0].get("board"))) != EXPECTED_29912_SOURCE_INCONSISTENCY:
        raise TournamentAdapterError("unexpected 29912 source inconsistency")
    if not isinstance(sessions, Sequence) or len(sessions) != 5:
        raise TournamentAdapterError("29912 evidence must contain five sessions")


def normalize_29912_report(report: Mapping[str, Any]) -> tuple[TournamentDeal, ...]:
    validate_29912_report_contract(report)
    deals: list[TournamentDeal] = []
    for session in report["sessions"]:
        round_no = int(session["round"])
        if round_no not in EXPECTED_29912_SESSIONS:
            raise TournamentAdapterError(f"unexpected 29912 round {round_no}")
        rows: list[dict[str, Any]] = []
        for board in session["boards"]:
            hands = board.get("hands")
            if not isinstance(hands, Mapping):
                raise TournamentAdapterError("29912 board missing hands")
            source_consistency = board.get("source_consistency") or {}
            rows.append(
                {
                    "board_number": int(board["board"]),
                    "hands": _deal_hands_from_pbn(hands),
                    "dealer": board.get("dealer"),
                    "vulnerability": board.get("vulnerability"),
                    "contract": board.get("contract"),
                    "declarer": board.get("declarer"),
                    "opening_lead": board.get("opening_lead"),
                    "score": board.get("pair_score"),
                    "auction": None,
                    "play_record": None,
                    "_source": {
                        "pair_direction": board.get("pair_direction"),
                        "pair_matchpoints": board.get("pair_matchpoints"),
                        "diana_seat": board.get("diana_seat"),
                        "source_consistency_ok": source_consistency.get("ok") is True,
                    },
                }
            )
        batch = normalize_structured_batch(
            rows,
            event_id="29912",
            session_id=f"round-{round_no}",
            scoring="MP",
            source={"kind": "audited_dds3_artifact", "round": round_no},
        )
        for deal, row in zip(batch.deals, rows, strict=True):
            deals.append(
                TournamentDeal(
                    **{
                        **deal.__dict__,
                        "source_provenance": {**dict(deal.source_provenance), **dict(row["_source"])},
                    }
                )
            )
    if len(deals) != 100:
        raise TournamentAdapterError(f"expected 100 normalized 29912 deals, got {len(deals)}")
    return tuple(deals)


def _dds_evidence(message: str, provenance: Mapping[str, Any]) -> tuple[Evidence, ...]:
    return (Evidence(EvidenceKind.DDS_FACT, message, provenance=dict(provenance), confidence=1.0),)


def findings_30041(report: Mapping[str, Any]) -> tuple[AnalysisFinding, ...]:
    findings: list[AnalysisFinding] = []
    for board in report["boards"]:
        comparison = board.get("same_contract_dd_comparison")
        if not isinstance(comparison, Mapping):
            continue
        delta = float(comparison["target_pair_delta_vs_dd_tricks"])
        if delta >= 0:
            continue
        board_no = int(board["board"])
        findings.append(
            AnalysisFinding(
                deal_id=f"30041:round-2:{board_no}",
                category="dds3_pair_same_contract_delta",
                summary="Результат пары по взяткам хуже DD-значения того же контракта/разыгрывающего.",
                evidence=_dds_evidence(
                    "Result-level DDS3 comparison; this is not a card-level or student-skill attribution.",
                    {"event": 30041, "round": 2, "board": board_no, "delta": delta},
                ),
                trick_loss=-delta,
                observability=Observability.NOT_OBSERVABLE,
                repeat_key=PAIR_SAME_CONTRACT_REPEAT_KEY,
            )
        )
    return tuple(findings)


def _side(seat: str) -> str:
    return "NS" if str(seat).upper() in {"N", "S"} else "EW"


def findings_29912(report: Mapping[str, Any]) -> tuple[AnalysisFinding, ...]:
    validate_29912_report_contract(report)
    findings: list[AnalysisFinding] = []
    for session in report["sessions"]:
        round_no = int(session["round"])
        for board in session["boards"]:
            consistency = board.get("source_consistency")
            if not isinstance(consistency, Mapping) or consistency.get("ok") is not True:
                continue
            board_no = int(board["board"])
            deal_id = f"29912:round-{round_no}:{board_no}"
            same = board.get("same_contract")
            if isinstance(same, Mapping):
                declarer_delta = float(same["actual_minus_dd_declarer"])
                target_delta = declarer_delta if _side(board["declarer"]) == str(board["pair_direction"]).upper() else -declarer_delta
                if target_delta < 0:
                    findings.append(
                        AnalysisFinding(
                            deal_id=deal_id,
                            category="dds3_pair_same_contract_delta",
                            summary="Результат пары по взяткам хуже DD-значения того же контракта/разыгрывающего.",
                            evidence=_dds_evidence(
                                "Result-level DDS3 comparison; this is not a card-level or student-skill attribution.",
                                {"event": 29912, "round": round_no, "board": board_no, "delta": target_delta},
                            ),
                            trick_loss=-target_delta,
                            observability=Observability.NOT_OBSERVABLE,
                            repeat_key=PAIR_SAME_CONTRACT_REPEAT_KEY,
                        )
                    )
            lead = board.get("opening_lead_dds3")
            if board.get("diana_opening_leader") is True and isinstance(lead, Mapping):
                regret = float(lead.get("regret", 0.0))
                if regret > 0:
                    findings.append(
                        AnalysisFinding(
                            deal_id=deal_id,
                            category="dds3_diana_opening_lead_regret",
                            summary="Зафиксированный первый ход Дианы имеет положительный DDS3 regret.",
                            evidence=_dds_evidence(
                                "The opening lead is observed and its DDS3 regret is technical evidence, not a teaching-rule judgment.",
                                {"event": 29912, "round": round_no, "board": board_no, "regret": regret},
                            ),
                            trick_loss=regret,
                            observability=Observability.OBSERVABLE,
                            repeat_key=DIANA_OPENING_LEAD_REPEAT_KEY,
                        )
                    )
            if board.get("diana_declarer") is True and isinstance(lead, Mapping) and isinstance(same, Mapping):
                actual = float(same["actual_tricks"])
                ceiling = float(lead["declarer_ceiling_after_recorded_lead"])
                if actual < ceiling:
                    findings.append(
                        AnalysisFinding(
                            deal_id=deal_id,
                            category="dds3_diana_declarer_post_lead_shortfall",
                            summary="Фактическое число взяток ниже DD-потолка после зафиксированного первого хода защиты.",
                            evidence=_dds_evidence(
                                "Post-opening-lead DDS3 ceiling; no later card-level swing is attributed without a full play record.",
                                {"event": 29912, "round": round_no, "board": board_no, "shortfall": ceiling - actual},
                            ),
                            trick_loss=ceiling - actual,
                            observability=Observability.NOT_OBSERVABLE,
                            repeat_key=DIANA_DECLARER_POST_LEAD_REPEAT_KEY,
                        )
                    )
    return tuple(findings)


def build_real_evidence(
    source_30041: Mapping[str, Any],
    dds3_30041: Mapping[str, Any],
    dds3_29912: Mapping[str, Any],
    *,
    source_30041_sha256: str,
) -> RealTournamentEvidence:
    batch_30041 = normalize_30041_facts(source_30041)
    validate_30041_dds3_report(dds3_30041, source_sha256=source_30041_sha256)
    deals_29912 = normalize_29912_report(dds3_29912)

    f30041 = findings_30041(dds3_30041)
    f29912 = findings_29912(dds3_29912)
    a30041 = analyze_tournament(batch_30041.deals, f30041)
    a29912 = analyze_tournament(deals_29912, f29912)
    longitudinal = build_longitudinal_report((a29912, a30041))

    for finding in (*a30041.findings, *a29912.findings):
        if any(e.kind in {EvidenceKind.SYSTEM_RULE, EvidenceKind.MODEL_OPINION} for e in finding.evidence):
            raise TournamentAdapterError("real-evidence adapter must not invent system rules or model opinions")

    return RealTournamentEvidence(
        analysis_30041=a30041,
        analysis_29912=a29912,
        longitudinal=longitudinal,
        source_summary={
            "30041": {
                "deals": len(batch_30041.deals),
                "played_contracts_compared": dds3_30041["summary"]["played_contracts_compared"],
                "source_sha256": source_30041_sha256,
            },
            "29912": {
                "deals": len(deals_29912),
                "decision_analyzable_boards": dds3_29912["aggregate"]["decision_analyzable_boards"],
                "source_inconsistencies": dds3_29912["aggregate"]["source_inconsistencies"],
            },
        },
    )


def serialize_real_evidence(evidence: RealTournamentEvidence) -> dict[str, Any]:
    def analysis_dict(analysis: TournamentAnalysis) -> dict[str, Any]:
        return {
            "event_id": analysis.event_id,
            "finding_count": len(analysis.findings),
            "category_totals": {k: dict(v) for k, v in analysis.category_totals.items()},
            "findings": [
                {
                    **{k: v for k, v in asdict(f).items() if k != "evidence"},
                    "observability": f.observability.value,
                    "evidence": [
                        {"kind": e.kind.value, "message": e.message, "provenance": dict(e.provenance), "confidence": e.confidence}
                        for e in f.evidence
                    ],
                }
                for f in analysis.findings
            ],
        }

    return {
        "schema": "tournament-longitudinal-real-evidence-v1",
        "policy": {
            "dds3_only_technical_evidence": True,
            "student_skill_writes_allowed": False,
            "methodology_inference_allowed": False,
            "auction_attribution_allowed": False,
            "full_play_attribution_allowed": False,
        },
        "sources": dict(evidence.source_summary),
        "events": {
            "29912": analysis_dict(evidence.analysis_29912),
            "30041": analysis_dict(evidence.analysis_30041),
        },
        "longitudinal": {
            "clusters": [asdict(c) | {"recoverable_loss": c.recoverable_loss} for c in evidence.longitudinal.clusters],
            "persistent": [asdict(c) | {"recoverable_loss": c.recoverable_loss} for c in evidence.longitudinal.persistent],
            "single_event": [asdict(c) | {"recoverable_loss": c.recoverable_loss} for c in evidence.longitudinal.single_event],
        },
    }
