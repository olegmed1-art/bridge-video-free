"""Provider-neutral transcript comparison for local/OCI shadow evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .runner import _similarity


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    return max(0.0, min(float(a["end"]), float(b["end"])) - max(float(a["start"]), float(b["start"])))


def compare_transcripts(
    primary: Iterable[dict[str, Any]],
    shadow: Iterable[dict[str, Any]],
    *,
    agreement_threshold: float = 0.82,
) -> dict[str, Any]:
    """Compare two timestamped providers without treating confidence scales as equal."""

    left = [dict(item) for item in primary]
    right = [dict(item) for item in shadow]
    windows: list[dict[str, Any]] = []
    matched_right: set[int] = set()
    for item in left:
        candidates = [(index, other) for index, other in enumerate(right) if _overlap(item, other) > 0]
        if not candidates:
            windows.append({"start": item["start"], "end": item["end"], "status": "primary_only", "primary": item})
            continue
        index, other = max(candidates, key=lambda pair: _overlap(item, pair[1]))
        matched_right.add(index)
        score = _similarity(str(item.get("text") or ""), str(other.get("text") or ""))
        windows.append(
            {
                "start": min(float(item["start"]), float(other["start"])),
                "end": max(float(item["end"]), float(other["end"])),
                "status": "agree" if score >= agreement_threshold else "disagree",
                "similarity": round(score, 6),
                "primary": item,
                "shadow": other,
            }
        )
    for index, other in enumerate(right):
        if index not in matched_right:
            windows.append({"start": other["start"], "end": other["end"], "status": "shadow_only", "shadow": other})
    counts = {name: sum(x["status"] == name for x in windows) for name in ("agree", "disagree", "primary_only", "shadow_only")}
    return {
        "contract": "universal-video-provider-comparison-v1",
        "agreement_threshold": agreement_threshold,
        "summary": {"windows": len(windows), **counts, "review_required": counts["disagree"] + counts["primary_only"] + counts["shadow_only"]},
        "windows": sorted(windows, key=lambda item: (float(item["start"]), float(item["end"]))),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("primary", type=Path)
    parser.add_argument("shadow", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare_transcripts(_read_jsonl(args.primary), _read_jsonl(args.shadow))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
