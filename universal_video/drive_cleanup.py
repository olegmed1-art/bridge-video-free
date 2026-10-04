"""Durable, bounded local source cleanup; never calls Drive or publication."""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from .durable_drive import SOURCE_RECEIPT, FINAL_RECEIPT, atomic_json, read_receipt, cleanup_proof_matches
from .drive_stage import remove_staged_job


def _digest(path: Path) -> str:
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise RuntimeError("unsafe cleanup evidence file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def queue_cleanup(spool: Path, media: Path, job_id: str, done: Path) -> Path:
    """Commit intent before deleting any bytes, including the source pin."""
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", job_id) or job_id in {".", ".."}:
        raise RuntimeError("invalid cleanup job identity")
    source = media / "drive-ready" / job_id
    result = spool / "results" / job_id
    pin = read_receipt(source / SOURCE_RECEIPT)
    manifest = read_receipt(result / "manifest.json")
    terminal = read_receipt(done)
    if (done.parent != spool / "done" or done.is_symlink()
            or pin.get("job_id") != job_id or pin.get("job_hash") != manifest.get("job_hash")
            or terminal.get("job_id") != job_id or terminal.get("job_hash") != pin.get("job_hash")
            or not cleanup_proof_matches(result, source, job_id=job_id)):
        raise RuntimeError("cleanup requires matching durable terminal result and source pin")
    path = spool / "cleanup_pending" / f"{job_id}.json"
    path.parent.mkdir(mode=0o750, exist_ok=True)
    if path.parent.is_symlink():
        raise RuntimeError("unsafe cleanup intent root")
    if path.exists():
        previous = read_receipt(path)
        if (previous.get("job_id") != job_id or previous.get("job_hash") != pin["job_hash"]
                or previous.get("source_pin") != pin or previous.get("done_sha256") != _digest(done)
                or previous.get("final_sha256") != _digest(result / FINAL_RECEIPT)):
            raise RuntimeError("existing cleanup intent changed")
        return path  # preserve retry budget / original intent
    atomic_json(path, {"schema": "universal-video-cleanup-pending-v1", "job_id": job_id,
                       "job_hash": pin["job_hash"], "source_path": str(source.absolute()),
                       "source_pin": pin, "done_name": done.name, "done_sha256": _digest(done),
                       "final_sha256": _digest(result / FINAL_RECEIPT), "attempts": 0,
                       "retry_after_unix": 0})
    return path


def retry_cleanup(spool: Path, media: Path, pending: Path) -> bool:
    """Caller holds the exclusive workload fence. Invalid proof always retains."""
    data = read_receipt(pending)
    job_id = str(data.get("job_id") or "")
    if (data.get("schema") != "universal-video-cleanup-pending-v1"
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", job_id) or job_id in {".", ".."}
            or pending != spool / "cleanup_pending" / f"{job_id}.json"):
        raise RuntimeError("invalid cleanup intent")
    source = media / "drive-ready" / job_id
    result = spool / "results" / job_id
    done_name = str(data.get("done_name") or "")
    if Path(done_name).name != done_name or not done_name.endswith(".json"):
        raise RuntimeError("invalid cleanup terminal path")
    done = spool / "done" / done_name
    if (str(source.absolute()) != data.get("source_path") or source.is_symlink()
            or media.is_symlink() or (media / "drive-ready").is_symlink()
            or result.is_symlink() or (spool / "results").is_symlink() or done.is_symlink()
            or (spool / "done").is_symlink() or pending.parent.is_symlink()):
        raise RuntimeError("cleanup path changed")
    from .maintenance import _protected_state
    active, _ = _protected_state(spool, media)
    if job_id in active:
        return False  # a queued/running retry still owns these local bytes
    # Bind the saved pin to the same verified receipt after a partial rmtree
    # may have removed the live pin. Never reconstruct or republish outputs.
    proof = read_receipt(result / FINAL_RECEIPT)
    pin = data["source_pin"]
    if (pin.get("job_id") != job_id or pin.get("job_hash") != data.get("job_hash")
            or proof.get("job_hash") != data.get("job_hash")
            or proof["source"]["before"] != pin.get("original")
            or proof["source"]["sha256_before"] != pin.get("sha256")
            or _digest(done) != data["done_sha256"]
            or _digest(result / FINAL_RECEIPT) != data["final_sha256"]
            or not cleanup_proof_matches(result, None, job_id=job_id)):
        raise RuntimeError("cleanup durability proof changed")
    live_pin = source / SOURCE_RECEIPT
    if live_pin.exists() and read_receipt(live_pin) != pin:
        raise RuntimeError("cleanup source pin changed")
    if not source.exists():
        pending.unlink()
        return True
    if int(data["attempts"]) >= 3 or float(data["retry_after_unix"]) > time.time():
        return False
    data.update(attempts=int(data["attempts"]) + 1, retry_after_unix=time.time() + 60)
    atomic_json(pending, data)  # crash consumes a bounded attempt
    try:
        remove_staged_job(source, media)
    except OSError:
        return False
    pending.unlink()
    return True
