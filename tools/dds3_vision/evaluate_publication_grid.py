#!/usr/bin/env python3
"""Real-source field gate for the bounded publication-grid pixel extractor."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
from pathlib import Path

import fitz
from PIL import Image

from bridge_school_api.dds3.vision_publication_grid import (
    PublicationGridVisionError,
    extract_publication_grid_observation,
)
from tools.dds3_vision.evaluate_publication_cross import (
    SAMPLES,
    _download,
    _find_clip,
    _metadata_truth,
    _observation_hands,
    _truth_hands,
)

# Both have independently parseable source truth; only layouts genuinely supported by
# pixels may become exact positives. Unsupported layouts must reject rather than repair.
GRID_SAMPLES = [SAMPLES[2], SAMPLES[3]]


def _evaluate(sample, root: Path, dpi: int) -> dict:
    pdf_path = root / f"{sample.source_id}.pdf"
    _download(sample.url, pdf_path)
    source_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    document = fitz.open(pdf_path)
    board, dealer, vulnerability = _metadata_truth(sample.title)
    for page_index, page in enumerate(document):
        clip = _find_clip(page, sample.title)
        if clip is None:
            continue
        truth_hands = _truth_hands(page, clip)
        pix = page.get_pixmap(
            matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False
        )
        image_bytes = pix.tobytes("png")
        image_sha = hashlib.sha256(image_bytes).hexdigest()
        try:
            observed = extract_publication_grid_observation(
                image_bytes,
                media_type="image/png",
                filename=f"{sample.source_id}.png",
            )
        except PublicationGridVisionError as exc:
            return {
                "id": sample.source_id,
                "status": "rejected",
                "reason": str(exc),
                "source_sha256": source_sha,
                "image_sha256": image_sha,
            }
        hands_ok = _observation_hands(observed) == truth_hands
        metadata_ok = (
            int(observed.board_number.value) == board
            and str(observed.dealer.value) == dealer
            and str(observed.vulnerability.value) == vulnerability
        )

        negative = "not_run"
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            # Cut through the South hand, not merely trailing article whitespace.
            cropped = image.crop((0, 0, image.width, max(1, int(image.height * 0.72))))
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG")
            extract_publication_grid_observation(
                buffer.getvalue(),
                media_type="image/png",
                filename=f"{sample.source_id}-cropped.png",
            )
            negative = "wrong_accept"
        except PublicationGridVisionError:
            negative = "rejected"

        return {
            "id": sample.source_id,
            "status": "exact" if hands_ok and metadata_ok else "wrong_accept",
            "hands_exact": hands_ok,
            "metadata_exact": metadata_ok,
            "negative_crop": negative,
            "source_sha256": source_sha,
            "image_sha256": image_sha,
            "page": page_index + 1,
            "board": board,
        }
    return {"id": sample.source_id, "status": "source_title_not_found"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dds3-grid-field-") as temp:
        root = Path(temp)
        results = []
        for sample in GRID_SAMPLES:
            try:
                results.append(_evaluate(sample, root, args.dpi))
            except Exception as exc:
                results.append(
                    {
                        "id": sample.source_id,
                        "status": "field_error",
                        "reason": f"{type(exc).__name__}:{exc}",
                    }
                )
    exact = sum(row.get("status") == "exact" for row in results)
    wrong = sum(row.get("status") == "wrong_accept" for row in results)
    negative = sum(row.get("negative_crop") == "rejected" for row in results)
    report = {
        "extractor": "local_tesseract_publication_grid_v1",
        "real_public_sources": len(GRID_SAMPLES),
        "exact": exact,
        "wrong_accepts": wrong,
        "negative_crop_rejected": negative,
        "truth_source": "embedded source PDF vector text",
        "dds3_used_for_truth": False,
        "bridge_inference_repair": False,
        "paid_or_cloud_vision": False,
        "results": results,
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if exact >= 1 and wrong == 0 and negative >= exact else 3


if __name__ == "__main__":
    raise SystemExit(main())
