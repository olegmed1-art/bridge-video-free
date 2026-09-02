#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from bridge_school_api.tournament_opening_lead_dds3_v3 import analyze_opening_leads


def _post_position(base_url: str, token: str, position: Mapping[str, Any]) -> dict[str, Any]:
    body = json.dumps(
        {"operation": "position_all_moves", "position": dict(position)},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/compute",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DDS3 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DDS3 unavailable: {exc}") from exc
    return result


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Tournament opening-lead DDS3 technical evidence",
        "",
        f"- Provider: `{report['provider_native_key']}`",
        f"- Played opening leads analyzed: **{report['played_leads_analyzed']}**",
        f"- Target-pair opening leads analyzed: **{report['target_pair_opening_leads_analyzed']}**",
        f"- Actual leads DD-optimal: **{report['dd_optimal_actual_leads']}**",
        f"- Positive-regret actual leads: **{report['positive_regret_actual_leads']}**",
        f"- Target-pair positive-regret teacher-review candidates: **{report['target_pair_positive_regret_candidates']}**",
        "",
        "This is double-dummy technical evidence only. Positive regret is not automatic student-error attribution.",
        "",
        "| Board | Leader | Target pair lead? | Actual lead | DD regret | DD optimal? |",
        "|---:|:---:|:---:|:---:|---:|:---:|",
    ]
    for item in report["results"]:
        lines.append(
            f"| {item['board_number']} | {item['opening_leader']} | "
            f"{'yes' if item['target_pair_made_opening_lead'] else 'no'} | {item['actual_opening_lead']} | "
            f"{item['lead_regret_tricks']} | {'yes' if item['actual_lead_dd_optimal'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    source = json.loads(args.facts.read_text(encoding="utf-8"))
    report = analyze_opening_leads(
        source,
        solve_position=lambda position: _post_position(args.base_url, args.token, position),
    )
    args.out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.out_md.write_text(_markdown(report), encoding="utf-8")
    print(
        "TOURNAMENT_OPENING_LEAD_DDS3_COMPLETE",
        f"played={report['played_leads_analyzed']}",
        f"target_leads={report['target_pair_opening_leads_analyzed']}",
        f"target_candidates={report['target_pair_positive_regret_candidates']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
