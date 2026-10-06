"""Job-bound opt-in comparison requirement; no compute or external writes."""
from __future__ import annotations

from pathlib import Path


def comparison_required(metadata) -> bool:
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("comparison metadata must be an object")
    return receipt_requirement(metadata)


def receipt_requirement(receipt) -> bool:
    value = receipt.get("comparison_required", False)
    if type(value) is not bool:
        raise ValueError("comparison_required must be a boolean")
    return value


def require_comparison_package(result_dir: Path, *, expected_required: bool) -> None:
    """Use the intake/job requirement, never a result-only opt-in.

    Reuse the strict complete typed validator, including source/job/version,
    all original PNG bytes and index/part hashes. A stripped manifest flag
    cannot downgrade a required intake. Old unflagged jobs remain optional.
    """
    if type(expected_required) is not bool:
        raise ValueError("expected comparison requirement must be a boolean")
    from .comparison_artifacts import collect_comparison_paths
    from .result_conformance import _read_json
    result_dir = Path(result_dir)
    manifest = _read_json(result_dir / "manifest.json", max_bytes=5 * 1024**2)
    if not isinstance(manifest, dict):
        raise RuntimeError("comparison parent manifest must be an object")
    if comparison_required(manifest.get("metadata")) != expected_required:
        raise RuntimeError("comparison requirement differs from intake")
    if expected_required and not collect_comparison_paths(result_dir, manifest):
        raise RuntimeError("required comparison package missing")
