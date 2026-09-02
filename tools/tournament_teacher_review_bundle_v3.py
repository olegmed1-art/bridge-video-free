#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.tournament_teacher_review_bundle_v3 import build_teacher_review_bundle


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _markdown(bundle: dict) -> str:
    lines = [
        "# Tournament teacher review bundle",
        "",
        f"Bundle ID: `{bundle['bundle_id']}`",
        f"Review state: **{bundle['review_state']}**",
        f"Items: **{bundle['item_count']}**",
        "",
        "## Event counts",
        "",
    ]
    for event_id, count in bundle["event_counts"].items():
        lines.append(f"- event {event_id}: {count}")
    lines.extend(
        [
            "",
            "## Safety boundary",
            "",
            "All reviews remain PENDING and require an explicit teacher decision. No automatic methodology mapping, student-error attribution, causal claim, or cross-event numeric ranking is allowed.",
            "",
            "## Component hashes",
            "",
        ]
    )
    for name, digest in bundle["component_sha256"].items():
        lines.append(f"- {name}: `{digest}`")
    lines.extend(["", bundle["interpretation"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a portable hash-bound Tournament Analyzer teacher-review bundle")
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--dossier", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    bundle = build_teacher_review_bundle(_load(args.queue), _load(args.ledger), _load(args.dossier))
    args.out_json.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(_markdown(bundle), encoding="utf-8")
    print(f"bundle_id={bundle['bundle_id']}")
    print(f"items={bundle['item_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
