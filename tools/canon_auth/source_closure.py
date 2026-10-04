"""Conservative transitive repository source inventory; hashes are NOT review.

Includes imports inside functions and static __import__. Dynamic import sites and
external modules remain explicit review obligations. No code execution/imports of
the inspected modules, no configuration/credential lookup and no source export.
"""
import ast
from hashlib import sha256
import json
from pathlib import Path
import subprocess

ROOTS = ("tools.canon_auth.owner_stage", "tools.canon_auth.bounded_controller",
         "tools.canon_auth.github_channels", "tools.canon_auth.teacher_behavior")
RESOURCES = ("tools/tournament_pilot/package.json", "pyproject.toml", "vercel.json",
             ".github/workflows/native-maintenance-owner-attest.yml")


def inventory(root, roots=ROOTS, resources=RESOURCES):
    root = Path(root).resolve()
    modules, external, dynamic = {}, set(), []
    pending = []
    def local(name):
        path = root.joinpath(*name.split(".")).with_suffix(".py")
        if path.is_file():
            return path
        package = root.joinpath(*name.split("."), "__init__.py")
        return package if package.is_file() else None
    def enqueue(name):
        if not name:
            return
        if local(name):
            pending.append(name)
        else:
            external.add(name.split(".")[0])
        # Package initializers execute before modules. Include every present one.
        parts = name.split(".")
        for size in range(1, len(parts)):
            package = ".".join(parts[:size])
            path = root.joinpath(*parts[:size], "__init__.py")
            if path.is_file() and package not in modules:
                pending.append(package)
    for name in roots:
        enqueue(name)
    while pending:
        name = pending.pop()
        if name in modules:
            continue
        path = local(name)
        if path is None:
            external.add(name.split(".")[0])
            continue
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        tree = ast.parse(raw, filename=relative)
        modules[name] = {"path": relative, "sha256": sha256(raw).hexdigest()}
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    enqueue(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split(".")
                    if node.level > len(parts):
                        raise ValueError("relative_import_escape")
                    base = ".".join(parts[:len(parts)-node.level+1])
                    imported = base + ("." + node.module if node.module else "")
                else:
                    imported = node.module or ""
                enqueue(imported)
                for alias in node.names:
                    if alias.name != "*" and local(imported + "." + alias.name):
                        enqueue(imported + "." + alias.name)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        enqueue(node.args[0].value)
                    else:
                        dynamic.append({"path": relative, "line": node.lineno, "kind": "__import__"})
                if isinstance(node.func, ast.Attribute) and node.func.attr in ("import_module", "exec_module"):
                    dynamic.append({"path": relative, "line": node.lineno, "kind": node.func.attr})
    files = {value["path"]: value["sha256"] for value in modules.values()}
    for relative in resources:
        files[relative] = sha256((root / relative).read_bytes()).hexdigest()
    # SQL functions/triggers can execute dependencies outside Python imports.
    for path in sorted((root / "database/migrations").rglob("*.sql")):
        files[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    # Exact closure identity, not approval or evidence of a live installation.
    return {"status": "SOURCE_REVIEW_REQUIRED", "files": dict(sorted(files.items())),
            "external_import_roots": sorted(external), "dynamic_import_sites": dynamic,
            "source_review_complete": False, "third_party_review_complete": False,
            "schema_runtime_dependency_review_required": True}


def main():
    root = Path(__file__).resolve().parents[2]
    report = inventory(root)
    report["checkout_sha"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    report["checkout_tree"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    print(json.dumps(report, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
