#!/usr/bin/env python3
"""Offline, bounded visual A/B replay; optional profile-driven auction, no ASR/DDS/publisher."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import importlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

SCHEMA = "recognizer-comparison-v1"
RUNNER_VERSION = "recognizer-comparison-v1-auction-scope-r3"
# A permitted scope, not a claim that a given runtime observed any auction.
SCOPE = "PRIMARY_VISUAL_WITH_OPTIONAL_EMBEDDED_PROFILE_AUCTION; NO_ASR_DDS_OR_PUBLISHER"
REVISIONS = {
    "baseline": "3.1-free-r26.3",
    "candidate": "3.1-free-r26.3-observation-guards-candidate2",
}
MODULES = {
    "baseline": "bridge_vision.bridgit_primary_video",
    "candidate": "bridge_vision.bridgit_primary_video_candidate",
}
MAX_DURATION_SECONDS = 120
MAX_FRAME_WRITES = 512
MAX_EVIDENCE_BYTES = 512 * 1024 * 1024


class EvidenceError(BaseException):
    """Do not let historical broad Exception handlers conceal recorder failure."""


class OfflineIOError(PermissionError):
    """OSError semantics let libraries treat denied capability probes as absent."""


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, timeout=30
    ).strip()


def verify_checkout(root, expected):
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("source SHA must be exact")
    if git(root, "rev-parse", "HEAD") != expected:
        raise ValueError("checkout SHA mismatch")
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("comparison checkout must be clean")


def verified_input(item):
    path = Path(item["path"]).resolve(strict=True)
    if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
        raise ValueError("invalid input")
    if digest(path) != item["sha256"]:
        raise ValueError("input hash mismatch: " + path.name)
    return {"path": str(path), "sha256": item["sha256"]}


def prepare(manifest_path, expected_hash, output):
    manifest_path = Path(manifest_path).resolve(strict=True)
    if digest(manifest_path) != expected_hash:
        raise ValueError("manifest seal mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("gold_frozen_before_outputs") is not True:
        raise ValueError("sealed pre-output gold declaration required")
    case = manifest["case_id"]
    if not isinstance(case, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", case):
        raise ValueError("invalid case_id")
    offset = manifest["source_offset_ms"]
    if type(offset) is not int or offset < 0:
        raise ValueError("invalid original source offset")
    inputs = {name: verified_input(manifest["inputs"][name])
              for name in ("video", "reference", "profile", "sprite", "gold")}
    roots = {}
    for name in REVISIONS:
        root = Path(manifest[name]["root"]).resolve(strict=True)
        verify_checkout(root, manifest[name]["sha"])
        roots[name] = root
    if roots["baseline"] == roots["candidate"]:
        raise ValueError("separate version checkouts required")
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("output must be a new directory")
    if any(output.is_relative_to(root) for root in roots.values()):
        raise ValueError("output must be outside comparison checkouts")
    output.mkdir(parents=True, mode=0o700)
    receipt = {
        "schema": SCHEMA, "case_id": case, "manifest_sha256": expected_hash,
        "gold_sha256": inputs["gold"]["sha256"],
        "sealed_at_unix": time.time(), "source_offset_ms": offset,
        "inputs": inputs, "runtimes": {name: manifest[name] for name in REVISIONS},
        "runner_sha256": digest(Path(__file__)),
        "scope": SCOPE, "runner_version": RUNNER_VERSION,
    }
    write_json(output / "seal.json", receipt)
    configs = {}
    for variant in REVISIONS:
        directory = output / variant / (case + "-" + variant)
        directory.mkdir(parents=True, mode=0o700)
        # Gold never enters either recognition process, even as a path.
        config = {
            "variant": variant, "job_id": case + "-" + variant,
            "root": str(roots[variant]), "sha": manifest[variant]["sha"],
            "source_offset_ms": offset,
            "inputs": {k: v for k, v in inputs.items() if k != "gold"},
            "output": str(directory), "manifest_sha256": expected_hash,
        }
        path = output / (variant + "-config.json")
        write_json(path, config)
        configs[variant] = path
    return receipt, configs


def offline_audit(event, args):
    if event.startswith(("socket.", "subprocess.")) or event in {
        "os.system", "os.posix_spawn", "os.exec", "os.fork", "os.forkpty", "os.spawn",
    }:
        raise OfflineIOError("offline worker refused external I/O: " + event)


def load_runtime(root, variant):
    """Use actual installation code, including r26.3 geometry/retry patches."""
    sys.path.insert(0, str(root))
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = REVISIONS[variant]
    name = ("bridge_runtime_hardening_r26" if variant == "baseline"
            else "bridge_runtime_hardening_r26_candidate")
    runtime = importlib.import_module(name)
    allowed_revisions = {REVISIONS[variant]}
    if variant == "candidate":
        allowed_revisions.add("3.1-free-r26.3-auction-candidate3")
    if runtime.REVISION not in allowed_revisions:
        raise ValueError("unexpected runtime revision")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = runtime.REVISION
    def no_token():
        raise EvidenceError("offline comparison must never request credentials")
    runtime.install(no_token)
    module = importlib.import_module(MODULES[variant])
    if not Path(module.__file__).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("recognizer imported from another checkout")
    return module


def pixel_info(image):
    return {
        "decoded_pixels_sha256": hashlib.sha256(image.tobytes(order="C")).hexdigest(),
        "shape": list(image.shape), "dtype": str(image.dtype),
    }


class Recorder:
    def __init__(self, module, output, offset=0, max_frames=MAX_FRAME_WRITES,
                 max_bytes=MAX_EVIDENCE_BYTES):
        self.module, self.output, self.offset = module, Path(output), offset
        self.output.mkdir(parents=True, exist_ok=True)
        self.log = (self.output / "events.jsonl").open("x", encoding="utf-8")
        self.max_frames, self.max_bytes = max_frames, max_bytes
        self.frames = self.bytes = self.attempts = 0
        self.last_attempt = None
        self.changes = []
        self.write_png = module._write_png

    def emit(self, kind, **fields):
        try:
            row = {"event": kind, "recorded_monotonic_ns": time.monotonic_ns(), **fields}
            self.log.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            self.log.flush()
            os.fsync(self.log.fileno())
        except Exception as exc:
            raise EvidenceError("cannot retain event evidence") from exc

    def context(self):
        frame = inspect.currentframe()
        try:
            while frame is not None:
                if frame.f_code is self.module.recognize_video_primary.__code__:
                    v = frame.f_locals
                    return {k: v[k] for k in ("timestamp_ms", "last_attempt_ms") if k in v} | {
                        "pending_pairs": len(v.get("pending", ())),
                        "accepted_observations": len(v.get("candidates", ())),
                    }
                frame = frame.f_back
            return {}
        finally:
            del frame

    def save(self, path, *, image=None, source=None):
        if self.frames >= self.max_frames:
            raise EvidenceError("frame evidence limit reached")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise EvidenceError("evidence overwrite refused")
        try:
            if source is None:
                self.write_png(path, image)
            else:
                shutil.copyfile(source, path)
            self.frames += 1
            self.bytes += path.stat().st_size
            if self.bytes > self.max_bytes:
                raise EvidenceError("evidence byte limit reached")
            info = {"path": str(path.relative_to(self.output)), "sha256": digest(path)}
            if image is not None:
                info.update(pixel_info(image))
            else:
                cv2, _ = self.module.rank_layout._pixel_runtime()
                decoded = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if decoded is None:
                    raise EvidenceError("copied observation cannot be decoded")
                info.update(pixel_info(decoded))
            return info
        except Exception as exc:
            raise EvidenceError("cannot retain frame evidence") from exc

    def patch(self, obj, name, replacement):
        self.changes.append((obj, name, getattr(obj, name)))
        setattr(obj, name, replacement)

    def __enter__(self):
        original_frame = self.module._frame_at
        original_geometry = self.module._full_geometry_gate
        original_observe = self.module.EventFrameSelector.observe
        original_backend = self.module.recognize_frames_with_original_gambler_deck
        original_accept = self.module._accepted_primary_result
        recorder = self

        def frame_at(capture, timestamp):
            frame = original_frame(capture, timestamp)
            context = recorder.context()
            if frame is None:
                recorder.emit("DECODE_REJECTED", requested_ms=timestamp, **context)
                return frame
            cv2, _ = recorder.module.rank_layout._pixel_runtime()
            position = float(capture.get(cv2.CAP_PROP_POS_MSEC))
            evidence = recorder.save(
                recorder.output / "decoded" / ("%05d.png" % recorder.frames), image=frame)
            recorder.emit("FRAME_DECODED", requested_ms=timestamp,
                          requested_source_ms=recorder.offset + timestamp,
                          decoder_reported_position_ms=position if math.isfinite(position) else None,
                          pts_verified=False, evidence=evidence, **context)
            return frame

        def geometry(image, *args, **kwargs):
            try:
                value = original_geometry(image, *args, **kwargs)
            except Exception as exc:
                recorder.emit("GEOMETRY_ERROR", error_type=type(exc).__name__,
                              frame=pixel_info(image), **recorder.context())
                raise
            recorder.emit("GEOMETRY_RESULT", geometry=value, frame=pixel_info(image),
                          **recorder.context())
            return value

        def observe(instance, signature, timestamp):
            value = original_observe(instance, signature, timestamp)
            recorder.emit("SELECTOR_RESULT", observed_ms=timestamp,
                          selector_event=dataclasses.asdict(value) if value is not None else None,
                          **recorder.context())
            return value

        def backend(reference, frames, profile, **kwargs):
            recorder.attempts += 1
            attempt = recorder.attempts
            recorder.last_attempt = attempt
            directory = recorder.output / "attempts" / ("%05d" % attempt)
            evidence = [recorder.save(directory / ("frame-%d.png" % i), source=path)
                        for i, path in enumerate(frames)]
            expected = kwargs.get("expected_frame_sha256s")
            if expected != [item["sha256"] for item in evidence]:
                raise EvidenceError("attempt frame hash mismatch")
            recorder.emit("PAIR_CAPTURED", attempt=attempt, frames=evidence,
                          observation_timestamps_ms=kwargs.get("observation_timestamps_ms"),
                          **recorder.context())
            try:
                result = original_backend(reference, frames, profile, **kwargs)
            except Exception as exc:
                recorder.emit("BACKEND_ERROR", attempt=attempt,
                              error_type=type(exc).__name__, detail=str(exc)[:500],
                              **recorder.context())
                raise
            try:
                write_json(directory / "backend-result.json", result)
            except Exception as exc:
                raise EvidenceError("cannot retain backend result") from exc
            recorder.emit("BACKEND_RETURN", attempt=attempt, status=result.get("status"),
                          **recorder.context())
            return result

        def accept(result):
            value = original_accept(result)
            recorder.emit("ACCEPTANCE_RESULT", attempt=recorder.last_attempt,
                          accepted=value, **recorder.context())
            return value

        self.patch(self.module, "_frame_at", frame_at)
        self.patch(self.module, "_full_geometry_gate", geometry)
        self.patch(self.module.EventFrameSelector, "observe", observe)
        self.patch(self.module, "recognize_frames_with_original_gambler_deck", backend)
        self.patch(self.module, "_accepted_primary_result", accept)
        return self

    def __exit__(self, *exc):
        for obj, name, previous in reversed(self.changes):
            setattr(obj, name, previous)
        self.log.close()


def video_metadata(module, video):
    cv2, _ = module.rank_layout._pixel_runtime()
    capture = cv2.VideoCapture(str(video))
    try:
        if not capture.isOpened():
            raise ValueError("video cannot be opened")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not (math.isfinite(fps) and fps > 0 and math.isfinite(count) and count > 0):
            raise ValueError("invalid video metadata")
        duration = count / fps
        if duration > MAX_DURATION_SECONDS:
            raise ValueError("bounded replay requires a clip of at most 120 seconds")
        return {"fps": fps, "frame_count": count, "duration_seconds": duration}
    finally:
        capture.release()


def worker(config_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    output = Path(config["output"])
    started = time.monotonic()
    status = {"status": "ERROR", "job_id": config["job_id"], "variant": config["variant"],
              "source_sha": config["sha"], "pid": os.getpid(),
              "scope": SCOPE, "runner_version": RUNNER_VERSION, "accuracy_evaluated": False}
    try:
        verify_checkout(Path(config["root"]), config["sha"])
        inputs = {k: verified_input(v) for k, v in config["inputs"].items()}
        sys.addaudithook(offline_audit)
        module = load_runtime(config["root"], config["variant"])
        status["version"] = module.PRIMARY_VIDEO_VERSION
        status["metadata"] = video_metadata(module, inputs["video"]["path"])
        with Recorder(module, output / "evidence", config["source_offset_ms"]) as recorder:
            result = module.recognize_video_primary(
                Path(inputs["video"]["path"]),
                reference_frame=Path(inputs["reference"]["path"]),
                profile_path=Path(inputs["profile"]["path"]),
                gambler_sprite_path=Path(inputs["sprite"]["path"]),
                gambler_sprite_sha256=inputs["sprite"]["sha256"],
                output_dir=output / "recognizer",
                verified_card_width_px=109.0, verified_card_height_px=147.0,
                scan_ms=1000, attempt_gap_ms=15000, max_deals=64,
            )
            write_json(output / "result.json", result)
            auction = result.get("auction_recognition")
            status["auction_result_status"] = (
                auction.get("status", "NOT_REPORTED") if isinstance(auction, dict)
                else "NOT_REPORTED"
            )
            status.update(status="RETURNED", result_status=result.get("status"),
                          attempts=recorder.attempts, retained_frames=recorder.frames,
                          evidence_bytes=recorder.bytes)
    except BaseException as exc:
        status.update(error_type=type(exc).__name__, detail=str(exc)[:500])
    finally:
        status["elapsed_seconds_with_observer"] = time.monotonic() - started
        write_json(output / "worker-status.json", status)
    return 0 if status["status"] == "RETURNED" else 1


def launch_worker(command, **kwargs):
    """Runner-local launch hook; Git checks keep the shared subprocess module."""
    return subprocess.run(command, **kwargs)


def compare(manifest, seal, output, timeout=300):
    receipt, configs = prepare(manifest, seal, output)
    output = Path(output).resolve()
    results = {}
    for variant, path in configs.items():
        config = json.loads(path.read_text(encoding="utf-8"))
        directory = Path(config["output"])
        try:
            with (directory / "process.log").open("xb") as log:
                process = launch_worker(
                    [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                     "_worker", "--config", str(path)],
                    cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                    timeout=timeout, check=False,
                )
            state = json.loads((directory / "worker-status.json").read_text(encoding="utf-8"))
            state["exit_code"] = process.returncode
        except subprocess.TimeoutExpired:
            state = {"status": "TIMEOUT", "exit_code": None}
        except (OSError, ValueError) as exc:
            state = {"status": "PROCESS_ERROR", "error_type": type(exc).__name__}
        results[variant] = state
        # Never compare against a changed source, asset, gold or checkout.
        for item in receipt["inputs"].values():
            verified_input(item)
        for runtime in receipt["runtimes"].values():
            verify_checkout(runtime["root"], runtime["sha"])
    summary = {
        "schema": SCHEMA, "manifest_sha256": seal,
        "scope": receipt["scope"], "runner_version": RUNNER_VERSION,
        "gold_sha256": receipt["gold_sha256"], "runs": results,
        "status": "CAPTURED_UNSCORED" if all(x.get("status") == "RETURNED" and x.get("exit_code") == 0
                                             for x in results.values())
                  else "REPLAY_ERROR",
        "accuracy_evaluated": False, "promotion_allowed": False,
        "note": "Returned is not recognition success. Score sealed gold separately, including abstentions.",
    }
    write_json(output / "comparison.json", summary)
    return 0 if summary["status"] == "CAPTURED_UNSCORED" else 1


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("compare")
    replay.add_argument("--manifest", required=True, type=Path)
    replay.add_argument("--manifest-sha256", required=True)
    replay.add_argument("--output", required=True, type=Path)
    replay.add_argument("--timeout", type=int, default=300)
    child = commands.add_parser("_worker")
    child.add_argument("--config", required=True, type=Path)
    check = commands.add_parser("inspect")
    check.add_argument("--root", required=True, type=Path)
    check.add_argument("--sha", required=True)
    check.add_argument("--variant", required=True, choices=REVISIONS)
    args = parser.parse_args()
    if args.command == "_worker":
        return worker(args.config)
    if args.command == "inspect":
        verify_checkout(args.root, args.sha)
        sys.addaudithook(offline_audit)
        module = load_runtime(args.root, args.variant)
        print(json.dumps({"variant": args.variant, "sha": args.sha,
                          "module": module.__name__, "version": module.PRIMARY_VIDEO_VERSION,
                          "pid": os.getpid(), "media_processed": False}))
        return 0
    if not 1 <= args.timeout <= 900:
        parser.error("timeout must be between 1 and 900 seconds per process")
    return compare(args.manifest, args.manifest_sha256, args.output, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
