#!/usr/bin/env python3
"""Materialize the reviewed quality-v2 implementation bundle.

This bootstrap is deterministic and auditable. It does not fetch anything from
outside the repository and refuses a changed bundle digest.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import zlib

B64_SHA256 = "de5e58129d3b85b7ec4a40b7482e68578b17df7d174717234b6fa2f866911487"
RAW_SHA256 = "cb2244eefec05f897e64e38985579312239882645a7d6a84b2213586083f61ac"
EXPECTED_FILES = {
    ".github/workflows/bridge-video-3.1-free.yml",
    ".github/workflows/diana-longitudinal-regression.yml",
    ".github/workflows/diana-semantic-refinement.yml",
    "bridge_speaker_diarization.py",
    "database/migrations/0014_analysis_candidate_staging.sql",
    "database/video_candidate_persistence.py",
    "diana_longitudinal_postprocess.py",
    "diana_longitudinal_quality_v2.py",
    "run_master_3_1_free_semantic_v2.py",
    "tests/test_diana_longitudinal_postprocess.py",
    "tests/test_diana_longitudinal_quality_v2.py",
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    chunks = sorted((root / "tools" / "quality_v2_bundle").glob("chunk*.b64"))
    if len(chunks) != 9:
        raise SystemExit(f"expected 9 bundle chunks, found {len(chunks)}")
    encoded = "".join(path.read_text(encoding="utf-8") for path in chunks).encode("ascii")
    if hashlib.sha256(encoded).hexdigest() != B64_SHA256:
        raise SystemExit("quality-v2 base64 bundle digest mismatch")
    raw = zlib.decompress(base64.b64decode(encoded, validate=True))
    if hashlib.sha256(raw).hexdigest() != RAW_SHA256:
        raise SystemExit("quality-v2 raw bundle digest mismatch")
    files = json.loads(raw.decode("utf-8"))
    if set(files) != EXPECTED_FILES:
        raise SystemExit(f"bundle file manifest mismatch: {sorted(files)}")
    for relative, content in sorted(files.items()):
        target = (root / relative).resolve()
        if root not in target.parents:
            raise SystemExit(f"unsafe bundle path: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        print(f"MATERIALIZED {relative} {len(content.encode('utf-8'))} bytes")
    print("QUALITY_V2_BUNDLE_PASS")


if __name__ == "__main__":
    main()
