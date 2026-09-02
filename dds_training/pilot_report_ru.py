from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from audit import audit_database
from config import ALGORITHM_VERSION
from investigations import open_investigations


def pct(n: int, d: int) -> str:
    return "н/д" if not d else f"{100*n/d:.2f}%"


def split_metrics(con: sqlite3.Connection) -> dict[str, dict]:
    out = defaultdict(lambda: {
        "ct_total": 0, "ct_exact": 0, "ct_abs": 0.0, "ct_over": 0, "ct_under": 0,
        "ol_total": 0, "ol_opt": 0, "ol_regret": 0.0, "ol_2plus": 0, "ol_illegal": 0,
    })
    for task_type, split, payload in con.execute("SELECT task_type,split,result_json FROM dds_results"):
        r = json.loads(payload)
        m = out[split]
        if task_type == "contract_tricks":
            delta = int(r["delta_pred_minus_dds"])
            m["ct_total"] += 1
            m["ct_exact"] += int(delta == 0)
            m["ct_abs"] += abs(delta)
            m["ct_over"] += int(delta > 0)
            m["ct_under"] += int(delta < 0)
        else:
            m["ol_total"] += 1
            regret = r.get("dd_regret")
            if regret is None:
                m["ol_illegal"] += 1
            else:
                regret = float(regret)
                m["ol_regret"] += regret
                m["ol_opt"] += int(regret == 0)
                m["ol_2plus"] += int(regret >= 2)
    return dict(out)


