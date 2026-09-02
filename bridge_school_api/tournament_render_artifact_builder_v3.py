from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .tournament_render_release_evidence_v3 import (
    TournamentRenderEvidenceError,
    build_evidence_bound_portfolio_release_gate,
    validate_render_release_evidence,
)


class TournamentRenderArtifactBuilderError(ValueError):
    pass


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TournamentRenderArtifactBuilderError(f"{field} is required")
    return text


def _expected_slide_keys(coverage_manifest: Mapping[str, Any]) -> list[str]:
    if coverage_manifest.get("schema") != "tournament-coverage-manifest-v1":
        raise TournamentRenderArtifactBuilderError("unsupported coverage manifest schema")
    raw = coverage_manifest.get("expected_slide_keys")
    if isinstance(raw, (str, bytes)) or not isinstance(raw, list):
        raise TournamentRenderArtifactBuilderError("expected_slide_keys must be a list")
    keys = [str(item).strip() for item in raw]
    if not keys or any(not item for item in keys) or len(keys) != len(set(keys)):
        raise TournamentRenderArtifactBuilderError("expected_slide_keys must be non-empty and unique")
    return keys


def _inspect_pdf(path: Path, *, blank_page_threshold: float) -> dict[str, Any]:
    if not path.is_file():
        raise TournamentRenderArtifactBuilderError(f"rendered PDF not found: {path}")
    if path.stat().st_size <= 0:
        raise TournamentRenderArtifactBuilderError("rendered PDF is empty")
    with path.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            raise TournamentRenderArtifactBuilderError("render artifact is not a PDF")

    try:
        import fitz
    except Exception as exc:  # pragma: no cover - environment boundary
        raise TournamentRenderArtifactBuilderError("PyMuPDF is required for artifact-derived visual QA") from exc

    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise TournamentRenderArtifactBuilderError(f"cannot open rendered PDF: {exc}") from exc

    ratios: list[float] = []
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), alpha=False)
            if pix.width <= 0 or pix.height <= 0:
                ratios.append(0.0)
                continue
            data = pix.samples
            nonwhite = 0
            for index in range(0, len(data), pix.n):
                if min(data[index : index + 3]) < 245:
                    nonwhite += 1
            ratios.append(nonwhite / (pix.width * pix.height))
        page_count = len(doc)
    finally:
        doc.close()

    blank_pages = [index + 1 for index, ratio in enumerate(ratios) if ratio < blank_page_threshold]
    return {
        "page_count": page_count,
        "nonwhite_ratios": ratios,
        "blank_pages": blank_pages,
        "qa_engine_name": "PyMuPDF",
        "qa_engine_version": _required_text(getattr(fitz, "VersionBind", None), "PyMuPDF version"),
    }


