#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from bridge_school_api.tournament_analyzer_v3 import analyze_tournament
from bridge_school_api.tournament_real_sources_v3 import (
    findings_30041,
    normalize_30041_facts,
    validate_30041_dds3_report,
)
from bridge_school_api.tournament_scoring_context_29912_v3 import (
    build_29912_source_score_context,
    join_29912_findings_with_source_score_context,
    serialize_29912_source_score_context,
)
from bridge_school_api.tournament_scoring_context_v3 import (
    build_30041_mp_context,
    join_findings_with_mp_context,
    serialize_mp_context,
)
from bridge_school_api.tournament_teacher_review_queue_v3 import (
    build_cross_event_teacher_review_queue,
    serialize_teacher_review_queue,
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_source_facts(path: Path) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for file in path.glob("tournament_29912_round*_diana_facts.json"):
        match = re.search(r"round(\d+)", file.name)
        if not match:
            continue
        round_no = int(match.group(1))
        if round_no in result:
            raise ValueError(f"duplicate 29912 source facts for round {round_no}")
        result[round_no] = _load_json(file)
    return result


def _build_30041_context(source_path: Path, dds_path: Path) -> dict:
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    dds = _load_json(dds_path)
    validate_30041_dds3_report(dds, source_json_sha256=hashlib.sha256(source_bytes).hexdigest())
    batch = normalize_30041_facts(source)
    analysis = analyze_tournament(batch.deals, findings_30041(dds))
    context = build_30041_mp_context(source)
    joined = join_findings_with_mp_context(analysis, context)
    return serialize_mp_context(context, joined)


def _build_29912_context(dds_path: Path, facts_dir: Path) -> dict:
    dds = _load_json(dds_path)
    context = build_29912_source_score_context(dds, _load_source_facts(facts_dir))
    joined = join_29912_findings_with_source_score_context(dds, context)
    return serialize_29912_source_score_context(context, joined)


def render_markdown(payload: dict) -> str:
    lines = [
        "# Tournament Analyzer v3 — teacher review queue",
        "",
        "Очередь предназначена только для преподавательского просмотра. Численные шкалы разных турниров не смешиваются и не образуют общего рейтинга.",
        "",
    ]
    for lane in payload["lanes"]:
        lines.extend(
            [
                f"## Event {lane['event_id']} — {lane['outcome_scale']}",
                "",
                "| Раздача | Категория | DD mass | Наблюдаемый результат | Неблагоприятная величина |",
                "|:---|:---|---:|---:|---:|",
            ]
        )
        for item in lane["items"]:
            outcome = item["observed_outcome"]
            rendered = "—" if outcome is None else f"{float(outcome):+.2f}"
            lines.append(
                f"| {item['deal_id']} | {item['category']} | "
                f"{float(item.get('technical_trick_loss') or 0.0):.1f} | {rendered} | "
                f"{float(item['adverse_outcome_magnitude']):.2f} |"
            )
        if not lane["items"]:
            lines.append("| — | — | — | — | — |")
        lines.append("")
    lines.extend(
        [
            "## Границы",
            "",
            "- `cross_event_numeric_ranking_allowed=false`: MP% 30041 и signed source score 29912 несопоставимы численно.",
            "- `causal_link=NOT_ESTABLISHED`: технический DDS3-факт не доказывает причину результата.",
            "- Автоматическая атрибуция ошибки ученику запрещена.",
            "- Новые темы обучения, правила торговли и методические категории этим слоем не создаются.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts-30041", type=Path, required=True)
    parser.add_argument("--dds3-30041", type=Path, required=True)
    parser.add_argument("--dds3-29912", type=Path, required=True)
    parser.add_argument("--facts-29912-dir", type=Path, required=True)
    parser.add_argument("--per-event-limit", type=int, default=10)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    c30041 = _build_30041_context(args.facts_30041, args.dds3_30041)
    c29912 = _build_29912_context(args.dds3_29912, args.facts_29912_dir)
    queue = build_cross_event_teacher_review_queue(c30041, c29912, per_event_limit=args.per_event_limit)
    payload = serialize_teacher_review_queue(queue)
    args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({
        "schema": payload["schema"],
        "lanes": {lane["event_id"]: len(lane["items"]) for lane in payload["lanes"]},
        "cross_event_numeric_ranking_allowed": payload["cross_event_numeric_ranking_allowed"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
