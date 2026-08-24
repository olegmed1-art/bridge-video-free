"""Audit external dependencies in bridge-analysis decision paths.

Every non-stdlib, non-repository import used by critical analysis modules must be
registered explicitly as infrastructure, model, oracle, or legacy. This prevents
a new opaque recognizer/heuristic from silently becoming part of the canonical
pipeline.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "bridge_vision" / "decision_dependencies.json"
CRITICAL_PATHS = (
    ROOT / "bridge_vision",
    ROOT / "universal_video",
    ROOT / "tools" / "bridge_video_positions.py",
)

ALLOWED_KINDS = {"infrastructure", "model", "oracle", "legacy"}


class DependencyAuditError(RuntimeError):
    pass


def _repo_roots() -> set[str]:
    roots: set[str] = set()
    for child in ROOT.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            roots.add(child.name)
        elif child.is_file() and child.suffix == ".py":
            roots.add(child.stem)
    return roots


def _python_files(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts))
        elif path.is_file():
            out.append(path)
    return out


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def load_registry() -> dict[str, dict[str, str]]:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    deps = raw.get("dependencies")
    if not isinstance(deps, dict):
        raise DependencyAuditError("decision dependency registry must contain dependencies object")
    for name, entry in deps.items():
        if not isinstance(entry, dict) or entry.get("kind") not in ALLOWED_KINDS:
            raise DependencyAuditError(f"invalid dependency registry entry: {name}")
        if not str(entry.get("policy") or "").strip():
            raise DependencyAuditError(f"dependency {name} is missing policy")
    return deps


def audit() -> dict[str, object]:
    registry = load_registry()
    repo_roots = _repo_roots()
    stdlib = set(sys.stdlib_module_names)
    external: dict[str, list[str]] = {}
    for path in _python_files(CRITICAL_PATHS):
        for root in sorted(_imports(path)):
            if root in stdlib or root in repo_roots:
                continue
            external.setdefault(root, []).append(str(path.relative_to(ROOT)))

    unregistered = sorted(set(external) - set(registry))
    if unregistered:
        details = "; ".join(f"{name}: {', '.join(external[name])}" for name in unregistered)
        raise DependencyAuditError("unregistered external decision dependency: " + details)

    return {
        "status": "PASS",
        "registered": sorted(registry),
        "observed_external": {name: sorted(paths) for name, paths in sorted(external.items())},
    }


def main() -> None:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
