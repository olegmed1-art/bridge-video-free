"""Default-off handoff for existing r3 captures; no recognition or scoring launch."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
from types import ModuleType

SCORER_SHA256 = "2343d67f7f5b2e2631f4c6bf8d6845187113b68b9da54147eb6ffa204fb9db4b"
MAX_FILES = 4096
MAX_BYTES = 256 * 1024**2

def require(ok, message):
    if not ok:
        raise ValueError(message)

def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()

def digest(data):
    return hashlib.sha256(data).hexdigest()

def scorer_at(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "regular reviewed scorer required")
    verified_bytes = path.read_bytes()
    require(digest(verified_bytes) == SCORER_SHA256, "reviewed scorer pin mismatch")
    # Execute exactly the authenticated snapshot: no loader, path reread or pyc.
    code = compile(verified_bytes, str(path), "exec", dont_inherit=True, optimize=0)
    module = ModuleType("reviewed_offline_score_r2")
    module.__file__ = str(path)
    module.__package__ = ""
    exec(code, module.__dict__)
    return module

def inventory(root):
    root = Path(root)
    require(not root.is_symlink(), "capture root symlink")
    root = root.resolve(strict=True)
    require(root.is_dir(), "existing finalized capture required")
    rows, total = [], 0
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        require(not path.is_symlink() and (stat.S_ISDIR(mode) or stat.S_ISREG(mode)),
                "capture entries must be regular; no symlinks")
        if stat.S_ISDIR(mode):
            continue
        name = path.relative_to(root).as_posix()
        require("\\" not in name and ":" not in name and
                all(p not in {"", ".", ".."} for p in name.split("/")), "unsafe capture path")
        before = path.stat()
        h, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                require(total + size <= MAX_BYTES, "capture quota")
                h.update(block)
        after = path.stat()
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and size == before.st_size, "capture changed while hashing")
        total += size
        rows.append({"path": name, "bytes": size, "sha256": h.hexdigest()})
        require(len(rows) <= MAX_FILES, "capture file quota")
    require(rows, "empty capture")
    return rows

def inventory_sha256(rows):
    return digest(encoded(rows))

def outside(root, path):
    path = Path(path)
    require(not path.is_symlink(), "control path symlink")
    path = path.resolve()
    require(not path.is_relative_to(root), "control path inside capture")
    return path

def write_new(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())

def freeze_capture(*, enabled=False, scorer, comparison, state_dir,
                   gold, gold_sha256, index, index_sha256, freeze, freeze_sha256,
                   review, review_sha256):
    # No filesystem access, inventory or subprocess launch while default-off.
    require(enabled is True, "handoff disabled; explicit enable required")
    root = Path(comparison).resolve(strict=True)
    require(not Path(comparison).is_symlink(), "capture root symlink")
    state = outside(root, state_dir)
    require(not state.exists() and state.parent.is_dir(), "new external control directory required")
    paths = {name: outside(root, path) for name, path in
             (("gold", gold), ("index", index), ("freeze", freeze), ("review", review))}
    pins = {"gold": gold_sha256, "index": index_sha256,
            "freeze": freeze_sha256, "review": review_sha256}
    inputs = {name: scorer.sealed_json(path, pins[name]) for name, path in paths.items()}
    g, pts, gf, approval = (inputs[n] for n in ("gold", "index", "freeze", "review"))
    require(scorer.frozen(gold_sha256, index_sha256, gf),
            "independent pre-output gold freeze required")
    require(g.get("schema") == scorer.TAXONOMY and
            pts.get("schema") == "bridge-source-pts-index/v1" and
            pts.get("independently_verified") is True, "independent gold/PTS schema")
    require(scorer.hex64(g.get("source_sha256")) and scorer.hex64(g.get("clip_sha256"))
            and pts.get("source_sha256") == g["source_sha256"]
            and pts.get("clip_sha256") == g["clip_sha256"]
            and pts.get("time_base") == g.get("time_base"), "independent source/clip/PTS binding")
    # Existing scorer checks half-open gold intervals and independently decoded PTS.
    # It never derives PTS or labels from recognition predictions.
    scorer.Alignment(pts, g)
    rows = inventory(root)
    require(approval.get("schema") == "bridge-independent-capture-review/v1"
            and approval.get("independent_review") is True
            and approval.get("outputs_finalized") is True
            and approval.get("scoring_not_started") is True
            and isinstance(approval.get("reviewer"), str) and approval["reviewer"].strip(),
            "external independent capture review required")
    expected = {"comparison_inventory_sha256": inventory_sha256(rows),
                "gold_sha256": gold_sha256, "pts_index_sha256": index_sha256,
                "gold_freeze_sha256": freeze_sha256, "clip_sha256": g["clip_sha256"]}
    require(all(approval.get(k) == v for k, v in expected.items()), "capture review binding mismatch")
    now = datetime.now(timezone.utc)
    reviewed = datetime.fromisoformat(approval["reviewed_utc"].replace("Z", "+00:00"))
    require(reviewed.utcoffset() is not None and reviewed <= now, "capture review time invalid")
    seal_row = next((r for r in rows if r["path"] == "seal.json"), None)
    require(seal_row is not None, "existing runner seal required")
    seal = scorer.sealed_json(root / "seal.json", seal_row["sha256"])
    sealed_time = seal.get("sealed_at_unix")
    require(type(sealed_time) in {int, float} and math.isfinite(sealed_time)
            and reviewed.timestamp() >= sealed_time, "capture review predates runner seal")
    manifest = {"schema": "bridge-independent-capture-freeze/v1",
                "independent_review": True, "outputs_finalized": True,
                "frozen_before_scoring": True, "reviewer": approval["reviewer"],
                "frozen_utc": now.isoformat(), "gold_sha256": gold_sha256,
                "pts_index_sha256": index_sha256, "clip_sha256": g["clip_sha256"],
                "capture_review_sha256": review_sha256, "files": rows}
    require(inventory(root) == rows, "capture changed before freeze")
    require(all(digest(path.read_bytes()) == pins[name] for name, path in paths.items()),
            "independent inputs changed before freeze")
    state.mkdir(mode=0o700)
    capture = state / "capture.json"
    capture_bytes = encoded(manifest)
    capture_hash = digest(capture_bytes)
    write_new(capture, capture_bytes)
    # Validate existing complete r3 comparison/evidence with the unchanged scorer.
    # On failure, retain the partial control directory for diagnosis; issue no handoff.
    scorer.load_comparison(root, gold_sha256, g["clip_sha256"], gf,
                           capture_path=capture, capture_hash=capture_hash,
                           index_hash=index_sha256)
    require(inventory(root) == rows, "capture changed during validation")
    require(all(digest(path.read_bytes()) == pins[name] for name, path in paths.items()),
            "independent inputs changed during validation")
    write_new(state / "capture.sha256", (capture_hash + "\n").encode())
    receipt = {"schema": "bridge-offline-score-handoff/v1", "status": "FROZEN_UNSCORED",
               "comparison": str(root), "capture": str(capture), "capture_sha256": capture_hash,
               "scorer_sha256": SCORER_SHA256, "gold": str(paths["gold"]),
               "gold_sha256": gold_sha256, "index": str(paths["index"]),
               "index_sha256": index_sha256, "freeze": str(paths["freeze"]),
               "freeze_sha256": freeze_sha256, "capture_review_sha256": review_sha256,
               "accuracy_evaluated": False, "promotion_allowed": False}
    handoff_bytes = encoded(receipt)
    write_new(state / "handoff.json", handoff_bytes)
    return {"receipt": receipt, "handoff": str(state / "handoff.json"),
            "handoff_sha256": digest(handoff_bytes)}

def scoring_argv(*, handoff_path, handoff_sha256, scorer_path, api_checkout,
                 output, python=sys.executable):
    module = scorer_at(scorer_path)
    receipt = module.sealed_json(handoff_path, handoff_sha256)
    require(receipt.get("schema") == "bridge-offline-score-handoff/v1"
            and receipt.get("status") == "FROZEN_UNSCORED", "frozen handoff required")
    root = Path(receipt["comparison"]).resolve(strict=True)
    output = outside(root, output)
    controls = {Path(receipt[n]).resolve(strict=True) for n in ("gold", "index", "freeze", "capture")}
    require(output not in controls and not output.exists(), "new separate report file required")
    require(not output.is_relative_to(Path(receipt["capture"]).parent),
            "report must be outside frozen control directory")
    # argv is a plan only. Executor coordinator owns process isolation and invocation.
    argv = [str(python), "-I", "-B", str(Path(scorer_path).resolve(strict=True))]
    for name in ("comparison", "gold", "index", "freeze", "capture"):
        argv.extend(["--" + name, receipt[name]])
    for name in ("gold", "index", "freeze", "capture"):
        argv.extend(["--" + name + "-sha256", receipt[name + "_sha256"]])
    argv.extend(["--repo", str(Path(api_checkout).resolve(strict=True)),
                 "--repo-sha", module.PINNED_API_SHA, "--output", str(output)])
    return argv

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true")
    for name in ("scorer", "comparison", "state-dir", "gold", "index", "freeze", "review"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("gold", "index", "freeze", "review"):
        parser.add_argument("--" + name + "-sha256", required=True)
    a = parser.parse_args()
    require(a.enable, "handoff disabled; explicit enable required")
    receipt = freeze_capture(enabled=True, scorer=scorer_at(a.scorer),
                             comparison=a.comparison, state_dir=a.state_dir,
                             **{k: getattr(a, k) for k in ("gold", "index", "freeze", "review",
                                "gold_sha256", "index_sha256", "freeze_sha256", "review_sha256")})
    print(json.dumps(receipt, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
