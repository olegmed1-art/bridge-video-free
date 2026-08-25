#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from bridge_school_api.tournament_real_sources_v3 import (
    PAIR_SAME_CONTRACT_REPEAT_KEY,
    build_real_evidence,
    serialize_real_evidence,
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


def _load_29912_source_facts(path: Path) -> tuple[dict[int, dict[str, Any]], dict[str, str]]:
    """Load only the exact recovered source facts pinned by content hash.

    The historical source artifact is external evidence.  The longitudinal report
    must therefore fail closed if a file is missing, added, renamed to an unexpected
    round, or differs byte-for-byte from the already recovered evidence.
    """
    expected = PROVENANCE["29912"]["source_fact_sha256"]
    if not isinstance(expected, dict):
        raise ValueError("29912 source fact provenance is malformed")

    result: dict[int, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for file in sorted(path.glob("tournament_29912_round*_diana_facts.json")):
        match = re.search(r"round(\d+)", file.name)
        if not match:
            continue
        round_no = int(match.group(1))
        key = f"round-{round_no}"
        if key not in expected:
            raise ValueError(f"unexpected 29912 source facts round: {round_no}")
        if round_no in result:
            raise ValueError(f"duplicate 29912 source facts for round {round_no}")
        raw = file.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected[key]:
            raise ValueError(
                f"29912 source facts digest mismatch for round {round_no}: {digest} != {expected[key]}"
            )
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"29912 source facts round {round_no} must be a JSON object")
        result[round_no] = payload
        digests[key] = digest

    expected_rounds = {int(key.split("-", 1)[1]) for key in expected}
    if set(result) != expected_rounds:
        missing = sorted(expected_rounds - set(result))
        extra = sorted(set(result) - expected_rounds)
        raise ValueError(f"29912 source facts coverage mismatch: missing={missing}, extra={extra}")
    return result, digests


def render_markdown(report: dict[str, Any]) -> str:
    sources = report["sources"]
    clusters = report["longitudinal"]["clusters"]
    persistent = report["longitudinal"]["persistent"]
    mp = report["scoring_context"]["30041"]
    score_29912 = report["scoring_context"]["29912"]
    review = [
        item
        for item in mp["technical_finding_context"]
        if (item.get("observed_gap_to_neutral") or 0.0) > 0.0
    ]
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

    lines.extend(
        [
            "",
            "## MP outcome context — event 30041",
            "",
            f"- Фактический итог пары: {mp['final_percentage']:.2f}%, место {mp['rank']}/{mp['field_size']}; в итог вошло {mp['counted_results']} результатов.",
            f"- Сумма только наблюдаемых дефицитов относительно нейтральных 50%: {mp['total_below_neutral_mass']:.1f} MP-процентных пунктов по отдельным сдачам.",
            f"- Чисто арифметический сценарий «все результаты ниже 50% заменить ровно на 50%» дал бы {mp['counterfactual_final_percentage_if_all_below_neutral_were_neutral']:.2f}%.",
            "- Это НЕ оценка того, сколько можно вернуть обучением, и НЕ перевод DDS3-взяток в MP.",
            "",
            "### Сдачи для преподавательского просмотра: технический DDS3-факт + фактический результат ниже 50%",
            "",
            "Совпадение двух фактов используется только для приоритизации просмотра. Причинная связь между DDS3-отклонением и MP-результатом не установлена.",
            "",
            "| Раздача | DD mass | Факт MP% | Дефицит до 50% | Вклад в итог при замене на 50% |",
            "|:---|---:|---:|---:|---:|",
        ]
    )
    for item in review:
        lines.append(
            f"| {item['deal_id']} | {float(item.get('technical_trick_loss') or 0.0):.1f} | "
            f"{float(item['observed_pair_percentage']):.1f} | {float(item['observed_gap_to_neutral']):.1f} | "
            f"{float(item['final_percentage_uplift_if_neutral']):.3f} п.п. |"
        )
    if not review:
        lines.append("| — | — | — | — | — |")

    gaps_29912 = [row for row in score_29912["session_additivity"] if not row["verified"]]
    lines.extend(
        [
            "",
            "## Source score context — event 29912",
            "",
            f"- Для {len(score_29912['outcomes'])} DDS3-анализированных сдач восстановлен исходный знаковый `pair_matchpoints`-вклад; сумма анализированных вкладов: {score_29912['analyzed_board_score_contribution_sum']:+.1f}.",
            f"- Сумма модулей только отрицательных исходных вкладов: {score_29912['negative_score_contribution_mass']:.1f}.",
            f"- Аддитивность полностью подтверждена для сессий {score_29912['source_score_additivity_verified_rounds']}; evidence gap сохранён для {score_29912['source_score_additivity_unverified_rounds']}.",
            "- Шкала не переводится в проценты; DDS3→score conversion не выполняется и причинная связь с техническими находками не устанавливается.",
        ]
    )
    for gap in gaps_29912:
        lines.append(
            f"- Сессия {gap['round_no']}: необъяснённый остаток {gap['unexplained_remainder']:+.1f} сохранён как evidence gap и не распределяется по сдачам."
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
            "- MP/score-контекст не выполняет DDS3→результат conversion и не создаёт причинную атрибуцию ошибки.",
            "- SYSTEM_RULE и MODEL_OPINION этим слоем не создаются; L1/BEN/DDS3 семантика не меняется.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts-30041", type=Path, required=True)
    parser.add_argument("--dds3-30041", type=Path, required=True)
    parser.add_argument("--dds3-29912", type=Path, required=True)
    parser.add_argument("--facts-dir-29912", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    source_30041, source_sha = _read_json(args.facts_30041)
    dds3_30041, dds3_30041_sha = _read_json(args.dds3_30041)
    dds3_29912, dds3_29912_sha = _read_json(args.dds3_29912)
    source_facts_29912, source_facts_29912_sha = _load_29912_source_facts(args.facts_dir_29912)

    evidence = build_real_evidence(
        source_30041,
        dds3_30041,
        dds3_29912,
        source_30041_json_sha256=source_sha,
    )
    report = serialize_real_evidence(evidence)
    mp_context = build_30041_mp_context(source_30041)
    joined = join_findings_with_mp_context(evidence.analysis_30041, mp_context)
    score_context_29912 = build_29912_source_score_context(dds3_29912, source_facts_29912)
    joined_29912 = join_29912_findings_with_source_score_context(dds3_29912, score_context_29912)
    report["scoring_context"] = {
        "30041": serialize_mp_context(mp_context, joined),
        "29912": serialize_29912_source_score_context(score_context_29912, joined_29912),
    }
    report["provenance"] = {
        **PROVENANCE,
        "input_file_sha256": {
            "facts_30041": source_sha,
            "dds3_30041": dds3_30041_sha,
            "dds3_29912": dds3_29912_sha,
            "source_facts_29912": source_facts_29912_sha,
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
        "mp_context_30041": True,
        "score_context_29912": True,
        "score_context_29912_verified_rounds": report["scoring_context"]["29912"]["source_score_additivity_verified_rounds"],
        "score_context_29912_unverified_rounds": report["scoring_context"]["29912"]["source_score_additivity_unverified_rounds"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
