#!/usr/bin/env python3
"""Token-provider adapter for bounded Bridge Video r26.4 validation."""
import bridge_runtime_hardening_r26_4 as hardening


def main(token_func):
    hardening.run(token_func)
