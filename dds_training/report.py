from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path


def _pct(a: int, b: int) -> str:
    return "n/a" if not b else f"{100.0*a/b:.2f}%"


def generate_report(work: Path, stage: str) -> Path:
    db_path = work / "training.sqlite3"
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT task_type,split,result_json FROM dds_results").fetchall()
    errors = Counter()
    split_counts = Counter()

    ct_total = ct_exact = ct_over = ct_under = 0
    ct_abs_error = 0.0
    ol_total = ol_opt = ol_illegal = 0
    ol_regret_sum = 0.0
    ol_regret_2plus = 0
    investigations = 0

    for task_type, split, result_json in rows:
        r = json.loads(result_json)
        split_counts[split] += 1
        errors[r.get("error_code", "UNKNOWN")] += 1
        investigations += int(bool(r.get("investigation_required")))
        if task_type == "contract_tricks":
            ct_total += 1
            d = int(r["delta_pred_minus_dds"])
            ct_abs_error += abs(d)
            ct_exact += int(d == 0)
            ct_over += int(d > 0)
            ct_under += int(d < 0)
        elif task_type == "opening_lead":
            ol_total += 1
            if not r.get("legal_or_equivalent"):
                ol_illegal += 1
            regret = r.get("dd_regret")
            if regret is not None:
                regret = float(regret)
                ol_regret_sum += regret
                ol_opt += int(regret == 0)
                ol_regret_2plus += int(regret >= 2)

    corpus_summary = {}
    p = work / "corpus_summary.json"
    if p.exists():
        corpus_summary = json.loads(p.read_text(encoding="utf-8"))

    lines = [
        f"# DDS learning report — {stage}",
        "",
        "## Corpus / run state",
        f"- Raw deals: {corpus_summary.get('count', 'n/a')}",
        f"- Seed: {corpus_summary.get('seed', 'n/a')}",
        f"- Corpus SHA-256: `{corpus_summary.get('raw_sha256', 'n/a')}`",
        f"- DDS-evaluated tasks: {len(rows)}",
        f"- Train / validation / sealed evaluated: {split_counts['train']} / {split_counts['validation']} / {split_counts['sealed_test']}",
        "",
        "## Declarer / contract-value tasks",
        f"- Evaluated: {ct_total}",
        f"- Exact trick prediction: {_pct(ct_exact, ct_total)}",
        f"- Mean absolute trick error: {('n/a' if not ct_total else f'{ct_abs_error/ct_total:.3f}')}",
        f"- Claims above DDS: {ct_over}",
        f"- Missed available tricks: {ct_under}",
        "",
        "## Defense / opening-lead tasks",
        f"- Evaluated: {ol_total}",
        f"- Equal-optimal leads: {_pct(ol_opt, ol_total)}",
        f"- Mean DD-regret: {('n/a' if not ol_total else f'{ol_regret_sum/ol_total:.3f}')}",
        f"- Regret >= 2 tricks: {ol_regret_2plus}",
        f"- Illegal/unrepresented lead predictions: {ol_illegal}",
        "",
        "## Mandatory investigations",
        f"- Better-than-DDS / defense-over-DDS claims requiring replay: {investigations}",
        "- Every such case must be replayed against optimal opposition to find the first point where the proposed line relies on an opponent error.",
        "",
        "## Error codes",
    ]
    for code, count in errors.most_common():
        lines.append(f"- {code}: {count}")

    lines += [
        "",
        "## Skills and algorithm changes",
        "This section is completed by the methodological assistant after clustering the error events. A skill is promoted from candidate to confirmed only after transfer to new unseen deals and regression checks.",
        "",
        "## User decision before next stage",
        "Do not expand the corpus mechanically. Review the report, approve or reject proposed algorithm changes, and choose whether the next corpus should be broad or targeted at the largest stable weakness.",
        "",
        "## Stage completion rule",
        "A stage is complete only after: train evaluation, validation evaluation, error investigation, skill/rule update, regression pass, sealed-test evaluation, and this report.",
    ]

    out = work / f"report_{stage}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
