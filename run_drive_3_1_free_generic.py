#!/usr/bin/env python3
"""Token-provider adapter for Bridge Video 3.1 FREE master analysis."""
import os


def _hardening():
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested == "3.1-free-r26.4":
        import bridge_runtime_hardening_r26_4 as hardening
    else:
        import bridge_runtime_hardening_r26 as hardening
    return hardening


def main(token_func):
    _hardening().run(token_func)
