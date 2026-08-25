#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from bridge_school_api.tournament_real_sources_v3 import (
    PAIR_SAME_CONTRACT_REPEAT_KEY,
    build_real_evidence,
    serialize_real_evidence,
)

PROVENANCE = {
    "30041": {
        "artifact_id": 9463631738,
        "artifact_digest": "sha256:87aa70e02676c5b6154e3a13ec3e7cf82351e65e8e8a10772ac006a6458a3720",
        "workflow_run_id": 32530235619,
    },
    "29912": {
        "artifact_id": 9471054929,
        "artifact_digest": "sha256:1f45c77aafc4d7c4e99f401c98803ec82d37a5b1d97c2bed0dc85553738a2bf2",
        "workflow_run_id": 32554526410,
        "source_fact_sha256": {
            "round-1": "34fe5c59d901686b712c2384078aac618a191a4528c694db2ea8c6af2ae51073",
            "round-2": "806ed04726195fefb3b941d0ca2a132822cb24de514ae424c52b6330fa81fc27",
            "round-4": "7cc1822cc64ed6464b99dce0937fc6c5a64efbeee2a6a5e8f9596500a3dde637",
            "round-5": "432d386551745509218911075c099230e1fbb1d4f485832d99ee0f9aa6954e67",
            "round-6": "ebc064b8921560e56829409f65e3add3a192206dd62971a91664df116e94c7dd",
        },
    },
}


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def render_markdown(report: dict[str, Any]) -> str:
    sources = report["sources"]
    clusters = report["longitudinal"]["clusters"]
    persistent = report["longitudinal"]["persistent"]
    lines = [
        "# Tournament Analyzer v3 — первый реальный longitudinal report",
        "",
        "## Проверенные источники",
        "",
        f"- Event 30041: {sources['30041']['deals']} раздачи; result-level DDS3 сравнение доступно для {sources['30041']['played_contracts_compared']} сыгранных контрактов.",
        f"- Event 29912: {sources['29912']['deals']} раздач в сессиях 1, 2, 4, 5, 6; decision-analyzable: {sources['29912']['decision_analyzable_boards']}.",
        "- Event 29912, сессия 1, сдача 5 исключена из персональных/контрактных выводов из-за противоречия источника.",
        "",
        "## Longitudinal технические кластеры",
        "",
        "Кластеры ниже являются только повторяемыми DDS3-техническими наблюдениями. Они не являются диагнозом навыка, правилом системы торговли или автоматически назначенной темой обучения.",
        "",
        "| Ключ | Турниров | Наблюдений | Потеря взяток (DD mass) | Статус |",
        "|:---|---:|---:|---:|:---|",
    ]
    persistent_keys = {c["repeat_key"] for c in persistent}
    for cluster in clusters:
        status = "persistent" if cluster["repeat_key"] in persistent_keys else "single-event"
        lines.append(
            f"| {cluster['repeat_key']} | {cluster['tournament_count']} | {cluster['finding_count']} | "
            f"{cluster['total_trick_loss']:.1f} | {status} |"
        )
    pair = next((c for c in persistent if c["repeat_key"] == PAIR_SAME_CONTRACT_REPEAT_KEY), None)
    lines.extend(["", "## Интерпретационная граница", ""])
    if pair:
        lines.append(
            f"- `{PAIR_SAME_CONTRACT_REPEAT_KEY}` повторился в {pair['tournament_count']} турнирах. Это означает только, что в обоих источниках есть сдачи, где результат пары по взяткам хуже double-dummy значения того же контракта/разыгрывающего. Это не доказывает устойчивую персональную ошибку Дианы."
        )
    lines.extend(
        [
            "- Для 29912 первый ход наблюдаем, поэтому его DDS3-regret можно фиксировать как технический факт; методическое правило из него не выводится.",
            "- Без полного покарточного протокола последующие конкретные ходы не атрибутируются.",
            "- Без аукциона торговые ошибки конкретным заявкам не приписываются.",
            "- SYSTEM_RULE и MODEL_OPINION этим слоем не создаются; L1/BEN/DDS3 семантика не меняется.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts-30041", type=Path, required=True)
    parser.add_argument("--dds3-30041", type=Path, required=True)
    parser.add_argument("--dds3-29912", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    source_30041, source_sha = _read_json(args.facts_30041)
    dds3_30041, dds3_30041_sha = _read_json(args.dds3_30041)
    dds3_29912, dds3_29912_sha = _read_json(args.dds3_29912)

    evidence = build_real_evidence(
        source_30041,
        dds3_30041,
        dds3_29912,
        source_30041_sha256=source_sha,
    )
    report = serialize_real_evidence(evidence)
    report["provenance"] = {
        **PROVENANCE,
        "input_file_sha256": {
            "facts_30041": source_sha,
            "dds3_30041": dds3_30041_sha,
            "dds3_29912": dds3_29912_sha,
        },
    }
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "schema": report["schema"],
        "events": sorted(report["events"]),
        "findings_29912": report["events"]["29912"]["finding_count"],
        "findings_30041": report["events"]["30041"]["finding_count"],
        "persistent_clusters": len(report["longitudinal"]["persistent"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
