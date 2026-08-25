from __future__ import annotations

import math
from pathlib import Path

import pytest

from ops.oracle_ben_benchmark import (
    deterministic_cases,
    parse_bytes,
    percentile,
    verify_ben_payload,
)


def test_percentile_and_memory_parsing_are_deterministic():
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.50) == 3.0
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.95) == 4.0
    assert parse_bytes("1.5GiB") == int(1.5 * 1024**3)
    assert parse_bytes("256MiB") == 256 * 1024**2


def test_fixed_cases_are_real_13_card_hands():
    cases = deterministic_cases()
    assert len(cases) == 5
    for case in cases:
        assert sum(len(suit) for suit in case["hand"].split(".")) == 13
        assert case["seat"] in "NESW"
        assert case["dealer"] in "NESW"


def test_ben_quality_requires_selected_finite_policy_score():
    assert verify_ben_payload({"bid": "1S", "candidates": [{"call": "1S", "insta_score": 1.5}]}) == "1S"
    with pytest.raises(ValueError, match="finite"):
        verify_ben_payload({"bid": "1S", "candidates": [{"call": "1S", "insta_score": math.nan}]})
    with pytest.raises(ValueError, match="selected"):
        verify_ben_payload({"bid": "1S", "candidates": [{"call": "1H", "insta_score": 1.5}]})


def test_github_operator_exposes_only_the_exact_bounded_benchmark():
    workflow = Path(".github/workflows/oracle-operator-v2.yml").read_text(encoding="utf-8")
    assert workflow.count("/oracle-v2 benchmark-ben-100-500") >= 3
    assert "ops/oracle_ben_benchmark.py" in workflow
    assert 'BEN_P95_LIMIT_MS=5000' in workflow
    assert "ben_benchmark=${BEN_BENCHMARK_OUTCOME}" in workflow


def test_benchmark_emits_fail_closed_diagnostics_for_preflight_failures():
    source = Path("ops/oracle_ben_benchmark.py").read_text(encoding="utf-8")
    assert '"bridge-ben-healthcheck.timer"' in source
    assert '"dds3-healthcheck.timer"' in source
    assert 'report["error"]' in source
    assert 'type(exc).__name__' in source
