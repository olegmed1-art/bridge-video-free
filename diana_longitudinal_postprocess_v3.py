#!/usr/bin/env python3
"""Workflow-compatibility entry point for the current v4.2 semantic layer.

The historical filename stays ``postprocess_v3`` so production workflow wiring
does not fork.  v4.2 preserves v4.1 Learning Interaction behavior and adds only
conservative report-visual partial-board reconstruction plus content-addressed,
SHA-verified Drive artifact idempotency.  Source video and raw ASR stay read-only.
"""
from __future__ import annotations

import diana_longitudinal_postprocess as base
from diana_longitudinal_postprocess_v4_2 import main
from diana_longitudinal_quality_v4_2 import (
    QUALITY_METHOD_VERSION,
    QUALITY_SCHEMA_VERSION,
    build_quality_layer,
)

# Compatibility values for code/tests that import the historical wrapper.
base.build_quality_layer = build_quality_layer
base.QUALITY_METHOD_VERSION = QUALITY_METHOD_VERSION
base.QUALITY_SCHEMA_VERSION = QUALITY_SCHEMA_VERSION
base.SCHEMA_VERSION = 5


if __name__ == "__main__":
    raise SystemExit(main())
