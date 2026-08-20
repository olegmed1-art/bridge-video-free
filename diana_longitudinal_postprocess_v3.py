#!/usr/bin/env python3
"""Run the mature Drive postprocessor with quality-v4.1 semantic refinement.

The filename stays ``postprocess_v3`` for workflow compatibility.  The outer
artifact envelope is reused, while the embedded quality schema is v4 and the
method revision is v4.1.  No media, ASR or identity evidence is reprocessed by
this wrapper.
"""
from __future__ import annotations

import diana_longitudinal_postprocess as base
from diana_longitudinal_quality_v4_1 import (
    QUALITY_METHOD_VERSION,
    QUALITY_SCHEMA_VERSION,
    build_quality_layer,
)

# Reuse mature Drive/Neon routing and replace only the semantic quality layer.
# Source-read-only, FREE, identity and staging-only authority gates are preserved.
base.build_quality_layer = build_quality_layer
base.QUALITY_METHOD_VERSION = QUALITY_METHOD_VERSION
base.QUALITY_SCHEMA_VERSION = QUALITY_SCHEMA_VERSION
base.SCHEMA_VERSION = 4

_legacy_summary_markdown = base._summary_markdown


def _summary_markdown_v41(payload):
    text = _legacy_summary_markdown(payload)
    text = text.replace(
        "# Диана — продольное извлечение v2",
        "# Диана — продольное извлечение v4.1",
    )
    marker = "## Quality-first counts"
    dynamic_note = "\n".join([
        "## Evidence-linked learning v4.1",
        "",
        "- Complete interactions require observed task → student action → teacher intervention → substantive student follow-up.",
        "- Weak interrogative cues without a real question do not create a decision window.",
        "- Task and student action must share bridge context, except a compact numeric answer to an explicit count question.",
        "- Nested prompts sharing one intervention/follow-up core are deduplicated and re-anchored to the latest matching source segment.",
        "- A follow-up never proves correctness by itself; correctness remains separately evidence-gated.",
        "- Acoustic speaker coverage and semantic role fallback are reported separately.",
        "- Structured board fragments merge only under an exact explicit board identity; board number/time/topic alone never merge.",
        "- Knowledge candidates are review-only; the legacy word 'promotable' is a deprecated compatibility alias.",
        "- Stable skill state is forbidden from one lesson alone.",
        "- Person-specific conclusions remain forbidden without a separate operational r29 identity mapping.",
        "- This is a semantic-only rebuild; raw ASR and source media remain unchanged.",
        "",
    ])
    return text.replace(marker, dynamic_note + marker)


base._summary_markdown = _summary_markdown_v41


if __name__ == "__main__":
    raise SystemExit(base.main())
