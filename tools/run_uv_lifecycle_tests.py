#!/usr/bin/env python3
"""Stdlib-only synthetic tests. Does not implement or simulate pytest.

Run from this isolated work root: python tools/run_uv_lifecycle_tests.py
No inherited configuration, network, subprocesses, credentials, media or models.
"""
from __future__ import annotations
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.clear()
os.environ.update(PATH="/usr/bin:/bin", UNIVERSAL_VIDEO_QUEUE_BACKEND="spool_only")


def forbid(*args, **kwargs):
    raise AssertionError("External transport/process forbidden in synthetic fixture tests")


def audit(event, args):
    if event.startswith("socket.") or event == "subprocess.Popen" or event == "os.system":
        forbid()

sys.addaudithook(audit)
# requests is absent here. This explicit transport double only permits import;
# every use fails. Production drive code and imports remain unchanged.
transport = types.ModuleType("requests")
for name in ("get", "post", "put", "delete", "request", "Session"):
    setattr(transport, name, forbid)
sys.modules["requests"] = transport
sys._uv_synthetic_guards_active = True

import unittest
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_uv_lifecycle_unittest.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
