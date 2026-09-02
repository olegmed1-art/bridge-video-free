import hashlib
import json

import fitz
import pytest

from bridge_school_api.tournament_render_artifact_builder_v3 import (
    TournamentRenderArtifactBuilderError,
    build_artifact_derived_render_evidence,
)


def _coverage(keys=None):
    return {
        "schema": "tournament-coverage-manifest-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": "bridge.co.il:event:30041:round:2",
        "episode_inventory_complete": True,
        "coverage_plan_release_ready": True,
        "release_blockers": [],
        "expected_slide_keys": keys or ["deck-title", "board-2-base", "deck-final"],
    }


def _coverage_sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _make_pdf(path, pages=3, blank_page=None, marker="A"):
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=640, height=360)
        if blank_page != index:
            page.draw_rect(fitz.Rect(20, 20, 620, 340), fill=(0.85, 0.85, 0.85))
            page.insert_text((60, 80), f"{marker}-{index}", fontsize=24)
    doc.save(path)
    doc.close()


def _build(coverage, path):
    return build_artifact_derived_render_evidence(
        coverage_manifest=coverage,
        rendered_pdf_path=path,
        renderer_name="LibreOffice",
        renderer_version="test",
        render_provenance={"run_id": "render-run"},
        qa_provenance={"run_id": "qa-run"},
    )


def test_derives_hash_size_order_and_qa_from_real_pdf(tmp_path):
    pdf = tmp_path / "deck.pdf"
    _make_pdf(pdf)
    coverage = _coverage()

    render, qa = _build(coverage, pdf)

    expected_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert render["artifact"]["sha256"] == expected_sha
    assert render["artifact"]["size_bytes"] == pdf.stat().st_size
    assert render["slide_keys"] == coverage["expected_slide_keys"]
    assert render["coverage_manifest_sha256"] == _coverage_sha(coverage)
    assert render["artifact_values_derived_from_bytes"] is True
    assert render["slide_order_derived_from_exact_coverage"] is True
    assert qa["render_artifact_sha256"] == expected_sha
    assert qa["checked_slide_count"] == 3
    assert qa["pass"] is True
    assert qa["qa_values_derived_from_rendered_bytes"] is True


def test_rejects_blank_page_in_real_pdf(tmp_path):
    pdf = tmp_path / "blank.pdf"
    _make_pdf(pdf, blank_page=1)

    with pytest.raises(TournamentRenderArtifactBuilderError, match="blank pages"):
        _build(_coverage(), pdf)


def test_rejects_page_count_mismatch(tmp_path):
    pdf = tmp_path / "two-pages.pdf"
    _make_pdf(pdf, pages=2)

    with pytest.raises(TournamentRenderArtifactBuilderError, match="page count"):
        _build(_coverage(), pdf)


def test_render_hash_changes_with_actual_bytes(tmp_path):
    coverage = _coverage()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    _make_pdf(first, marker="FIRST")
    _make_pdf(second, marker="SECOND")

    first_render, _ = _build(coverage, first)
    second_render, _ = _build(coverage, second)

    assert first_render["artifact"]["sha256"] != second_render["artifact"]["sha256"]


def test_rejects_non_pdf_even_if_extension_matches(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not a pdf")

    with pytest.raises(TournamentRenderArtifactBuilderError, match="not a PDF"):
        _build(_coverage(), fake)
