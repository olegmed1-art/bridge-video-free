#!/usr/bin/env python3
"""Token-provider adapter for Bridge Video 3.1 FREE master analysis."""
import bridge_runtime_hardening_r25_4 as hardening


def main(token_func):
    hardening.run(token_func)
