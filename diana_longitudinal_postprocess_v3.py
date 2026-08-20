#!/usr/bin/env python3
"""Run the existing longitudinal Drive postprocessor with quality-v3 learning logic.

The outer v2 artifact envelope is intentionally kept for backward compatibility
with current Drive/Neon consumers.  The embedded quality layer, method version,
schema version and staging candidates are upgraded to v3.
"""
from __future__ import annotations

import diana_longitudinal_postprocess as base
from diana_longitudinal_quality_v3 import (
    QUALITY_METHOD_VERSION,
    QUALITY_SCHEMA_VERSION,
    build_quality_layer,
)

# Reuse the mature Drive/Neon routing code, but replace only the semantic quality
# layer.  This keeps source-read-only, FREE and staging-only gates unchanged.
base.build_quality_layer = build_quality_layer
base.QUALITY_METHOD_VERSION = QUALITY_METHOD_VERSION
base.QUALITY_SCHEMA_VERSION = QUALITY_SCHEMA_VERSION
base.SCHEMA_VERSION = 3

_legacy_summary_markdown = base._summary_markdown


def _summary_markdown_v3(payload):
    text = _legacy_summary_markdown(payload)
    text = text.replace(
        "# Диана — продольное извлечение v2",
        "# Диана — продольное извлечение v3",
    )
    marker = "## Quality-first counts"
    dynamic_note = "\n".join([
        "## Dynamic learning v3",
        "",
        "- Skill-state, hypothesis, counterevidence and next-probe objects are candidate-only.",
        "- Stable skill state is forbidden from one lesson alone.",
        "- Person-specific conclusions remain forbidden without operational identity mapping.",
        "",
    ])
    return text.replace(marker, dynamic_note + marker)


base._summary_markdown = _summary_markdown_v3


if __name__ == "__main__":
    raise SystemExit(base.main())
