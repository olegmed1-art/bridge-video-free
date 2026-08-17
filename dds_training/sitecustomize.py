from __future__ import annotations

"""Interpreter-start reliability hooks for DDS evaluation and test evidence.

Python imports ``sitecustomize`` during normal startup.  When the test runner
supplies runtime-coverage environment variables, coverage collection is enabled
before application imports.  Separately, before ``run_stage.py`` can load, the
launch guard rejects any ``evaluate --start`` invocation that was not created by
``authorized_run_stage.py`` after consuming a one-time receipt.
"""

import json
import os
import sys
from pathlib import Path

from coverage_runtime import activate_from_environment

EXIT_UNAUTHORIZED_DDS = 86


def _is_mass_evaluate(argv: list[str]) -> bool:
    if not argv:
        return False
    script = Path(argv[0]).name
    return script == "run_stage.py" and "evaluate" in argv[1:] and "--start" in argv[1:]


def _fail(detail: str) -> None:
    sys.stderr.write(f"DDS launch blocked before run_stage import: {detail}\n")
    sys.stderr.flush()
    os._exit(EXIT_UNAUTHORIZED_DDS)


def _guard() -> None:
    if not _is_mass_evaluate(sys.argv):
        return
    if os.environ.get("DDS_AUTHORIZED_LAUNCH") != "YES":
        _fail("DDS_AUTHORIZED_LAUNCH=YES was not supplied by the authorization wrapper")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        _fail("mass DDS execution is restricted to workflow_dispatch")
    receipt_id = os.environ.get("DDS_LAUNCH_RECEIPT_ID", "")
    marker_text = os.environ.get("DDS_LAUNCH_CONSUMED_MARKER", "")
    if len(receipt_id) != 64 or not marker_text:
        _fail("receipt identity or consumed marker is missing")
    marker = Path(marker_text)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read consumed receipt marker: {exc}")
    if data.get("receipt_id") != receipt_id:
        _fail("consumed marker receipt identity does not match")
    if data.get("commit_sha") != os.environ.get("GITHUB_SHA"):
        _fail("consumed marker is bound to a different commit")
    if data.get("scope") != os.environ.get("DDS_LAUNCH_SCOPE"):
        _fail("consumed marker is bound to a different scope")
    if os.environ.get("DDS_TRAINING_CONFIRM") != "YES":
        _fail("internal confirmation was not supplied by the wrapper")


# Coverage activation is a no-op unless the test runner/workflow explicitly sets
# DDS_COVERAGE_ROOT and DDS_COVERAGE_DIR.  It is intentionally independent from
# the launch authorization decision.
activate_from_environment()
_guard()
