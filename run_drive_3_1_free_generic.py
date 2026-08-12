#!/usr/bin/env python3
"""Token-provider adapter for Bridge Video 3.1 FREE master analysis."""
import run_master_3_1_free_semantic as master


def main(token_func):
    master.process_job(token_func())
