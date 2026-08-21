#!/usr/bin/env python3
"""Run a tournament facts extract through the canonical DDS3 HTTP runtime.

This runner is deliberately facts-only. It computes a full-deal DDS3 baseline for
all boards and, when an actual contract/result is present, compares the observed
trick result with the DDS3 game-theoretic value for the same strain/declarer and
the target-pair score with DDS3 Par. It does not infer a card-level mistake
without a real play record and does not write student skill/error observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SEATS = ["N", "E", "S", "W"]
CONTRACT_RE = re.compile(r"^([1-7])(NT|[SHDC])(XX|X)?$")


def _side(seat: str) -> str:
    return "NS" if seat in {"N", "S"} else "EW"


def _pair_side(value: str) -> str:
    normalized = value.upper().replace("–", "-").replace("—", "-").replace(" ", "")
    if normalized in {"N-S", "NS"}:
        return "NS"
    if normalized in {"E-W", "EW"}:
        return "EW"
    raise ValueError(f"unsupported pair_direction: {value!r}")


def _parse_contract(value: str) -> tuple[int, str, str]:
    text = value.strip().upper()
    match = CONTRACT_RE.fullmatch(text)
    if not match:
        raise ValueError(f"unsupported contract: {value!r}")
    level = int(match.group(1))
    strain = match.group(2)
    multiplier = match.group(3) or ""
    return level, strain, multiplier


def _post_json(base_url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/compute",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DDS3 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DDS3 unavailable: {exc}") from exc

    if data.get("engine") != "DDS3" or data.get("fallback_used") is not False:
        raise RuntimeError("non-canonical or fallback DDS result rejected")
    if data.get("operation") != "dd_table" or data.get("input_validated") is not True:
        raise RuntimeError("DDS3 result failed provenance contract")
    return data


def _row_dict(columns: list[str], row: str) -> dict[str, str]:
    values = row.split("|")
    if len(values) != len(columns):
        raise ValueError(f"row has {len(values)} fields, expected {len(columns)}: {row}")
    return dict(zip(columns, values, strict=True))


def _build_pbn(row: dict[str, str]) -> str:
    hands = [row[seat].strip() for seat in SEATS]
    if not all(hands):
        raise ValueError(f"board {row['board']}: missing hand")
    return "N:" + " ".join(hands)


def run(data_path: Path, base_url: str, token: str) -> dict[str, Any]:
    raw_bytes = data_path.read_bytes()
    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    source = json.loads(raw_bytes.decode("utf-8"))
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise ValueError("unsupported tournament facts schema")

    columns = list(source["columns"])
    rows = [_row_dict(columns, row) for row in source["rows"]]
    results: list[dict[str, Any]] = []
    engine_version: str | None = None

    for row in rows:
        board = int(row["board"])
        pbn = _build_pbn(row)
        dds = _post_json(
            base_url,
            token,
            {
                "operation": "dd_table",
                "pbn": pbn,
                "dealer": row["dealer"],
                "vulnerability": row["vulnerability"],
            },
        )
        if engine_version is None:
            engine_version = dds.get("engine_version")
        elif dds.get("engine_version") != engine_version:
            raise RuntimeError("DDS3 engine version changed inside one tournament run")

        item: dict[str, Any] = {
            "board": board,
            "status": row["status"],
            "dealer": row["dealer"],
            "vulnerability": row["vulnerability"],
            "pair_direction": row["pair_direction"],
            "contract": row["contract"] or None,
            "declarer": row["declarer"] or None,
            "result_delta": int(row["result_delta"]) if row["result_delta"] else None,
            "opening_lead": row["opening_lead"] or None,
            "pair_score": int(row["pair_score"]) if row["pair_score"] else None,
            "pair_percentage": float(row["pair_percentage"]) if row["pair_percentage"] else None,
            "dds3": {
                "engine": dds["engine"],
                "engine_version": dds["engine_version"],
                "fallback_used": dds["fallback_used"],
                "input_validated": dds["input_validated"],
                "dd_table": dds["dd_table"],
                "par_score_ns": dds["par_score_ns"],
                "par_contracts": dds["par_contracts"],
            },
        }

        if row["status"] == "played" and row["pair_score"]:
            target_side = _pair_side(row["pair_direction"])
            target_par_score = int(dds["par_score_ns"]) if target_side == "NS" else -int(dds["par_score_ns"])
            actual_pair_score = int(row["pair_score"])
            item["dd_par_comparison"] = {
                "target_pair_side": target_side,
                "actual_pair_score": actual_pair_score,
                "dds3_par_score_target_pair": target_par_score,
                "actual_pair_score_minus_dds3_par": actual_pair_score - target_par_score,
                "interpretation": (
                    "positive means the target pair's observed board score exceeded DDS3 Par; negative means it was below DDS3 Par. "
                    "This combines auction and play outcome and is not a card-level skill attribution."
                ),
            }

        if row["status"] == "played" and row["contract"] and row["declarer"] and row["result_delta"]:
            level, strain, multiplier = _parse_contract(row["contract"])
            declarer = row["declarer"].upper()
            if declarer not in SEATS:
                raise ValueError(f"board {board}: invalid declarer {declarer!r}")
            actual_tricks = 6 + level + int(row["result_delta"])
            if not 0 <= actual_tricks <= 13:
                raise ValueError(f"board {board}: impossible actual trick count {actual_tricks}")
            dd_tricks = int(dds["dd_table"][strain][SEATS.index(declarer)])
            target_side = _pair_side(row["pair_direction"])
            declarer_side = _side(declarer)
            target_role = "declarer_side" if target_side == declarer_side else "defending_side"
            target_delta = actual_tricks - dd_tricks if target_role == "declarer_side" else dd_tricks - actual_tricks
            item["same_contract_dd_comparison"] = {
                "level": level,
                "strain": strain,
                "multiplier": multiplier,
                "actual_tricks": actual_tricks,
                "dds3_tricks_same_strain_declarer": dd_tricks,
                "actual_minus_dd_declarer_tricks": actual_tricks - dd_tricks,
                "target_pair_role": target_role,
                "target_pair_delta_vs_dd_tricks": target_delta,
                "interpretation": (
                    "positive means the observed result favored the target pair relative to the DDS3 value; "
                    "negative means it favored the opponents. This is result-level evidence, not card-level attribution."
                ),
            }
        results.append(item)

    comparisons = [r for r in results if "same_contract_dd_comparison" in r]
    negatives = [r for r in comparisons if r["same_contract_dd_comparison"]["target_pair_delta_vs_dd_tricks"] < 0]
    zeros = [r for r in comparisons if r["same_contract_dd_comparison"]["target_pair_delta_vs_dd_tricks"] == 0]
    positives = [r for r in comparisons if r["same_contract_dd_comparison"]["target_pair_delta_vs_dd_tricks"] > 0]
    negative_sorted = sorted(
        negatives,
        key=lambda r: (r["same_contract_dd_comparison"]["target_pair_delta_vs_dd_tricks"], r["board"]),
    )

    par_comparisons = [r for r in results if "dd_par_comparison" in r]
    below_par = [r for r in par_comparisons if r["dd_par_comparison"]["actual_pair_score_minus_dds3_par"] < 0]
    at_par = [r for r in par_comparisons if r["dd_par_comparison"]["actual_pair_score_minus_dds3_par"] == 0]
    above_par = [r for r in par_comparisons if r["dd_par_comparison"]["actual_pair_score_minus_dds3_par"] > 0]
    below_par_sorted = sorted(
        below_par,
        key=lambda r: (r["dd_par_comparison"]["actual_pair_score_minus_dds3_par"], r["board"]),
    )

    return {
        "schema": "bridge-dds3-tournament-baseline-v1",
        "mode": "FACTS_ONLY_DDS3_BASELINE",
        "source_sha256": source_sha256,
        "source": source.get("source", {}),
        "tournament": source.get("tournament", {}),
        "policy": {
            "engine": "DDS3",
            "engine_version": engine_version,
            "fallback_used": False,
            "card_level_attribution_allowed": False,
            "student_skill_writes_allowed": False,
            "reason": "No play record in the source facts; exact first-swing/replay attribution is forbidden.",
        },
        "summary": {
            "boards_total": len(results),
            "dds3_baselines_computed": len(results),
            "played_contracts_compared": len(comparisons),
            "target_pair_below_dd_equilibrium": len(negatives),
            "target_pair_at_dd_equilibrium": len(zeros),
            "target_pair_above_dd_equilibrium": len(positives),
            "boards_below_dd_equilibrium": [r["board"] for r in negative_sorted],
            "played_scores_compared_to_par": len(par_comparisons),
            "target_pair_below_dd_par": len(below_par),
            "target_pair_at_dd_par": len(at_par),
            "target_pair_above_dd_par": len(above_par),
            "boards_below_dd_par": [r["board"] for r in below_par_sorted],
        },
        "boards": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    tournament = report["tournament"]
    summary = report["summary"]
    lines = [
        "# DDS3 baseline — турнир Дианы Векслер",
        "",
        f"- Турнир: {tournament.get('provider_native_key', '')}",
        f"- Целевой ученик: {tournament.get('target_student_name', '')}",
        f"- Пара: {', '.join(tournament.get('members', []))}",
        f"- Результат турнира: {tournament.get('final_percentage')}%, место {tournament.get('rank')} из {tournament.get('field_size')}",
        f"- DDS3: {report['policy']['engine_version']}",
        f"- Полных DDS3 baseline: {summary['dds3_baselines_computed']}",
        f"- Сыгранных контрактов с result-level сравнением: {summary['played_contracts_compared']}",
        "",
        "## Result-level сравнение с DDS3",
        "",
        "Δ пары — сравнение фактических взяток с DDS3-значением того же контракта/разыгрывающего с точки зрения пары Дианы. Δ score vs Par — фактический score пары минус DDS3 Par для её стороны. Положительные значения благоприятны для пары, отрицательные — неблагоприятны. Ни один показатель не приписывается конкретной карте без play record.",
        "",
        "| Сдача | Пара | Контракт | Разыгр. | Факт взяток | DDS3 | Δ пары | Score | DDS3 Par | Δ score vs Par | % пары |",
        "|---:|:---:|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for board in report["boards"]:
        cmp = board.get("same_contract_dd_comparison")
        par = board.get("dd_par_comparison")
        if not cmp or not par:
            continue
        lines.append(
            f"| {board['board']} | {board['pair_direction']} | {board['contract']} | {board['declarer']} | "
            f"{cmp['actual_tricks']} | {cmp['dds3_tricks_same_strain_declarer']} | "
            f"{cmp['target_pair_delta_vs_dd_tricks']:+d} | {par['actual_pair_score']} | "
            f"{par['dds3_par_score_target_pair']} | {par['actual_pair_score_minus_dds3_par']:+d} | "
            f"{board['pair_percentage']:.1f} |"
        )
    lines.extend(
        [
            "",
            "## Сводка",
            "",
            f"- Ниже DDS3 equilibrium по взяткам для пары: {summary['target_pair_below_dd_equilibrium']} сдач.",
            f"- Ровно DDS3 equilibrium по взяткам: {summary['target_pair_at_dd_equilibrium']} сдач.",
            f"- Выше DDS3 equilibrium по взяткам для пары: {summary['target_pair_above_dd_equilibrium']} сдач.",
            f"- Сдачи с отрицательной Δ пары: {summary['boards_below_dd_equilibrium']}.",
            f"- Ниже DDS3 Par по score: {summary['target_pair_below_dd_par']} сдач; на Par: {summary['target_pair_at_dd_par']}; выше Par: {summary['target_pair_above_dd_par']}.",
            f"- Сдачи ниже DDS3 Par: {summary['boards_below_dd_par']}.",
            "",
            "Точный поиск first swing, regret конкретного хода и final unrecovered damage не выполнялся: в исходных данных нет покарточной записи розыгрыша.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    report = run(args.data, args.base_url, args.token)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
