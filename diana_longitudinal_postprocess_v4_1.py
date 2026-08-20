#!/usr/bin/env python3
"""Run the mature longitudinal postprocessor with quality-v4.1 semantics.

The transport/output envelope remains backward-compatible with existing Drive and
Neon consumers.  Only the semantic quality layer is replaced.  Source media and
raw ASR remain read-only; all authority and cost gates remain fail-closed.
"""
from __future__ import annotations

import diana_longitudinal_postprocess as base
from diana_longitudinal_quality_v4_1 import (
    QUALITY_METHOD_VERSION,
    QUALITY_SCHEMA_VERSION,
    build_quality_layer,
)

base.build_quality_layer = build_quality_layer
base.QUALITY_METHOD_VERSION = QUALITY_METHOD_VERSION
base.QUALITY_SCHEMA_VERSION = QUALITY_SCHEMA_VERSION
base.SCHEMA_VERSION = 4

_legacy_summary_markdown = base._summary_markdown


def _summary_markdown_v41(payload):
    text = _legacy_summary_markdown(payload)
    text = text.replace("# Диана — продольное извлечение v2", "# Диана — продольное извлечение v4.1")
    marker = "## Quality-first counts"
    note = "\n".join([
        "## Decision-window reconstruction v4.1",
        "",
        "- Полный цикл создаётся только из наблюдаемой цепочки задача → действие → вмешательство → содержательная реакция.",
        "- Слабые вопросительные маркеры без фактического вопроса не считаются задачей.",
        "- Вопрос и ответ должны быть связаны по бриджевому контексту; исключение — короткий числовой ответ на явный вопрос «сколько». ",
        "- Последующая реакция не доказывает правильность решения.",
        "- Имена участников и person-specific выводы этим слоем не назначаются.",
        "",
    ])
    return text.replace(marker, note + marker)


base._summary_markdown = _summary_markdown_v41


if __name__ == "__main__":
    raise SystemExit(base.main())
