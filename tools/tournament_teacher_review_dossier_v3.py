#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from bridge_school_api.tournament_real_sources_v3 import (
    findings_29912,
    findings_30041,
    normalize_29912_report,
    normalize_30041_facts,
    validate_30041_dds3_report,
)
from bridge_school_api.tournament_teacher_review_dossier_v3 import (
    build_teacher_review_dossier,
    serialize_teacher_review_dossier,
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_real_evidence(facts_30041_path: Path, dds3_30041_path: Path, dds3_29912_path: Path):
    source_bytes = facts_30041_path.read_bytes()
    source_30041 = json.loads(source_bytes.decode("utf-8"))
    dds_30041 = _load_json(dds3_30041_path)
    dds_29912 = _load_json(dds3_29912_path)
    validate_30041_dds3_report(
        dds_30041,
        source_json_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )
    batch_30041 = normalize_30041_facts(source_30041)
    deals_29912 = normalize_29912_report(dds_29912)
    deals = tuple(batch_30041.deals) + tuple(deals_29912)
    findings = tuple(findings_30041(dds_30041)) + tuple(findings_29912(dds_29912))
    return deals, findings


def _hand(cards: list[str]) -> str:
    return " ".join(cards)


def render_markdown(payload: dict) -> str:
    lines = [
        "# Tournament Analyzer v3 — teacher review dossier",
        "",
        "Пакет фактов для явного решения преподавателя. Ни один пункт ниже не является автоматически признанной ошибкой ученика или методической категорией.",
        "",
    ]
    for item in payload["items"]:
        facts = item["deal_facts"]
        queue = item["queue_context"]
        technical = item["technical_finding"]
        outcome = queue["observed_outcome"]
        outcome_text = "—" if outcome is None else f"{float(outcome):+.2f}"
        lines.extend(
            [
                f"## {item['deal_id']} — {item['category']}",
                "",
                f"- Review ID: `{item['review_id']}`",
                f"- Статус: `{item['status']}`; требуется явное решение преподавателя.",
                f"- Турнирная шкала: `{queue['outcome_scale']}`; наблюдаемый результат: {outcome_text}; неблагоприятная величина: {float(queue['adverse_outcome_magnitude'] or 0.0):.2f}.",
                f"- Техническая DD-масса: {float(queue['technical_trick_loss'] or 0.0):.1f}; причинная связь: `NOT_ESTABLISHED`.",
                f"- Dealer: {facts['dealer'] or '—'}; vulnerability: {facts['vulnerability'] or '—'}; contract: {facts['contract'] or '—'}; declarer: {facts['declarer'] or '—'}; opening lead: {facts['opening_lead'] or '—'}.",
                f"- N: {_hand(facts['hands']['N'])}",
                f"- E: {_hand(facts['hands']['E'])}",
                f"- S: {_hand(facts['hands']['S'])}",
                f"- W: {_hand(facts['hands']['W'])}",
                f"- Observability: `{technical['observability']}`; technical summary: {technical['summary']}",
                "- Методическая привязка: не назначена; атрибуция ошибки ученику: не выполнена.",
                "",
            ]
        )
    lines.extend(
        [
            "## Границы",
            "",
            "- Все review receipts остаются `PENDING`.",
            "- Автоматические решения преподавателя запрещены.",
            "- Автоматическая методическая привязка запрещена.",
            "- Автоматическая атрибуция ошибки ученику запрещена.",
            "- Межтурнирный численный рейтинг запрещён: разные scoring scales не смешиваются.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--facts-30041", type=Path, required=True)
    parser.add_argument("--dds3-30041", type=Path, required=True)
    parser.add_argument("--dds3-29912", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    deals, findings = _build_real_evidence(args.facts_30041, args.dds3_30041, args.dds3_29912)
    dossier = build_teacher_review_dossier(
        _load_json(args.queue),
        _load_json(args.ledger),
        deals=deals,
        findings=findings,
    )
    payload = serialize_teacher_review_dossier(dossier)
    args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "queue_sha256": payload["queue_sha256"],
                "items": len(payload["items"]),
                "pending": sum(x["status"] == "PENDING" for x in payload["items"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
