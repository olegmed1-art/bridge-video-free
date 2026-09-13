#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from bridge_school_api.tournament_scoring_context_29912_v3 import (
    build_29912_source_score_context,
    join_29912_findings_with_source_score_context,
    serialize_29912_source_score_context,
)


def _load_source_facts(path: Path) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for file in path.glob("tournament_29912_round*_diana_facts.json"):
        match = re.search(r"round(\d+)", file.name)
        if not match:
            continue
        round_no = int(match.group(1))
        if round_no in result:
            raise ValueError(f"duplicate source facts for round {round_no}")
        result[round_no] = json.loads(file.read_text(encoding="utf-8"))
    return result


def render_markdown(payload: dict) -> str:
    sessions = payload["session_additivity"]
    joined = payload["technical_finding_context"]
    negative = [x for x in joined if x["negative_score_contribution"] > 0.0]
    lines = [
        "# Tournament Analyzer v3 — event 29912 source score context",
        "",
        "## Проверка исходной шкалы",
        "",
        "Историческое поле `pair_matchpoints` подтверждено как знаковый аддитивный вклад в исходный session score. Отрицательные значения допустимы. В проценты эти данные не переводятся.",
        "",
        "| Сессия | Анализ. сдач | Пропущено | Сумма анализ. | Сумма пропущ. | Известная сумма | Session score | Остаток | Статус |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for s in sessions:
        lines.append(
            f"| {s['round_no']} | {s['boards_counted']} | {s['skipped_rows_count']} | "
            f"{s['analyzed_board_sum']:+.1f} | {s['skipped_numeric_sum']:+.1f} | "
            f"{s['known_source_sum']:+.1f} | {s['reported_session_score']:+.1f} | "
            f"{s['unexplained_remainder']:+.1f} | {'PASS' if s['verified'] else 'EVIDENCE GAP'} |"
        )
    lines.extend(
        [
            "",
            "## Cross-session raw outcome context",
            "",
            f"- Сумма знаковых вкладов по 100 DDS3-анализированным сдачам: {payload['analyzed_board_score_contribution_sum']:+.1f}.",
            f"- Сумма модулей только отрицательных вкладов: {payload['negative_score_contribution_mass']:.1f}.",
            f"- Полностью аддитивно подтверждённые сессии: {payload['source_score_additivity_verified_rounds']}.",
            f"- Сессии с необъяснённым остатком: {payload['source_score_additivity_unverified_rounds']}.",
            "- Это исходная шкала сайта; процентная конверсия, прогноз обучения и DDS3→score conversion не выполняются.",
            "",
            "## DDS3-технические наблюдения при отрицательном исходном вкладе",
            "",
            "Совпадение технического DDS3-факта и отрицательного исходного вклада используется только для приоритизации преподавательского просмотра. Причинная связь не установлена.",
            "",
            "| Раздача | Категория | DD mass | Исходный вклад |",
            "|:---|:---|---:|---:|",
        ]
    )
    for item in negative:
        lines.append(
            f"| {item['deal_id']} | {item['category']} | {float(item.get('technical_trick_loss') or 0.0):.1f} | "
            f"{item['source_pair_score_contribution']:+.1f} |"
        )
    if not negative:
        lines.append("| — | — | — | — |")
    lines.extend(
        [
            "",
            "## Граница интерпретации",
            "",
            "- `causal_link=NOT_ESTABLISHED` для всех join-строк.",
            "- Отрицательный исходный вклад не равен автоматически ошибке ученика.",
            "- Необъяснённый остаток сохраняется как evidence gap и не распределяется по сдачам.",
            "- Методическое правило и тема обучения этим слоем не создаются.",
            "- Без полного play record последующие карты не локализуются; без auction record торговые решения не приписываются.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dds3-29912", type=Path, required=True)
    parser.add_argument("--facts-dir", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    report = json.loads(args.dds3_29912.read_text(encoding="utf-8"))
    source_facts = _load_source_facts(args.facts_dir)
    context = build_29912_source_score_context(report, source_facts)
    joined = join_29912_findings_with_source_score_context(report, context)
    payload = serialize_29912_source_score_context(context, joined)
    args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "sessions": [x["round_no"] for x in payload["session_additivity"]],
                "boards": len(payload["outcomes"]),
                "technical_findings": len(payload["technical_finding_context"]),
                "verified_rounds": payload["source_score_additivity_verified_rounds"],
                "unverified_rounds": payload["source_score_additivity_unverified_rounds"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
