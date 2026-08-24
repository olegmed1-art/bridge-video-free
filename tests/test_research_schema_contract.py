from pathlib import Path

SQL = Path("assistant_lab/research_schema.sql").read_text(encoding="utf-8")


def test_research_schema_is_non_canonical_and_idempotent():
    assert "assistant_lab.research_job" in SQL
    assert "canonical_promotion boolean NOT NULL DEFAULT false" in SQL
    assert "UNIQUE" in SQL
    assert "ON CONFLICT (research_key)" in SQL
    assert "assistant_lab.enqueue_research_job" in SQL


def test_research_schema_enables_only_bounded_compute_kinds():
    assert "'DDS3_COMPUTE', 'BEN_COMPUTE', 'NOOP'" in SQL
    assert "only executable DDS3/BEN research jobs" in SQL
    for forbidden in ("curriculum", "student_profile", "school_canon"):
        assert forbidden not in SQL.lower()
