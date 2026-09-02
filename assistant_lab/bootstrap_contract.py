"""Backward-compatible import path for the shared bootstrap contract."""

from bridge_contracts.bootstrap import BootstrapContractError, build_bootstrap_script, token_digest

__all__ = ["BootstrapContractError", "build_bootstrap_script", "token_digest"]
