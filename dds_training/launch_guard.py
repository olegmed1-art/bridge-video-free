from __future__ import annotations

"""Fail-closed runtime guard for mass DDS evaluation."""

import json
import os
import sys
from pathlib import Path

EXIT_UNAUTHORIZED_DDS = 86


def is_mass_evaluate(argv: list[str] | None = None) -> bool:
    values = sys.argv if argv is None else argv
    if not values:
        return False
    return Path(values[0]).name == "run_stage.py" and "evaluate" in values[1:] and "--start" in values[1:]


def _fail(detail: str) -> None:
    sys.stderr.write(f"DDS launch blocked before run_stage import: {detail}\n")
    sys.stderr.flush()
    os._exit(EXIT_UNAUTHORIZED_DDS)


def enforce_mass_evaluate_guard(argv: list[str] | None = None) -> None:
    if not is_mass_evaluate(argv):
        return
    if os.environ.get("DDS_AUTHORIZED_LAUNCH") != "YES":
        _fail("DDS_AUTHORIZED_LAUNCH=YES was not supplied by the authorization wrapper")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        _fail("mass DDS execution is restricted to workflow_dispatch")

    receipt_id = os.environ.get("DDS_LAUNCH_RECEIPT_ID", "")
    marker_text = os.environ.get("DDS_LAUNCH_CONSUMED_MARKER", "")
    scope = os.environ.get("DDS_LAUNCH_SCOPE", "")
    commit_sha = os.environ.get("GITHUB_SHA", "")
    if len(receipt_id) != 64 or not marker_text or not scope or not commit_sha:
        _fail("receipt identity, consumed marker, scope or commit identity is missing")

    marker = Path(marker_text)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read consumed receipt marker: {exc}")
    if data.get("receipt_id") != receipt_id:
        _fail("consumed marker receipt identity does not match")
    if data.get("commit_sha") != commit_sha:
        _fail("consumed marker is bound to a different commit")
    if data.get("scope") != scope:
        _fail("consumed marker is bound to a different scope")
    if os.environ.get("DDS_TRAINING_CONFIRM") != "YES":
        _fail("internal confirmation was not supplied by the authorization wrapper")
