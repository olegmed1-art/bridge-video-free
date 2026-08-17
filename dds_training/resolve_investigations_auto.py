from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_predictor import _side_stats
from investigations import open_investigations, resolve_investigation, sync_required_investigations
from storage import connect


def load_tasks(paths: list[Path]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            out[row["task_id"]] = row
    return out


def contract_diagnostic(task: dict, pred: dict, result: dict) -> tuple[str, str, str, dict]:
    stats = _side_stats(task)
    predicted = int(pred.get("tricks", result.get("predicted_tricks", 0)))
    actual = int(result["dds_tricks"])
    strain = int(task["strain"])
    if strain == 4:
        shape = (
            f"линия имеет {stats['side_hcp']} очков, максимум {stats['max_len']} карт в общей масти "
            f"и приблизительно {stats['stoppers']} задержки"
        )
        mechanism = "очки и общая длина были приняты за готовые и доступные взятки без проверки входов, разблокирования и темпа"
    else:
        shape = (
            f"линия имеет {stats['side_hcp']} очков, фит {stats['fit']} карт, "
            f"козырных очков {stats['trump_hcp']} и {stats['shortness']} короткости"
        )
        mechanism = "сила фита и короткости была переоценена без полного подсчёта козырных и побочных отдач"

    line = pred.get("line") or []
    if line:
        line_note = f"В прогнозе была записана линия из {len(line)} карт, но она не доказала результат против оптимальной защиты."
    else:
        line_note = "В прогнозе не была записана конкретная законная линия розыгрыша."

    cause = (
        f"Статическая слепая эвристика завысила оценку с {actual} до {predicted} взяток: {shape}; "
        f"{mechanism}. {line_note}"
    )
    first_refutation = (
        f"Опровержение возникает уже в начальной позиции: DDS устанавливает потолок {actual} взяток при любой "
        f"законной линии против оптимальной защиты, тогда как locked-прогноз заявил {predicted}. "
        "Без конкретной линии нет более позднего хода, на котором можно честно локализовать ошибку; ошибочным является сам неподтверждённый overclaim."
    )
    lesson = (
        "Не превращать HCP, фит, длину или короткость непосредственно в гарантированные взятки. "
        "Перед заявлением результата строить законную карточную линию, проверять входы, темп, отдачи и лучший ответ защиты; "
        "если линия не построена, отмечать оценку как приблизительную и не утверждать результат выше технического потолка."
    )
    evidence = {
        "resolution_quality": "structural_estimate_without_verified_line",
        "predicted_tricks": predicted,
        "dds_tricks": actual,
        "prediction_reason": pred.get("reason"),
        "prediction_line": line,
        "features": stats,
    }
    return cause, first_refutation, lesson, evidence


def defense_diagnostic(task: dict, pred: dict, result: dict) -> tuple[str, str, str, dict]:
    expected = pred.get("expected_defense_tricks")
    best = result.get("best_defense_tricks")
    cause = (
        f"Прогноз защиты ожидал {expected} взяток, хотя DDS допускает максимум {best}. "
        "Оценка не была подтверждена полной линией защиты против оптимального розыгрыша."
    )
    first_refutation = (
        f"Уже стартовая DDS-позиция ограничивает защиту {best} взятками. Любая линия, обещающая {expected}, "
        "неявно требует ошибки разыгрывающего; конкретная ошибка не может быть названа без заявленной полной линии."
    )
    lesson = (
        "Отделять собственные гарантированные защитные взятки от взяток, которые возникают только после ошибки разыгрывающего; "
        "для claims выше DDS обязательно записывать полную линию и первый оптимальный ответ разыгрывающего."
    )
    return cause, first_refutation, lesson, {
        "resolution_quality": "structural_defense_estimate_without_verified_line",
        "expected_defense_tricks": expected,
        "best_defense_tricks": best,
        "prediction_reason": pred.get("reason"),
        "prediction_line": pred.get("line") or [],
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Resolve mandatory better-than-DDS estimate investigations honestly and append-only")
    p.add_argument("--work", required=True)
    p.add_argument("--tasks", nargs="+", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    work = Path(args.work)
    con = connect(work / "training.sqlite3")
    sync = sync_required_investigations(con, args.run_id)
    tasks = load_tasks([Path(x) for x in args.tasks])
    predictions = {
        task_id: json.loads(payload)
        for task_id, payload in con.execute("SELECT task_id,prediction_json FROM predictions")
    }

    resolved = []
    skipped = []
    for item in open_investigations(con):
        task_id = item["task_id"]
        task = tasks.get(task_id)
        pred = predictions.get(task_id)
        result = item["dds_result"]
        if task is None or pred is None:
            skipped.append({"task_id": task_id, "reason": "task_or_prediction_missing"})
            continue
        if task["task_type"] == "contract_tricks":
            cause, first_refutation, lesson, evidence = contract_diagnostic(task, pred, result)
        elif task["task_type"] == "opening_lead":
            cause, first_refutation, lesson, evidence = defense_diagnostic(task, pred, result)
        else:
            skipped.append({"task_id": task_id, "reason": f"unsupported_type:{task['task_type']}"})
            continue
        event_id = resolve_investigation(
            con,
            task_id=task_id,
            cause=cause,
            first_refutation=first_refutation,
            lesson=lesson,
            run_id=args.run_id,
            evidence=evidence,
        )
        resolved.append({"task_id": task_id, "event_id": event_id, "quality": evidence["resolution_quality"]})

    con.commit()
    remaining = open_investigations(con)
    summary = {
        "sync": sync,
        "resolved": len(resolved),
        "skipped": skipped,
        "remaining_open": len(remaining),
        "resolution_policy": (
            "These pilot overclaims came from static estimates without verified play lines. They are resolved as unsupported "
            "initial claims; exact card-level first refutation is deliberately not invented. Future predictor versions must emit a line."
        ),
        "resolved_items": resolved,
    }
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if skipped or remaining:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
