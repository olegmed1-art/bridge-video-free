"""Explicit review-only v2 wrapper. No production entry point selects it.

Only an exact requested candidate revision permits installation. There is no
run() entry point and no call to historical r26/r264/r265 installers. Golden
fixtures, holdout, independent review and an authorized canary remain required.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_16 as previous
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
from bridge_output_scoped_idempotency import existing_same_revision_done
from bridge_vision import bridgit_primary_production_candidate as primary

REVISION = "3.1-free-r26.3-auction-candidate3"
PRODUCTION_ALLOWED = False
_INSTALL_STATE = "NOT_INSTALLED"
_TOKEN_FUNC = None


def install(token_func):
    global _INSTALL_STATE, _TOKEN_FUNC
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested != REVISION:
        raise RuntimeError("candidate requires its exact requested revision")
    if _INSTALL_STATE == "INSTALLED":
        if token_func is not _TOKEN_FUNC:
            raise RuntimeError("candidate token provider cannot change after installation")
        primary.install(base, token_func)  # Verify the same hooks are still installed.
        return
    if _INSTALL_STATE != "NOT_INSTALLED":
        raise RuntimeError("failed or concurrent installation requires a fresh process")
    primary.ensure_installable(base)
    _INSTALL_STATE = "INSTALLING"
    try:
        os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = previous.REVISION
        try:
            previous.install(token_func)
        finally:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = requested
        import run_master_3_1_free_semantic_v2 as semantic_v2
        semantic_v2.REVISION = REVISION
        semantic_v2._existing_same_revision_done = lambda token, job: existing_same_revision_done(semantic_v2, token, job, REVISION)
        semantic_v2.previous._existing_same_revision_done = lambda token, job: existing_same_revision_done(semantic_v2.previous, token, job, REVISION)
        core.ALGORITHM_REVISION = REVISION
        base.ALGORITHM_REVISION = REVISION
        primary.install(base, token_func)
    except Exception:
        _INSTALL_STATE = "FAILED"
        raise
    _TOKEN_FUNC = token_func
    _INSTALL_STATE = "INSTALLED"


__all__ = ["REVISION", "PRODUCTION_ALLOWED", "install"]
