#!/usr/bin/env python3
"""Token-provider adapter for bounded Bridge Video r26.6 validation."""
import bridge_runtime_hardening_r26_6 as hardening


def main(token_func):
    return hardening.run(token_func)

