#!/usr/bin/env python3
"""Evaluate the bounded local pixel extractor against a private real-image corpus.

Canonical truth must be created independently from source material. This evaluator
never uses DDS3 to create or repair truth and counts every rejected valid image in
the full-corpus accuracy denominator.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_school_api.dds3.vision_local import (
    LocalVisionError,
    extract_federation_yellow_observation,
)

MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _canonical_hands(observation) -> dict[str, str]:
    return {
        seat: ".".join(observation.hands[seat][suit] for suit in "SHDC")
        for seat in "NESW"
    }


def evaluate(manifest: Path, corpus_root: Path) -> dict:
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    exact = 0
    rejected = 0
    wrong_accepts = 0
    metadata_exact = 0
    failures: list[dict] = []
    for record in records:
        source_dir = corpus_root / Path(record["source_file"]).stem
        image_path = source_dir / record["crop_file"]
        media_type = MEDIA_TYPES.get(image_path.suffix.lower())
        if media_type is None:
            raise ValueError(f"unsupported corpus media type: {image_path}")
        raw = image_path.read_bytes()
        try:
            observation = extract_federation_yellow_observation(
                raw, media_type=media_type, filename=image_path.name
            )
        except LocalVisionError as exc:
            rejected += 1
            failures.append({"id": record["id"], "status": "rejected", "reason": str(exc)})
            continue
        hands = _canonical_hands(observation)
        board = int(observation.board_number.value)
        dealer = str(observation.dealer.value)
        vulnerability = str(observation.vulnerability.value)
        expected_vul = {"N-S": "NS", "E-W": "EW"}.get(record["vulnerability"], record["vulnerability"])
        meta_ok = board == record["board"] and dealer == record["dealer"] and vulnerability == expected_vul
        if meta_ok:
            metadata_exact += 1
        if meta_ok and hands == record["hands"]:
            exact += 1
        else:
            wrong_accepts += 1
            failures.append(
                {
                    "id": record["id"],
                    "status": "wrong_accept",
                    "metadata_exact": meta_ok,
                    "expected_hands": record["hands"],
                    "observed_hands": hands,
                }
            )
    total = len(records)
    accepted = exact + wrong_accepts
    report = {
        "extractor": "local_tesseract_federation_yellow_v1",
        "real_valid_images": total,
        "exact_deals": exact,
        "wrong_accepts": wrong_accepts,
        "rejected_valid_images": rejected,
        "metadata_exact_accepted": metadata_exact,
        "exact_full_corpus_accuracy": exact / total if total else 0.0,
        "accepted_precision": exact / accepted if accepted else 0.0,
        "valid_image_rejection_rate": rejected / total if total else 0.0,
        "dds3_used_for_truth": False,
        "bridge_inference_repair": False,
        "failures": failures,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.manifest, args.corpus_root)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["wrong_accepts"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
