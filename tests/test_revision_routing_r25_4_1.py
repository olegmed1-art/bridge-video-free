#!/usr/bin/env python3
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_4_1 as candidate
import bridge_worker_3_1_free as core
import run_master_3_1_free as base


def test_exact_revision_required():
    assert candidate.validate_requested_revision(candidate.REVISION) == candidate.REVISION
    try:
        candidate.validate_requested_revision("3.1-free-r25.5")
    except RuntimeError as exc:
        assert "ALGORITHM_REVISION_MISMATCH" in str(exc)
    else:
        raise AssertionError("mismatched queued revision was accepted")


def test_install_preserves_candidate_revision(monkeypatch=None):
    original = candidate.r25_4.install
    seen = {}

    def fake_install(_token_func):
        seen["requested_inside"] = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION")
        core.ALGORITHM_REVISION = candidate.r25_4.REVISION
        base.ALGORITHM_REVISION = candidate.r25_4.REVISION

    candidate.r25_4.install = fake_install
    previous = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = candidate.REVISION
    try:
        candidate.install(lambda: "token")
        assert seen["requested_inside"] == candidate.r25_4.REVISION
        assert core.ALGORITHM_REVISION == candidate.REVISION
        assert base.ALGORITHM_REVISION == candidate.REVISION
        assert os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] == candidate.REVISION
    finally:
        candidate.r25_4.install = original
        if previous is None:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
        else:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = previous


if __name__ == "__main__":
    test_exact_revision_required()
    test_install_preserves_candidate_revision()
    print("R25_4_1_REVISION_ROUTING: PASS")
