#!/usr/bin/env python3
"""Workflow-compatibility entry point for the current v4.2 semantic layer.

The historical filename stays ``postprocess_v3`` so production workflow wiring
does not fork. v4.2 preserves v4.1 Learning Interaction behavior and adds only
conservative report-visual partial-board reconstruction plus content-addressed,
SHA-verified Drive artifact idempotency. Source video and raw ASR stay read-only.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys

import diana_longitudinal_postprocess as base
from diana_longitudinal_quality_v4_2 import (
    QUALITY_METHOD_VERSION,
    QUALITY_SCHEMA_VERSION,
    build_quality_layer,
)
from diana_longitudinal_summary_v4_2 import render_summary

# Compatibility values for code/tests that import the historical wrapper.
base.build_quality_layer = build_quality_layer
base.QUALITY_METHOD_VERSION = QUALITY_METHOD_VERSION
base.QUALITY_SCHEMA_VERSION = QUALITY_SCHEMA_VERSION
base.SCHEMA_VERSION = 5


def _ensure_report_runtime() -> None:
    """Install pinned FREE report-image dependencies only when heavy runtime was skipped."""
    missing = []
    if importlib.util.find_spec('cv2') is None:
        missing.append('opencv-python-headless==5.0.0.93')
    if importlib.util.find_spec('PIL') is None:
        missing.append('Pillow==12.3.0')
    if not missing:
        return
    print('V42_REPORT_RUNTIME_BOOTSTRAP: installing pinned FREE dependencies: ' + ', '.join(missing))
    subprocess.check_call([
        sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', *missing,
    ])


def main() -> int:
    _ensure_report_runtime()
    import diana_longitudinal_postprocess_v4_2 as current

    # Reporting-only remediation from the D3/D4/D5 production audit. The
    # evidence payload, quality builder and all authority gates remain intact.
    current._summary_markdown = render_summary
    return current.main()


if __name__ == "__main__":
    raise SystemExit(main())