def main() -> None:
    p = argparse.ArgumentParser(description="Русский итоговый отчёт пилота DDS")
    p.add_argument("--work", required=True)
    p.add_argument("--comparison")
    p.add_argument("--model")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    work = Path(args.work)
    con = sqlite3.connect(work / "training.sqlite3")
    metrics = split_metrics(con)
    errors = Counter(code for (code,) in con.execute("SELECT error_code FROM error_events"))
    audit = audit_database(con)
    open_count = len(open_investigations(con))
    investigations_total = con.execute("SELECT COUNT(*) FROM investigation_events WHERE event_type='opened'").fetchone()[0]
    investigations_resolved = con.execute("SELECT COUNT(*) FROM investigation_events WHERE event_type='resolved'").fetchone()[0]
    validation_leak = con.execute("SELECT COUNT(*) FROM skill_evidence WHERE split='validation'").fetchone()[0]
    sealed_leak = con.execute("SELECT COUNT(*) FROM skill_evidence WHERE split='sealed_test'").fetchone()[0]
    skills = con.execute(
        "SELECT skill_key,status,evidence_count,transfer_count,regression_passes,regression_failures,counterexample_count "
        "FROM skill_profiles WHERE algorithm_version=? ORDER BY skill_key",
        (ALGORITHM_VERSION,),
    ).fetchall()
    skill_status = Counter(r[1] for r in skills)
    corpus = json.loads((work / "corpus_summary.json").read_text(encoding="utf-8"))
    comparison = json.loads(Path(args.comparison).read_text(encoding="utf-8")) if args.comparison and Path(args.comparison).exists() else None
    model = json.loads(Path(args.model).read_text(encoding="utf-8")) if args.model and Path(args.model).exists() else None

    lines = [
        "# Итоговый отчёт DDS-обучения — пилот 10 000 сдач",
        "",
        "## Что было выполнено",
        f"- Сгенерировано и проверено сдач: **{corpus['count']}**.",
        f"- Разбиение: train {corpus['splits']['train']}, validation {corpus['splits']['validation']}, закрытый тест {corpus['splits']['sealed_test']}.",
        "- На каждую сдачу создано две технические задачи: оценка числа взяток и первый ход защиты.",
        "- Все ответы фиксировались до обращения к DDS; исходный PBN не содержит DDS-подсказок.",
        "- TRAIN использован для накопления опыта и построения адаптивной версии; validation и sealed_test не меняли базу навыков.",
        "",
        "## Результаты по выборкам",
    ]
    for split in ("train", "derived", "validation", "sealed_test"):
        m = metrics.get(split, {})
        ct = int(m.get("ct_total", 0))
        ol = int(m.get("ol_total", 0))
        lines += [
            f"### {split}",
            f"- Оценка взяток: {ct} задач; точное совпадение {pct(int(m.get('ct_exact', 0)), ct)}; "
            f"MAE {('н/д' if not ct else f'{float(m.get('ct_abs', 0))/ct:.3f}')}; "
            f"завышений {int(m.get('ct_over', 0))}, занижений {int(m.get('ct_under', 0))}.",
            f"- Первый ход защиты: {ol} задач; равнооптимальных ходов {pct(int(m.get('ol_opt', 0)), ol)}; "
            f"средний DD-regret {('н/д' if not ol else f'{float(m.get('ol_regret', 0))/ol:.3f}')}; "
            f"ошибок на 2+ взятки {int(m.get('ol_2plus', 0))}.",
        ]

    if comparison:
        lines += [
            "",
            "## Проверка реального улучшения на validation",
            f"- Правило выбора: {comparison['selection_rule']}",
            f"- Для закрытого теста выбрана версия: **{comparison['selected']}**.",
        ]
        for name, data in comparison["models"].items():
            ct = data["contract"]
            ol = data["opening_lead"]
            lines.append(
                f"- `{name}`: contract exact {pct(ct['exact'], ct['total'])}, MAE {ct['mae']:.3f}; "
                f"opening lead optimal {pct(ol['equal_optimal'], ol['total'])}, mean regret {ol['mean_regret']:.3f}; "
                f"совокупная потеря {data['combined_loss']:.3f}."
            )

    if model:
        lines += [
            "",
            "## Что накопила адаптивная версия",
            f"- Версия: `{model['model_version']}`.",
            f"- TRAIN-примеров для оценки контракта: {model['contract']['samples']}.",
            f"- TRAIN-задач первого хода: {model['opening_lead']['tasks']}; оценённых кандидатов: {model['opening_lead']['candidate_samples']}.",
            "- Модель хранит интерпретируемые поправки по силе линии, фиту, задержкам, форме и признакам возможного первого хода.",
            "- При выдаче новых ответов DDS не вызывается; DDS использовался только как учебная разметка TRAIN.",
        ]

    lines += [
        "",
        "## Ошибки, расследования и память",
        f"- Всего событий ошибок: {sum(errors.values())}.",
        f"- Обязательных расследований открыто: {investigations_total}; закрыто с причиной и уроком: {investigations_resolved}; осталось открытых: {open_count}.",
        f"- Навыки: candidate {skill_status['candidate']}, testing {skill_status['testing']}, confirmed {skill_status['confirmed']}, stable {skill_status['stable']}, weakened {skill_status['weakened']}.",
        "",
        "### Основные коды ошибок",
    ]
    for code, count in errors.most_common(10):
        lines.append(f"- `{code}` — {count}")

    lines += ["", "### Состояние навыков"]
    for key, status, evidence, transfer, reg_ok, reg_bad, counter in skills:
        lines.append(
            f"- `{key}` — **{status}**; evidence {evidence}, transfer {transfer}, regression {reg_ok}/{reg_bad}, counterexamples {counter}."
        )
    if not skills:
        lines.append("- Навыки ещё не сформированы.")

    lines += [
        "",
        "## Контроль качества",
        f"- Аудит базы: **{audit['status']}**.",
        f"- Открытых обязательных расследований: {open_count}.",
        f"- Утечка validation в skill evidence: {validation_leak}.",
        f"- Утечка sealed_test в skill evidence: {sealed_leak}.",
        "- DDS-факты, исходные прогнозы и история исправлений сохраняются неизменяемо и раздельно.",
        "",
        "## Ограничения пилота",
        "- Это технический double-dummy корпус: предиктор видел все четыре руки, но не видел ответ DDS до фиксации собственного ответа.",
        "- Синтетические PBN не содержат реальной торговли и полного Play, поэтому пилот проверяет число взяток и первый ход, но ещё не даёт полноценной траектории после каждой сыгранной карты.",
        "- Первичная версия была эвристическим анализатором, а не переобучением базовых весов GPT. Улучшение накоплено в алгоритме, адаптивной модели и долговременной базе опыта.",
        "- В случаях overclaim без записанной карточной линии нельзя честно придумать точный ход опровержения. Такие случаи закрыты как неподтверждённые стартовые оценки; следующая версия должна генерировать проверяемую линию.",
        "",
        "## Вывод",
        "Пилот считается завершённым только при полном покрытии train/validation/sealed_test, успешном аудите, закрытии обязательных расследований и сохранении итогового отчёта. "
        "Следующий этап — расширение до 30 000 сдач и добавление задач продолжения защиты/полной игры — должен начинаться отдельным решением после изучения этого отчёта.",
    ]

    out = Path(args.out)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
