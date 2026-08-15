#!/usr/bin/env python3
"""Bridge Video 3.1 FREE internal revision r25.4.1.

This revision preserves all validated r25.4 analysis behavior and hardens only
revision provenance/routing. A queued request must name the same internal
algorithm revision that actually executes; a mismatch fails before processing.
The public product name remains exactly ``3.1 FREE``.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_4 as r25_4
import bridge_worker_3_1_free as core
import run_master_3_1_free as base

REVISION = "3.1-free-r25.4.1"


def validate_requested_revision(requested: str | None = None) -> str:
    value = (requested if requested is not None else os.getenv(
        "BRIDGE_REQUESTED_ALGORITHM_REVISION", ""
    )).strip()
    if value and value != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={value} executing={REVISION}"
        )
    return value


def install(token_func):
    """Install proven r25.4 behavior without allowing it to rewrite provenance."""
    validate_requested_revision()

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


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic as semantic
    return semantic.process_job(token_func())
