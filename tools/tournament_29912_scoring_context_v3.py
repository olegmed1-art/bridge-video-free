#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.tournament_scoring_context_29912_v3 import (
    build_29912_mp_context,
    join_29912_findings_with_mp_context,
    serialize_29912_mp_context,
)


def render_markdown(payload: dict) -> str:
    scales = payload["session_scales"]
    joined = payload["technical_finding_context"]
    dual = [x for x in joined if x["observed_gap_to_neutral_percentage_points"] > 0.0]
    lines = [
        "# Tournament Analyzer v3 — event 29912 MP context",
        "",
        "## Проверка шкалы MP",
        "",
        "Сырые board matchpoints переводятся в проценты только после независимой проверки: формула шкалы должна воспроизвести опубликованный session score каждой сохранённой сессии.",
        "",
        "| Сессия | Поле | Max MP/board | Сдач | Опубликовано | Получено | |Δ| |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in scales:
        lines.append(
            f"| {s['round_no']} | {s['field_size']} | {s['max_matchpoints_per_board']:.1f} | "
            f"{s['boards_counted']} | {s['reported_session_score']:.2f}% | "
            f"{s['derived_session_percentage']:.2f}% | {s['absolute_difference']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Cross-session outcome context",
            "",
            f"- Наблюдаемый средний board MP% по 100 сохранённым сдачам: {payload['cross_session_observed_mean_percentage']:.2f}%.",
            f"- Сумма дефицитов относительно 50%: {payload['total_below_neutral_mass_percentage_points']:.2f} процентных пунктов по отдельным сдачам.",
            f"- Арифметический сценарий «все результаты ниже 50% заменить ровно на 50%»: {payload['counterfactual_mean_if_all_below_neutral_were_neutral']:.2f}%.",
            "- Это не официальный итог event, не прогноз обучения и не DDS3→MP conversion.",
            "",
            "## Технические DDS3-наблюдения на сдачах ниже 50%",
            "",
            "Совпадение DDS3-технического факта и результата ниже 50% используется только для приоритизации преподавательского просмотра. Причинная связь не установлена.",
            "",
            "| Раздача | Категория | DD mass | MP% | Дефицит до 50% |",
            "|:---|:---|---:|---:|---:|",
        ]
    )
    for item in dual:
        lines.append(
            f"| {item['deal_id']} | {item['category']} | {float(item.get('technical_trick_loss') or 0.0):.1f} | "
            f"{item['observed_pair_percentage']:.1f} | {item['observed_gap_to_neutral_percentage_points']:.1f} |"
        )
    if not dual:
        lines.append("| — | — | — | — | — |")
    lines.extend(
        [
            "",
            "## Граница интерпретации",
            "",
            "- `causal_link=NOT_ESTABLISHED` для всех join-строк.",
            "- Ошибка ученика автоматически не атрибутируется.",
            "- Методическое правило и тема обучения этим слоем не создаются.",
            "- Без полного play record последующие карты не локализуются; без auction record торговые решения не приписываются.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dds3-29912", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    report = json.loads(args.dds3_29912.read_text(encoding="utf-8"))
    context = build_29912_mp_context(report)
    joined = join_29912_findings_with_mp_context(report, context)
    payload = serialize_29912_mp_context(context, joined)
    args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "sessions": [x["round_no"] for x in payload["session_scales"]],
                "boards": len(payload["outcomes"]),
                "technical_findings": len(payload["technical_finding_context"]),
                "scale_verified": payload["mp_scale_verified_against_reported_session_scores"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
