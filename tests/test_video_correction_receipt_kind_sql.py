"""Guard the forward receipt migration against losing the original trust checks."""
from pathlib import Path

from bridge_contracts.video_learning_feedback import _KINDS


ROOT = Path(__file__).parents[1]
OLD = (ROOT / "database/migrations/0329_workflow_video_canon_ai_promotion.sql").read_text()
FORWARD = (ROOT / "database/migrations/0400_video_correction_receipt_kind.sql").read_text()
ROLLBACK = (ROOT / "database/rollbacks/0400_video_correction_receipt_kind.sql").read_text()


def _guard(source: str) -> str:
    start = source.index("CREATE OR REPLACE FUNCTION bidding.validate_video_correction_review_receipt()")
    return source[start:source.index("END $$;", start) + len("END $$;")]


def test_new_receipt_guard_preserves_all_prior_database_trust_checks():
    old = _guard(OLD)
    expected = old.replace(
        "jsonb_object_length(NEW.receipt_payload)<>8",
        "(SELECT count(*) FROM jsonb_object_keys(NEW.receipt_payload))<>9",
    )
    expected = expected.replace("'correction_id','reviewer_ref'", "'correction_id','kind','reviewer_ref'")
    kinds = ",".join(f"'{kind}'" for kind in ("ASR", "SPEAKER", "CARD", "AUCTION", "EXTRACTION", "PEDAGOGY"))
    assert set(kinds.replace("'", "").split(",")) == _KINDS
    expected = expected.replace(
        "OR NEW.receipt_payload->>'status'<>'VERIFIED'",
        "OR COALESCE(NEW.receipt_payload->>'kind','') NOT IN\n"
        f"         ({kinds})\n"
        "       OR NEW.receipt_payload->>'status' IS DISTINCT FROM 'VERIFIED'",
    )
    assert _guard(FORWARD) == expected


def test_rollback_restores_exact_previous_guard_and_never_erases_receipts():
    assert "pg_get_functiondef(" in FORWARD
    assert "migration_0400_correction_receipt_guard_backup" in FORWARD
    assert "WHERE receipt_payload ? 'kind'" in ROLLBACK
    assert ROLLBACK.index("LOCK TABLE bidding.video_correction_review_receipt IN SHARE MODE") < ROLLBACK.index("WHERE receipt_payload ? 'kind'")
    assert "ROLLBACK_REFUSED_NEW_RECEIPTS" in ROLLBACK
    assert "EXECUTE previous_definition" in ROLLBACK
    assert "DELETE FROM bidding.video_correction_review_receipt" not in ROLLBACK
