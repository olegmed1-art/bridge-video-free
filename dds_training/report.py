from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from audit import audit_database
from config import ALGORITHM_VERSION
from learning import build_learning_plan


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

    skill_rows = con.execute(
        """
        SELECT skill_key,status,evidence_count,transfer_count,regression_passes,
               regression_failures,counterexample_count
        FROM skill_profiles ORDER BY skill_key
        """
    ).fetchall()
    skill_status = Counter(r[1] for r in skill_rows)
    high_conf_errors = con.execute(
        "SELECT COUNT(*) FROM skill_evidence WHERE confidence='high' AND outcome!='success'"
    ).fetchone()[0]
    corrections = con.execute("SELECT COUNT(*) FROM correction_events").fetchone()[0]
    regression_cases = con.execute("SELECT COUNT(*) FROM regression_cases WHERE active=1").fetchone()[0]
    experience_events = con.execute("SELECT COUNT(*) FROM experience_events").fetchone()[0]
    rule_versions = con.execute("SELECT COUNT(*) FROM rule_versions").fetchone()[0]
    spaced_reviews = con.execute("SELECT COUNT(*) FROM learning_queue WHERE purpose='spaced_review' AND status='planned'").fetchone()[0]

    reasoning_counts = Counter()
    gross_decl_loss = gross_def_gift = recovered_loss = value_trajectories = 0
    first_error_counts = Counter()
    for event_type, payload_json in con.execute(
        "SELECT event_type,payload_json FROM experience_events WHERE event_type IN ('reasoning_review','value_trajectory')"
    ):
        payload = json.loads(payload_json)
        if event_type == "reasoning_review":
            reasoning_counts[payload.get("verdict", "unknown")] += 1
        else:
            value_trajectories += 1
            gross_decl_loss += int(payload.get("declarer_gross_loss", 0))
            gross_def_gift += int(payload.get("defense_gross_gift", 0))
            recovered_loss += int(payload.get("recovered_declarer_loss", 0))
            first = payload.get("first_error")
            if first:
                first_error_counts[first.get("actor", "unknown")] += 1

    audit = audit_database(con)
    plan = build_learning_plan(con, 10)

    lines = [
        f"# DDS learning report — {stage}",
        "",
        "## Corpus / run state",
        f"- Algorithm version: `{ALGORITHM_VERSION}`",
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
        "## Durable experience memory",
        f"- Experience events: {experience_events}",
        f"- Active regression cases: {regression_cases}",
        f"- Planned spaced reviews: {spaced_reviews}",
        f"- High-confidence errors: {high_conf_errors}",
        f"- Correction events (append-only): {corrections}",
        f"- Versioned bridge rules: {rule_versions}",
        f"- Skills: candidate {skill_status['candidate']}, testing {skill_status['testing']}, confirmed {skill_status['confirmed']}, stable {skill_status['stable']}, weakened {skill_status['weakened']}",
        "",
        "### Skill states",
    ]
    if not skill_rows:
        lines.append("- No skill evidence yet.")
    for key, status, evidence, transfer, reg_pass, reg_fail, counter in skill_rows:
        lines.append(
            f"- `{key}` — {status}; evidence {evidence}, transfer {transfer}, regression {reg_pass}/{reg_fail}, counterexamples {counter}"
        )

    lines += [
        "",
        "## Reasoning quality independent of final result",
        f"- Reasoning reviews: {sum(reasoning_counts.values())}",
        f"- Correct result but wrong reasoning: {reasoning_counts['correct_result_wrong_reasoning']}",
        f"- Correct reasoning: {reasoning_counts['correct']}",
        f"- Incorrect reasoning: {reasoning_counts['incorrect']}",
        f"- Needs review: {reasoning_counts['needs_review']}",
        "",
        "## DD value trajectories / first-error accounting",
        f"- Recorded trajectories: {value_trajectories}",
        f"- First error by declarer: {first_error_counts['declarer']}",
        f"- First error by defense: {first_error_counts['defense']}",
        f"- Gross declarer tricks lost: {gross_decl_loss}",
        f"- Gross tricks gifted by defense: {gross_def_gift}",
        f"- Previously lost declarer tricks later restored by defense: {recovered_loss}",
        "",
        "## Mandatory investigations",
        f"- Better-than-DDS / defense-over-DDS claims requiring replay: {investigations}",
        "- Every such case must be replayed against optimal opposition to find the first point where the proposed line relies on an opponent error.",
        "- A correct final trick count is not enough if the proposed reasoning or line is wrong.",
        "",
        "## Error codes",
    ]
    for code, count in errors.most_common():
        lines.append(f"- {code}: {count}")

    lines += ["", "## Next targeted learning plan"]
    if not plan:
        lines.append("- Not enough evaluated evidence to rank weaknesses.")
    for item in plan:
        lines.append(
            f"- `{item['skill_key']}` priority {item['priority']:.4f}: error rate {item['error_rate']:.2%}, "
            f"mean regret {item['mean_regret']:.3f}, high-confidence errors {item['high_confidence_errors']}; "
            f"recommend {item['recommended_targeted_tasks']} transfer/counterexample/regression/symmetry/perturbation tasks."
        )

    lines += [
        "",
        "## Database audit",
        f"- Status: **{audit['status']}**",
    ]
    if not audit["issues"]:
        lines.append("- No database-provenance problems detected.")
    for issue in audit["issues"]:
        lines.append(f"- {issue['severity'].upper()} `{issue['code']}`: {issue['count']} — {issue['detail']}")

    lines += [
        "",
        "## Learning policy",
        "Locked predictions, DDS results and error events are immutable. A discovered mistake in import, classification or interpretation is appended as a correction event; the original evidence remains auditable.",
        "A rule is not promoted because DDS contradicted one deal. It must survive unseen transfer deals, symmetry/perturbation checks, counterexamples and regression checks. High-confidence errors receive extra priority because they indicate a likely wrong internal heuristic rather than simple uncertainty.",
        "",
        "## User decision before next stage",
        "Do not expand the corpus mechanically. Review the strongest stable weaknesses and proposed rule changes, then approve whether the next corpus should remain broad or become targeted.",
        "",
        "## Stage completion rule",
        "A stage is complete only after: train evaluation, validation evaluation, error investigation, experience/skill update, spaced retention checks, regression pass, counterexample checks, sealed-test evaluation, database audit, and this report.",
    ]

    out = work / f"report_{stage}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
