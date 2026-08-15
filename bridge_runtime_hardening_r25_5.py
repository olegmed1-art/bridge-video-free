#!/usr/bin/env python3
"""Bridge Video 3.1 FREE internal candidate r25.5.

r25.5 inherits the validated r25.4 ASR isolation and semantic safeguards and
fixes a PDF text-layer regression observed on the known-good Sunday lesson:
adjacent timeline entries and learning-cycle labels could be visually separated
but extracted without a boundary (for example ``confidence: medium00:58:11`` or
``...вДействие ученика:``).

The fix is intentionally presentation-only.  It inserts explicit PDF line
breaks for those structured records and extends PDF QC so such concatenations
fail closed.  The public product name remains exactly ``3.1 FREE``.
"""
from __future__ import annotations

import os
import re

import bridge_runtime_hardening_r25_4 as r25_4
import bridge_worker_3_1_free as core
import run_master_3_1_free as base

REVISION = "3.1-free-r25.5"

_TIMELINE_RE = re.compile(r"^\d{2}:\d{2}:\d{2}–\d{2}:\d{2}:\d{2}\s+—")
_STRUCTURED_PREFIXES = (
    "Задача/ситуация:",
    "Действие ученика:",
    "Вмешательство преподавателя:",
    "Реакция и результат:",
)


def _needs_explicit_break(text: str) -> bool:
    raw = re.sub(r"<[^>]+>", "", str(text or "")).strip()
    return bool(_TIMELINE_RE.match(raw)) or raw.startswith(_STRUCTURED_PREFIXES)


def pdf_text_boundary_issues(text: str) -> list[str]:
    """Detect confirmed unsafe text-layer concatenations in generated PDFs."""
    raw = text or ""
    issues: list[str] = []
    if re.search(
        r"confidence:\s*(?:low|medium|high)(?=\d{2}:\d{2}:\d{2})",
        raw,
        flags=re.IGNORECASE,
    ):
        issues.append("text-boundary-timeline")
    if re.search(
        r"\S(?=(?:Действие ученика|Вмешательство преподавателя|Реакция и результат):)",
        raw,
    ):
        issues.append("text-boundary-learning-cycle")
    return issues


def install(token_func):
    """Install r25.4, then add deterministic PDF text-boundary hardening."""
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    had_requested = "BRIDGE_REQUESTED_ALGORITHM_REVISION" in os.environ
    saved_requested = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = r25_4.REVISION
    try:
        r25_4.install(token_func)
    finally:
        if had_requested:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = saved_requested or ""
        else:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION

    previous_pdf_report = base.pdf_report
    previous_pdfqc = base.pdfqc

    def pdf_report_r25_5(out, master, shots):
        # run_master_3_1_free imports Paragraph inside pdf_report.  Patch the
        # module attribute only for that call, and only add explicit breaks to
        # the structured records that exhibited the confirmed extraction bug.
        import reportlab.platypus as platypus

        original_paragraph = platypus.Paragraph

        def paragraph_with_boundary(text, style, *args, **kwargs):
            value = text
            if isinstance(value, str) and _needs_explicit_break(value):
                stripped = value.rstrip()
                if not stripped.endswith("<br/>"):
                    value = stripped + "<br/>"
            return original_paragraph(value, style, *args, **kwargs)

        platypus.Paragraph = paragraph_with_boundary
        try:
            return previous_pdf_report(out, master, shots)
        finally:
            platypus.Paragraph = original_paragraph

    def pdfqc_r25_5(path):
        result = dict(previous_pdfqc(path))
        import fitz

        doc = fitz.open(path)
        try:
            text = "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
        boundary_issues = pdf_text_boundary_issues(text)
        issues = list(result.get("issues") or []) + boundary_issues
        result["issues"] = sorted(set(issues))
        result["ok"] = not result["issues"]
        result["textBoundaryCheck"] = {
            "ok": not boundary_issues,
            "issues": boundary_issues,
        }
        return result

    base.pdf_report = pdf_report_r25_5
    base.pdfqc = pdfqc_r25_5


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic as semantic
    return semantic.process_job(token_func())
