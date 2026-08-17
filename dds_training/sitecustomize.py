from __future__ import annotations

"""Interpreter-start reliability hooks for DDS evaluation and test evidence."""

from coverage_runtime import activate_from_environment
from launch_guard import EXIT_UNAUTHORIZED_DDS, enforce_mass_evaluate_guard

# Coverage activation is a no-op unless the test runner/workflow explicitly sets
# DDS_COVERAGE_ROOT and DDS_COVERAGE_DIR. It is independent from launch authorization.
activate_from_environment()
enforce_mass_evaluate_guard()

__all__ = ["EXIT_UNAUTHORIZED_DDS", "enforce_mass_evaluate_guard"]
