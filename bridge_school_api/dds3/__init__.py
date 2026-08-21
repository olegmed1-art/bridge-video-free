"""Bridge School DDS3 public API."""
from .service import DDS3Config, DDSUnavailable, solve_table

__all__ = ["DDS3Config", "DDSUnavailable", "solve_table"]