def build_artifact_derived_render_evidence(
    *,
    coverage_manifest: Mapping[str, Any],
    rendered_pdf_path: str | Path,
    renderer_name: str,
    renderer_version: str,
    render_provenance: Mapping[str, Any],
    qa_provenance: Mapping[str, Any],
    blank_page_threshold: float = 0.005,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build render + visual-QA evidence from the actual PDF bytes.

    The caller cannot supply the artifact SHA, size, slide ordering, page count, QA
    pass flag or blank-page result. Those values are derived from the exact
    coverage manifest and the rendered PDF itself.
    """
    if not 0.0 <= float(blank_page_threshold) < 1.0:
        raise TournamentRenderArtifactBuilderError("blank_page_threshold must be within [0, 1)")
    provider_native_key = _required_text(coverage_manifest.get("provider_native_key"), "provider_native_key")
    slide_keys = _expected_slide_keys(coverage_manifest)
    coverage_sha256 = _canonical_sha256(coverage_manifest)
    path = Path(rendered_pdf_path)
    inspection = _inspect_pdf(path, blank_page_threshold=float(blank_page_threshold))
    if inspection["page_count"] != len(slide_keys):
        raise TournamentRenderArtifactBuilderError(
            f"rendered page count {inspection['page_count']} does not match expected slide count {len(slide_keys)}"
        )
    if inspection["blank_pages"]:
        raise TournamentRenderArtifactBuilderError(
            f"rendered PDF contains blank pages: {inspection['blank_pages']}"
        )
    if not isinstance(render_provenance, Mapping) or not render_provenance:
        raise TournamentRenderArtifactBuilderError("render provenance is required")
    if not isinstance(qa_provenance, Mapping) or not qa_provenance:
        raise TournamentRenderArtifactBuilderError("QA provenance is required")

    render_sha256 = _file_sha256(path)
    render_size = path.stat().st_size
    render_evidence = {
        "schema": "tournament-render-evidence-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": provider_native_key,
        "coverage_manifest_sha256": coverage_sha256,
        "artifact": {
            "sha256": render_sha256,
            "size_bytes": render_size,
            "media_type": "application/pdf",
        },
        "slide_keys": slide_keys,
        "rendered_page_count": inspection["page_count"],
        "renderer": {
            "name": _required_text(renderer_name, "renderer name"),
            "version": _required_text(renderer_version, "renderer version"),
        },
        "provenance": dict(render_provenance),
        "artifact_values_derived_from_bytes": True,
        "slide_order_derived_from_exact_coverage": True,
    }
    visual_qa_evidence = {
        "schema": "tournament-visual-qa-evidence-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": provider_native_key,
        "coverage_manifest_sha256": coverage_sha256,
        "render_artifact_sha256": render_sha256,
        "checked_slide_count": inspection["page_count"],
        "status": "PASS",
        "pass": True,
        "hard_failures": [],
        "qa_engine": {
            "name": inspection["qa_engine_name"],
            "version": inspection["qa_engine_version"],
        },
        "provenance": {
            **dict(qa_provenance),
            "blank_page_threshold": float(blank_page_threshold),
            "nonwhite_ratio_min": min(inspection["nonwhite_ratios"]),
            "nonwhite_ratio_max": max(inspection["nonwhite_ratios"]),
        },
        "qa_values_derived_from_rendered_bytes": True,
    }
    validate_render_release_evidence(
        coverage_manifest=coverage_manifest,
        render_evidence=render_evidence,
        visual_qa_evidence=visual_qa_evidence,
    )
    return render_evidence, visual_qa_evidence


def build_artifact_derived_portfolio_release_gate(
    *,
    preanalysis_gate: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    mp_availability: Mapping[str, Any],
    event_teacher_review_gate: Mapping[str, Any],
    portfolio_episode_coverage_handoff: Mapping[str, Any] | None,
    rendered_pdf_path: str | Path,
    renderer_name: str,
    renderer_version: str,
    render_provenance: Mapping[str, Any],
    qa_provenance: Mapping[str, Any],
    blank_page_threshold: float = 0.005,
) -> dict[str, Any]:
    render_evidence, visual_qa_evidence = build_artifact_derived_render_evidence(
        coverage_manifest=coverage_manifest,
        rendered_pdf_path=rendered_pdf_path,
        renderer_name=renderer_name,
        renderer_version=renderer_version,
        render_provenance=render_provenance,
        qa_provenance=qa_provenance,
        blank_page_threshold=blank_page_threshold,
    )
    result = build_evidence_bound_portfolio_release_gate(
        preanalysis_gate=preanalysis_gate,
        coverage_manifest=coverage_manifest,
        mp_availability=mp_availability,
        event_teacher_review_gate=event_teacher_review_gate,
        portfolio_episode_coverage_handoff=portfolio_episode_coverage_handoff,
        render_evidence=render_evidence,
        visual_qa_evidence=visual_qa_evidence,
    )
    return {
        **result,
        "schema": "tournament-v1.4-artifact-derived-portfolio-release-gate-v1",
        "artifact_derived_render_evidence_enforced": True,
        "caller_supplied_render_sha_accepted": False,
        "caller_supplied_render_size_accepted": False,
        "caller_supplied_slide_order_accepted": False,
        "caller_supplied_visual_qa_pass_accepted": False,
    }
