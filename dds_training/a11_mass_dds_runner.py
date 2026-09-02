#!/usr/bin/env python3
"""A11 guarded mass DDS orchestration.

This runner is deliberately fail-closed. It never promotes canon, curriculum,
methodology, mastery, or student-profile state. It orchestrates an explicitly
provided immutable corpus command, persists local checkpoints atomically, and
produces machine-readable evidence for a human/evidence gate.

The actual DDS evaluator remains the repository's verified evaluator; this
wrapper does not invent a DDS service/API contract.
"""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, subprocess, sys, tempfile, time

ALLOWED = {10000, 30000, 40000}


def atomic_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def digest_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=int, required=True, choices=sorted(ALLOWED))
    p.add_argument("--corpus", type=pathlib.Path, required=True)
    p.add_argument("--checkpoint", type=pathlib.Path, required=True)
    p.add_argument("--evidence", type=pathlib.Path, required=True)
    p.add_argument("--evaluator", required=True,
                   help="Verified evaluator command; receives A11_* env vars")
    p.add_argument("--resume", action="store_true")
    a = p.parse_args()

    if not a.corpus.is_file():
        raise SystemExit(f"FAIL_CLOSED: corpus not found: {a.corpus}")
    corpus_sha = digest_file(a.corpus)
    state = {"schema":"a11-checkpoint-v1","target":a.target,"corpus_sha256":corpus_sha,
             "status":"starting","started_at":int(time.time()),"authority":"EVIDENCE_ONLY",
             "forbidden_promotions":["canon","curriculum","methodology","mastery","student_profile"]}
    if a.resume and a.checkpoint.exists():
        old = json.loads(a.checkpoint.read_text(encoding="utf-8"))
        if old.get("target") != a.target or old.get("corpus_sha256") != corpus_sha:
            raise SystemExit("FAIL_CLOSED: checkpoint target/corpus mismatch")
        state.update(old); state["status"] = "resuming"
    atomic_json(a.checkpoint, state)

    env = os.environ.copy()
    env.update({"A11_TARGET":str(a.target),"A11_CORPUS":str(a.corpus.resolve()),
                "A11_CORPUS_SHA256":corpus_sha,"A11_CHECKPOINT":str(a.checkpoint.resolve()),
                "A11_EVIDENCE":str(a.evidence.resolve()),"A11_AUTHORITY":"EVIDENCE_ONLY"})
    proc = subprocess.run(a.evaluator, shell=True, env=env)
    if proc.returncode != 0:
        state.update(status="failed", returncode=proc.returncode, finished_at=int(time.time()))
        atomic_json(a.checkpoint, state)
        return proc.returncode
    if not a.evidence.is_file():
        state.update(status="failed_no_evidence", finished_at=int(time.time()))
        atomic_json(a.checkpoint, state)
        return 65
    ev = json.loads(a.evidence.read_text(encoding="utf-8"))
    required = {"processed","target","corpus_sha256","dd_trajectory_complete","legal_alternatives_complete","regret_complete","first_swing_complete","unrecovered_damage_complete"}
    missing = sorted(required - set(ev))
    ok = (not missing and ev["target"] == a.target and ev["processed"] == a.target
          and ev["corpus_sha256"] == corpus_sha
          and all(ev[k] is True for k in ["dd_trajectory_complete","legal_alternatives_complete","regret_complete","first_swing_complete","unrecovered_damage_complete"]))
    state.update(status="passed" if ok else "failed_gate", finished_at=int(time.time()),
                 evidence_sha256=digest_file(a.evidence), missing_fields=missing)
    atomic_json(a.checkpoint, state)
    return 0 if ok else 66

if __name__ == "__main__":
    sys.exit(main())
