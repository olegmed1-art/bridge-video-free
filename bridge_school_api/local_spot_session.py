"""Bind the pinned offline TeacherSessionStore to one local preview session.

The browser never supplies a journal path, source checkout, or session ID.
This module neither creates a session nor writes to production storage.
"""

from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import sys
import types


STORE_SHA256 = "884f9a77b50310383107939edc9c7f516d699f10170e9156102357ca6888cc2b"
PACKAGE = "_local_spot_candidate"


class LocalSpotSessionUnavailable(RuntimeError):
    pass


def load_store_class():
    source = Path(__file__).resolve().parents[1]
    package_dir = source / "evolutionary_course"
    store_path = package_dir / "teacher_session_store.py"
    if not store_path.is_file() or hashlib.sha256(store_path.read_bytes()).hexdigest() != STORE_SHA256:
        raise LocalSpotSessionUnavailable("PINNED_TEACHER_STORE_UNAVAILABLE")
    existing = sys.modules.get(PACKAGE)
    if existing is not None and list(existing.__path__) != [str(package_dir)]:
        raise LocalSpotSessionUnavailable("TEACHER_SOURCE_CHANGED")
    if existing is None:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(package_dir)]
        sys.modules[PACKAGE] = package
    return importlib.import_module(f"{PACKAGE}.teacher_session_store").TeacherSessionStore


def bound_store():
    journal = os.environ.get("BRIDGE_LOCAL_TEACHER_JOURNAL", "")
    session_id = os.environ.get("BRIDGE_LOCAL_TEACHER_SESSION_ID", "")
    if not journal or not session_id or not Path(journal).is_file():
        raise LocalSpotSessionUnavailable("BOUND_LOCAL_SESSION_UNAVAILABLE")
    return load_store_class()(journal, session_id)


def apply_operation_to_store(store, operation: str, value: dict):
    if operation == "resume" and value == {}:
        from .local_spot_history import resume_with_history
        return resume_with_history(store)
    if operation == "execute" and set(value) == {"command", "expected_revision"}:
        from .spot_review_answer import validate_new_answer
        validate_new_answer(store, value["command"])
        return store.execute(value["command"], expected_revision=value["expected_revision"])
    if operation == "acknowledge" and set(value) == {"delivery_id", "result_sha256"}:
        return store.acknowledge(value["delivery_id"], value["result_sha256"])
    raise ValueError("UNKNOWN_LOCAL_SESSION_OPERATION")


def apply_operation(operation: str, value: dict):
    return apply_operation_to_store(bound_store(), operation, value)
