from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw

import run_master_3_1_free as base
from bridge_contracts.video_deal import SEATS
from bridge_vision.deal_review_pdf import (
    DealReviewPdfError,
    build_deal_review_views,
)


def _frame(path: Path, text: str) -> dict:
    image = Image.new("RGB", (1280, 720), "#DDE5F2")
    ImageDraw.Draw(image).text((80, 340), text, fill="#172033")
    image.save(path, format="JPEG", quality=92)
    return {
        "evidence_id": path.stem,
        "time": 201.0,
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": "video_frame",
    }


def _master(deals: list[dict]) -> dict:
    return {
        "algorithmVersion": "3.1 FREE",
        "algorithmRevision": "3.1-free-r25.16",
        "source": {
            "name": "verified-lesson.mp4",
            "durationSeconds": 7200,
            "sizeBytes": 1,
            "driveId": "synthetic",
            "sha256": "0" * 64,
        },
        "session_summary": {"episode_count": 0, "topics": []},
        "warnings": [],
        "timeline": [],
        "episodes": [],
        "learning_interactions": [],
        "student_analysis": {"observations": []},
        "errors": [],
        "strengths": [],
        "teacher_analysis": [],
        "best_explanations": [],
        "canon_links": [],
        "knowledge_gaps": [],
        "recommendations": [],
        "deals": deals,
        "decisions": [],
        "transcript": [],
        "technical_qc": {"transcript": {"primarySource": "fixture", "qc": []}},
        "content_quality": {
            "speaker_labels_present": False,
            "actor_attribution_status": "unavailable_without_speaker_labels",
            "r24Gate": {"ok": True, "issues": [], "unreliableDerivedEvidenceCount": 0},
        },
    }


def _complete_suit(suit: str) -> list[str]:
    return [rank + suit for rank in "AKQJT98765432"]


def test_stable_master_pdf_appends_verified_layout_and_exact_reconstruction(tmp_path: Path):
    first = _frame(tmp_path / "frame-one.jpg", "HUMAN VERIFIED SOUTH")
    second = _frame(tmp_path / "frame-two.jpg", "EXACT 39 TO 13")
    deals = [
        {
            "deal_id": "board-1",
            "board_number": 1,
            "status": "reviewed",
            "hands": {"E": _complete_suit("C")},
            "verification": {
                "status": "HUMAN_VERIFIED",
                "verified_seats": ["E"],
                "method": "director visual review",
                "reviewer": "school_director",
                "verified_at": "2026-08-29T16:15:00Z",
                "reference_frame_sha256": first["sha256"],
            },
            "dealer": "N",
            "auction": {
                "status": "COMPLETE_CONFIRMED",
                "dealer": "N",
                "calls": ["1H", "PASS", "2H", "PASS", "PASS", "PASS"],
            },
            "evidence": [first["evidence_id"]],
        },
        {
            "deal_id": "board-2",
            "board_number": 2,
            "status": "review",
            "hands": {
                "N": _complete_suit("S"),
                "E": _complete_suit("H"),
                "S": _complete_suit("D"),
            },
            "auction": None,
            "evidence": [second["evidence_id"]],
        },
    ]
    master = _master(deals)
    pdf = tmp_path / "master-3.1-free.pdf"

    report = base.pdf_report(pdf, master, [first, second])
    assert report == {
        "schema": "bridge-3.1-free-deal-review-pdf/v1",
        "pages": 2,
        "deals": 2,
        "screenshots_embedded": 2,
        "canon_promotion_performed": False,
        "reconstruction_rule": "deck_subtraction_from_three_complete_hands_only",
    }
    master["content_quality"]["deal_review_pdf"] = report
    base.embed_master(pdf, master)
    qc = base.pdfqc(pdf, expected_deal_review_pages=2)
    assert qc["ok"], qc

    document = fitz.open(pdf)
    try:
        review_pages = list(document)[-2:]
        assert all(page.rect.width > page.rect.height for page in review_pages)
        first_text = review_pages[0].get_text()
        second_text = review_pages[1].get_text()
        assert "E - HUMAN_VERIFIED" in first_text
        assert "NOT_DERIVED_INSUFFICIENT_OBSERVATIONS" in first_text
        assert "1H" in first_text and "Pass" in first_text
        assert "W - DERIVED" in second_text
        assert "DERIVED_39_TO_13" in second_text
        assert "EVIDENCE REVIEW" in first_text
        attachment = document.embfile_get("master_analysis.json")
        assert b'"canon_promotion_performed": false' in attachment
    finally:
        document.close()


def test_deal_review_fails_closed_on_cross_seat_card_conflict():
    master = _master([
        {
            "deal_id": "conflict",
            "hands": {"N": ["AS"], "S": ["AS"]},
            "auction": None,
            "evidence": [],
        }
    ])
    with pytest.raises(DealReviewPdfError, match="canonical 52-card contract"):
        build_deal_review_views(master, [])


def test_human_verified_label_requires_matching_hash_bound_frame(tmp_path: Path):
    shot = _frame(tmp_path / "frame.jpg", "REFERENCE")
    master = _master([
        {
            "deal_id": "unbound-review",
            "hands": {"S": ["AS"]},
            "verification": {
                "status": "HUMAN_VERIFIED",
                "verified_seats": ["S"],
                "method": "director visual review",
                "reviewer": "school_director",
                "verified_at": "2026-08-29T16:15:00Z",
                "reference_frame_sha256": "f" * 64,
            },
            "auction": None,
            "evidence": [shot["evidence_id"]],
        }
    ])
    with pytest.raises(DealReviewPdfError, match="hash-bound screenshot"):
        build_deal_review_views(master, [shot])


@pytest.mark.parametrize("verified_seat", SEATS)
def test_human_verification_is_seat_agnostic(tmp_path: Path, verified_seat: str):
    shot = _frame(tmp_path / f"frame-{verified_seat}.jpg", f"VERIFIED {verified_seat}")
    cards = {"N": "AS", "E": "AH", "S": "AD", "W": "AC"}
    master = _master([
        {
            "deal_id": f"verified-{verified_seat}",
            "hands": {verified_seat: [cards[verified_seat]]},
            "verification": {
                "status": "HUMAN_VERIFIED",
                "verified_seats": [verified_seat],
                "method": "independent visual review",
                "reviewer": "bridge_expert",
                "verified_at": "2026-08-29T16:15:00Z",
                "reference_frame_sha256": shot["sha256"],
            },
            "auction": None,
            "evidence": [shot["evidence_id"]],
        }
    ])
    views = build_deal_review_views(master, [shot])
    assert views[0]["verified_seats"] == {verified_seat}
    assert views[0]["observed_count"] == 1


def test_no_deals_preserves_existing_portrait_report(tmp_path: Path):
    master = _master([])
    pdf = tmp_path / "no-deals.pdf"
    report = base.pdf_report(pdf, master, [])
    assert report["pages"] == 0
    document = fitz.open(pdf)
    try:
        assert all(page.rect.height > page.rect.width for page in document)
    finally:
        document.close()
