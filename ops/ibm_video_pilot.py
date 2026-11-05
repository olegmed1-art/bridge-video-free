"""One-shot IBM video pilot boundary; this revision executes SYNTHETIC mocks only.

No Docker client, SSH, power API, downloader, scheduler or resident authority is
implemented here. An injected executor receives an immutable allowlisted plan.
Its observations are evidence inputs, NOT independently authenticated facts.
Live admission belongs to the parent after real clip/image/cache observations,
independent assurance, a hard output quota and external deadline/Stop control.

Linux preparation below implements a finite persistent output filesystem,
file-size limits, a narrowed seccomp profile, sealed input copies and bounded
pipe capture. These kernel mechanisms and the Docker mount handoff are NOT_TESTED
on a host here; unsupported mechanisms fail closed. Post-run checks alone are
not enforcement. There is no provider lifecycle implementation in this module.
Results and attempt markers are never deleted, even after delivery. Cleanup is
limited to an executor-proven newly created, stopped, exact owned container ID.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import stat
import struct
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

from universal_video.contract import CONTRACT_VERSION, VideoJob, canonical_job_hash
from universal_video.profiles import resolve_profile
from universal_video.readiness import RUNNER_EXECUTED_STAGES
from universal_video.result_conformance import verify_result
from universal_video.speaker_role_verifier import verify_speaker_roles

BASELINE = "d7860237cb4f00cc11a98ade1db3f95862699cfd"
DRIVE_ID = "1arn4rXDjEKaJ9HqmDFYfiqENm1x1Hs37"
SOURCE_SHA256 = "135664d4f7eec1a66c9e9da7d6adeef748f315308c2885126ca6ce2b0b574ed3"
SOURCE_BYTES = 682773765
SOURCE_SECONDS = 7132.12
START, END = 1250.0, 1320.0
ROOT = "/var/lib/universal-video"
CODE_ROOT = "/opt/bridge-school/universal-video-src"
DOCKERFILE = "deploy/oracle-universal-video/Dockerfile"
ENTRYPOINT_SOURCE = "deploy/oracle-universal-video/universal-video-container-entrypoint.sh"
MODEL = ROOT + "/model-cache/asr"
SPEAKER = ROOT + "/model-cache/speaker"
SPOOL_LEAVES = ("inbox", "running", "done", "failed", "results", "progress")
SPEAKER_HASHES = {
    "pyannote-segmentation-3.0.onnx": "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx": "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b",
}
ENTRYPOINT = ("/usr/bin/tini", "--", "/usr/local/bin/universal-video-container-entrypoint")
RUNNER_ARGV = ("python", "-m", "universal_video.runner", ROOT + "/media/pilot.job.json",
               "--output-root", ROOT + "/output")
DOWNSTREAM = ("bridge_context", "bridge_positions", "dds3_optional",
              "educational_candidates", "teacher", "PDF")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_RECEIPT_BYTES = 256 * 1024  # Separate control-file cap, not the media quota.
LIVE_GAPS = (
    "LIVE_EXECUTOR_NOT_IMPLEMENTED",
    "LINUX_OUTPUT_ENFORCEMENT_NOT_TESTED_ON_HOST",
    "SEALED_INPUT_DOCKER_MOUNT_HANDOFF_NOT_TESTED_ON_HOST",
    "EXTERNAL_DEADLINE_EXPORT_STOP_ADMISSION_NOT_OBSERVED",
    "REAL_CLIP_SOURCE_PTS_IMAGE_CODE_AND_MODEL_EVIDENCE_REQUIRED",
    "IMAGE_WORKDIR_EXECUTABLE_AND_DEPENDENCY_ATTESTATION_REQUIRED",
)


class PilotBlocked(ValueError):
    """A fail-closed gate; error codes contain no producer logs or secrets."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PilotBlocked(code)


def encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def fingerprint(value: object) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def strict_json(raw: str) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def constant(_):
        raise PilotBlocked("NONFINITE_JSON")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def safe_path(path: Path, *, directory: bool = False) -> os.stat_result:
    require(path.is_absolute() and ".." not in path.parts, "ABSOLUTE_PATH_REQUIRED")
    require(not any(c in str(path) for c in ",\n\r\0"), "UNSAFE_MOUNT_PATH")
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not (
            getattr(info, "st_file_attributes", 0) & 0x400), "SYMLINK_OR_REPARSE_PATH")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            "UNSAFE_FILE_TYPE")
    if not directory:
        require(info.st_nlink == 1, "HARDLINK_FORBIDDEN")
    return info


def file_identity(path: Path, *, cap: int) -> tuple[int, str, int, int, int]:
    before = safe_path(path)
    require(0 <= before.st_size <= cap, "FILE_BYTES_LIMIT")
    digest = hashlib.sha256()
    # O_NOFOLLOW where available plus ancestor checks; not a live TOCTOU proof.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), "PATH_RACE")
        count = 0
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            count += len(block)
            require(count <= cap, "FILE_BYTES_LIMIT")
            digest.update(block)
        after = os.fstat(stream.fileno())
    last = safe_path(path)
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    # Python 3.12 Windows lstat and fstat expose different ctime semantics.
    # Compare ctime only within the same API; still compare inode/mtime/size
    # across both APIs. This does not turn the snapshot into a live mount pin.
    require(signature(before) == signature(opened) == signature(after) == signature(last)
            and before.st_ctime_ns == last.st_ctime_ns and opened.st_ctime_ns == after.st_ctime_ns
            and count == before.st_size,
            "PATH_RACE")
    return count, digest.hexdigest(), last.st_ino, last.st_mtime_ns, last.st_ctime_ns


def tree_hashes(root: Path, *, max_bytes: int, max_files: int) -> dict[str, str]:
    safe_path(root, directory=True)
    result = {}
    total = 0
    entries = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        entries += len(dirs) + len(files)
        require(entries <= max_files, "FILE_COUNT_LIMIT")
        for name in dirs:
            safe_path(Path(parent) / name, directory=True)
        for name in sorted(files):
            path = Path(parent) / name
            require(len(result) < max_files, "FILE_COUNT_LIMIT")
            size, digest, *_ = file_identity(path, cap=max_bytes - total)
            total += size
            result[path.relative_to(root).as_posix()] = digest
    require(bool(result), "EMPTY_TREE")
    return result


def code_inventory(repo: Path) -> dict[str, str]:
    """Hash actual COPY inputs of the existing Dockerfile, not its revision label.

    The caller pins this complete inventory in its admission. No build occurs.
    image_code_inventory maps COPY input hashes to actual image paths. The
    Dockerfile itself is recipe evidence, not a file copied into this image.
    Installed dependencies/base layers are bound by the immutable image ID.
    """
    dockerfile = repo / "deploy/oracle-universal-video/Dockerfile"
    safe_path(dockerfile)
    text = dockerfile.read_text(encoding="utf-8").replace("\\\n", " ")
    sources = {"deploy/oracle-universal-video/Dockerfile"}
    for line in text.splitlines():
        if line.startswith("COPY "):
            sources.update(line.split()[1:-1])
    result = {}
    for name in sorted(sources):
        require(not Path(name).is_absolute() and ".." not in Path(name).parts, "CODE_PATH_ESCAPE")
        path = repo / name
        if path.is_dir():
            for relative, digest in tree_hashes(path, max_bytes=64 * 1024**2, max_files=4096).items():
                # __pycache__ is not approved source (run offline tests with -B).
                require("__pycache__" not in relative, "CODE_TREE_HAS_BYTECODE")
                result[name + "/" + relative] = digest
        else:
            result[name] = file_identity(path, cap=16 * 1024**2)[1]
    require("universal_video/runner.py" in result and
            "bridge_speaker_diarization_v3.py" in result, "CODE_INVENTORY_INCOMPLETE")
    return result


def image_code_inventory(source_hashes: dict[str, str]) -> dict[str, str]:
    """Exact destinations in the reused Dockerfile; do not attest a label."""
    return {("/usr/local/bin/universal-video-container-entrypoint" if name == ENTRYPOINT_SOURCE
             else CODE_ROOT + "/" + name): digest
            for name, digest in source_hashes.items() if name != DOCKERFILE}


@dataclass(frozen=True)
class Clip:
    path: Path
    size_bytes: int
    sha256: str
    duration_seconds: float
    # All four endpoints are independently observed presentation timestamps.
    source_start_pts: float
    source_end_pts: float
    clip_start_pts: float
    clip_end_pts: float
    drive_id: str
    source_bytes: int
    source_sha256: str
    source_duration_seconds: float
    provenance: str  # VERIFIED_BYTES_AND_PTS, or SYNTHETIC_FIXTURE in tests only
    evidence_sha256: str


@dataclass(frozen=True)
class Limits:
    # No compute-time estimate is implied by the 70-second media limit.
    deadline_epoch: float
    export_reserve_seconds: float
    stop_reserve_seconds: float
    max_output_bytes: int
    max_output_files: int
    max_file_bytes: int
    max_log_bytes: int
    # Owner removed the former USD20 ceiling on 2026-10-11. None means no
    # monetary ceiling; a later explicit cap must still bind to this admission.
    budget_usd: float | None
    admission_sha256: str
    output_quota_evidence_sha256: str
    stop_contract_sha256: str


@dataclass(frozen=True)
class Request:
    run_id: str  # Fresh UUID hex, also the owned output scope suffix.
    parent: Path  # Dedicated owned pilot parent; never a production output root.
    repo: Path
    clip: Clip
    asr_model: Path
    speaker_cache: Path
    image_id: str
    code_hashes: tuple[tuple[str, str], ...]
    model_hashes: tuple[tuple[str, str], ...]
    limits: Limits
    source_commit: str = BASELINE
    # Bounded producer evidence, not an authenticated verifier attestation.
    # Its exact bytes are bound by clip.evidence_sha256 and the admission.
    clip_evidence_json: bytes = b""


@dataclass(frozen=True)
class Observation:
    image_id: str
    code_hashes: tuple[tuple[str, str], ...]  # actual absolute IMAGE paths
    entrypoint: tuple[str, ...]
    # Complete effective image/container env, including base image ENV.
    # No credentials or caller-controlled environment may be present.
    base_env: tuple[tuple[str, str], ...]
    clip_sha256: str
    clip_bytes: int
    duration_seconds: float
    pts: tuple[float, float, float, float]
    video_streams: int
    audio_streams: int
    synthetic: bool


@dataclass(frozen=True)
class Mount:
    source: str
    target: str
    readonly: bool


@dataclass(frozen=True)
class Plan:
    name: str
    scope: Path
    image_id: str
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    mounts: tuple[Mount, ...]
    compute_deadline_epoch: float
    job_json: bytes
    job_hash: str
    export_deadline_epoch: float
    # No host env inheritance, including PATH, DOCKER_HOST or DOCKER_CONTEXT.
    client_env: tuple[tuple[str, str], ...] = ()

    def docker_create_argv(self) -> tuple[str, ...]:
        """Reviewable arguments only; never spawned by this module."""
        args = ["docker", "create", "--name", self.name, "--label", "ibm-video-pilot=" + self.name,
                "--pull=never", "--network=none", "--read-only", "--cpus=4", "--memory=8g",
                "--memory-swap=8g", "--pids-limit=512", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--user=10001:10001",
                "--tmpfs=/tmp:rw,noexec,nosuid,size=1g,mode=1777",
                "--log-driver=none"]
        for key, value in self.env:
            args += ["--env", key + "=" + value]
        for mount in self.mounts:
            value = "type=bind,src=" + mount.source + ",dst=" + mount.target
            args += ["--mount", value + (",readonly" if mount.readonly else "")]
        return tuple(args) + (self.image_id,) + self.argv


@dataclass(frozen=True)
class Execution:
    container_id: str
    created_new: bool
    owner: str
    stopped: bool
    exit_code: int
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float
    peak_memory_bytes: int


class Executor(Protocol):
    """Injected, SYNTHETIC-only test double. Never implicitly construct one.

    A future live implementation must pin paths/image before start, authenticate
    complete image bytes/environment observations, enforce limits while running,
    and return a stopped exact container receipt. It needs separate approval.
    """
    synthetic: bool

    def observe(self, request: Request) -> Observation: ...
    def execute_one(self, plan: Plan, limits: Limits) -> Execution: ...
    def remove_owned(self, container_id: str, owner: str) -> None: ...


def admission_fingerprint(request: Request) -> str:
    """Bind an external approval document to this exact plan and caps.

    This digest is not a signature or a verification of the approver. Synthetic
    tests can construct it; it cannot remove any live gate in this revision.
    """
    limits = asdict(request.limits)
    limits.pop("admission_sha256")
    clip = {**asdict(request.clip), "path": str(request.clip.path)}
    return fingerprint({"schema": "ibm-video-pilot-admission-v1", "run_id": request.run_id,
                        "parent": str(request.parent), "repo": str(request.repo), "clip": clip,
                        "source_commit": request.source_commit, "image_id": request.image_id,
                        "code_hashes": request.code_hashes, "model_hashes": request.model_hashes,
                        "asr_model": str(request.asr_model), "speaker_cache": str(request.speaker_cache),
                        "speaker_hashes": SPEAKER_HASHES, "limits": limits})


def environment() -> dict[str, str]:
    return {
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        "UNIVERSAL_VIDEO_SOURCE_COMMIT": BASELINE,
        "UNIVERSAL_VIDEO_REQUIRE_STAGED_SOURCE": "0",
        "UNIVERSAL_VIDEO_SPOOL_ROOT": ROOT + "/spool",
        "UNIVERSAL_VIDEO_OUTPUT_ROOT": ROOT + "/output",
        "UNIVERSAL_VIDEO_MEDIA_ROOT": ROOT + "/media",
        "HF_HOME": ROOT + "/model-cache",
        "UNIVERSAL_VIDEO_WHISPER_MODEL": MODEL,
        "UNIVERSAL_VIDEO_SPEAKER_MODEL_CACHE": SPEAKER,
        "BRIDGE_SPEAKER_MODEL_CACHE": SPEAKER,
        "BRIDGE_SPEAKER_ALLOW_NEMO_COMPAT": "0",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "UNIVERSAL_VIDEO_ASR_THREADS": "4", "OMP_NUM_THREADS": "4",
    }


def _limits(limits: Limits, now: float) -> float:
    for number in (now, limits.deadline_epoch, limits.export_reserve_seconds,
                   limits.stop_reserve_seconds):
        require(type(number) in (int, float) and math.isfinite(number), "INVALID_ADMISSION")
    require(limits.export_reserve_seconds > 0 and limits.stop_reserve_seconds > 0,
            "EXPORT_STOP_RESERVE_REQUIRED")
    if limits.budget_usd is not None:
        require(type(limits.budget_usd) in (int, float) and math.isfinite(limits.budget_usd)
                and limits.budget_usd > 0, "INVALID_BUDGET")
    for digest in (limits.admission_sha256, limits.output_quota_evidence_sha256,
                   limits.stop_contract_sha256):
        require(isinstance(digest, str) and HEX64.fullmatch(digest) is not None,
                "EXTERNAL_ENFORCEMENT_EVIDENCE_REQUIRED")
    for number, cap in ((limits.max_output_bytes, 512 * 1024**2),
                        (limits.max_output_files, 1024), (limits.max_file_bytes, 256 * 1024**2),
                        (limits.max_log_bytes, 4 * 1024**2)):
        require(type(number) is int and 0 < number <= cap, "INVALID_OUTPUT_CAP")
    require(limits.max_file_bytes <= limits.max_output_bytes, "INVALID_OUTPUT_CAP")
    export_deadline = limits.deadline_epoch - limits.stop_reserve_seconds
    deadline = export_deadline - limits.export_reserve_seconds
    # Positive inputs alone do not prove representable, non-overlapping windows.
    require(math.isfinite(deadline) and math.isfinite(export_deadline)
            and deadline < export_deadline < limits.deadline_epoch
            and export_deadline - deadline >= limits.export_reserve_seconds
            and limits.deadline_epoch - export_deadline >= limits.stop_reserve_seconds,
            "UNREPRESENTABLE_DEADLINE_WINDOWS")
    require(deadline > now, "NO_COMPUTE_WINDOW")
    return deadline


def _clip_evidence(clip: Clip, raw: bytes) -> None:
    """Bind a supplied document to bytes and a half-open seconds mapping.

    This checks consistency only. A producer can assert false facts; live
    execution still requires the independent source/PTS verifier in LIVE_GAPS.
    No source video is opened or downloaded by this document check.
    """
    require(type(raw) is bytes and 0 < len(raw) <= 16 * 1024, "CLIP_EVIDENCE_REQUIRED_OR_TOO_LARGE")
    require(hashlib.sha256(raw).hexdigest() == clip.evidence_sha256, "CLIP_EVIDENCE_HASH_MISMATCH")
    try:
        evidence = strict_json(raw.decode("utf-8"))
        require(type(evidence["source"]["size_bytes"]) is int and
                type(evidence["clip"]["size_bytes"]) is int, "CLIP_EVIDENCE_BYTES_INVALID")
        numeric = (evidence["source"]["size_bytes"], evidence["source"]["duration_seconds"],
                   evidence["clip"]["size_bytes"], evidence["clip"]["duration_seconds"],
                   *evidence["mapping"]["source"], *evidence["mapping"]["clip"])
        require(all(type(n) in (int, float) and math.isfinite(n) for n in numeric),
                "CLIP_EVIDENCE_NUMERIC_INVALID")
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError) as exc:
        raise PilotBlocked("CLIP_EVIDENCE_INVALID") from exc
    expected = {"schema": "ibm-video-clip-evidence-v1", "provenance": clip.provenance,
                "source": {"drive_id": clip.drive_id, "size_bytes": clip.source_bytes,
                           "sha256": clip.source_sha256, "duration_seconds": clip.source_duration_seconds},
                "clip": {"size_bytes": clip.size_bytes, "sha256": clip.sha256,
                         "duration_seconds": clip.duration_seconds},
                "mapping": {"time_unit": "seconds", "interval": "half_open",
                            "source": [clip.source_start_pts, clip.source_end_pts],
                            "clip": [clip.clip_start_pts, clip.clip_end_pts]}}
    require(evidence == expected, "CLIP_EVIDENCE_BINDING_MISMATCH")


def _inputs(request: Request, observation: Observation) -> dict[str, tuple]:
    c = request.clip
    require(request.source_commit == BASELINE, "BASELINE_MISMATCH")
    require(re.fullmatch(r"[0-9a-f]{32}", request.run_id) is not None, "INVALID_RUN_ID")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", request.image_id) is not None,
            "IMMUTABLE_IMAGE_ID_REQUIRED")
    require(observation.image_id == request.image_id, "IMAGE_MISSING_OR_MISMATCH")
    require(observation.entrypoint == ENTRYPOINT, "ENTRYPOINT_MISMATCH")
    expected = dict(request.code_hashes)
    require(len(expected) == len(request.code_hashes) and expected == code_inventory(request.repo),
            "CODE_BYTES_MISMATCH")
    image_expected = image_code_inventory(expected)
    require(dict(observation.code_hashes) == image_expected and
            len(observation.code_hashes) == len(image_expected), "IMAGE_CODE_BYTES_MISMATCH")
    # Known harmless base-image settings are allowed by key and bounded value.
    base_allowed = {"PATH", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256"}
    base = dict(observation.base_env)
    require(len(base) == len(observation.base_env), "DUPLICATE_ENV")
    for key, value in base.items():
        require(key in base_allowed or key in environment(), "FORBIDDEN_IMAGE_ENV")
        require(isinstance(value, str) and len(value) <= 1024 and "\n" not in value,
                "INVALID_IMAGE_ENV")
        # Existing Dockerfile staged-source default is overridden only in pilot.
        if key in environment():
            require(value == environment()[key] or
                    (key == "UNIVERSAL_VIDEO_REQUIRE_STAGED_SOURCE" and value == "1"),
                    "IMAGE_ENV_ALIAS_OR_VALUE_MISMATCH")
    require(c.provenance in {"VERIFIED_BYTES_AND_PTS", "SYNTHETIC_FIXTURE"},
            "CLAIMED_OR_NULL_PROVENANCE")
    require(c.provenance != "SYNTHETIC_FIXTURE" or observation.synthetic is True,
            "SYNTHETIC_NOT_REAL_VIDEO")
    require(c.drive_id == DRIVE_ID and c.source_bytes == SOURCE_BYTES and
            c.source_sha256 == SOURCE_SHA256 and c.source_duration_seconds == SOURCE_SECONDS,
            "ORIGINAL_SOURCE_MISMATCH")
    require(isinstance(c.evidence_sha256, str) and HEX64.fullmatch(c.evidence_sha256) is not None,
            "SOURCE_EVIDENCE_REQUIRED")
    pts = (c.source_start_pts, c.source_end_pts, c.clip_start_pts, c.clip_end_pts)
    require(all(type(n) in (int, float) and math.isfinite(n) for n in (*pts, c.duration_seconds)),
            "INVALID_PTS")
    require(type(observation.pts) is tuple and len(observation.pts) == 4 and
            all(type(n) in (int, float) and math.isfinite(n)
                for n in (*observation.pts, observation.duration_seconds)), "INVALID_OBSERVED_PTS")
    require(pts == (START, END, 0.0, END - START) and observation.pts == pts,
            "CLIP_PTS_MAPPING_MISMATCH")
    require(0 < c.duration_seconds <= 70 and abs(c.duration_seconds - (END - START)) <= 0.001 and
            observation.duration_seconds == c.duration_seconds, "CLIP_DURATION_MISMATCH")
    require(observation.video_streams >= 1 and observation.audio_streams >= 1,
            "VIDEO_AND_AUDIO_REQUIRED")
    require(c.path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}, "VIDEO_REQUIRED")
    clip = file_identity(c.path, cap=256 * 1024**2)
    require(clip[:2] == (c.size_bytes, c.sha256) == (observation.clip_bytes, observation.clip_sha256),
            "CLIP_BYTES_HASH_MISMATCH")
    require(type(c.size_bytes) is int and c.size_bytes > 0, "INVALID_CLIP_SIZE")
    _clip_evidence(c, request.clip_evidence_json)
    models = tree_hashes(request.asr_model, max_bytes=8 * 1024**3, max_files=128)
    require({"model.bin", "config.json", "tokenizer.json"} <= models.keys(), "ASR_CACHE_INCOMPLETE")
    require(models == dict(request.model_hashes) and len(models) == len(request.model_hashes),
            "ASR_HASH_MISMATCH")
    safe_path(request.speaker_cache, directory=True)
    identities = {str(c.path): clip}
    for relative in models:
        path = request.asr_model / relative
        identities[str(path)] = file_identity(path, cap=8 * 1024**3)
        require(identities[str(path)][0] > 0, "ASR_CACHE_EMPTY_FILE")
    for name, digest in SPEAKER_HASHES.items():
        path = request.speaker_cache / name
        observed = file_identity(path, cap=2 * 1024**3)
        require(observed[0] > 1024 and observed[1] == digest, "SPEAKER_CACHE_HASH_MISMATCH")
        identities[str(path)] = observed
    return identities


def build_plan(request: Request, observation: Observation, *, now: float) -> Plan:
    """Validate observations and construct an inert plan, without making dirs."""
    _inputs(request, observation)
    deadline = _limits(request.limits, now)
    safe_path(request.parent, directory=True)
    scope = request.parent / ("pilot-" + request.run_id)
    require(not scope.exists(), "OUTPUT_SCOPE_ALREADY_EXISTS")
    for path in (request.clip.path, request.asr_model, request.speaker_cache, request.repo):
        require(not path.is_relative_to(request.parent) and not request.parent.is_relative_to(path),
                "OUTPUT_INPUT_SCOPE_OVERLAP")
    require(request.limits.admission_sha256 == admission_fingerprint(request), "ADMISSION_BINDING_MISMATCH")
    name = "ibm-video-pilot-" + request.run_id
    job_id = "pilot-" + request.run_id
    job = VideoJob(job_id=job_id, profile="bridge_lesson",
                   source={"kind": "local_path", "path": ROOT + "/media/clip.mp4"},
                   project="school-of-bridge", metadata={"pilot": "IBM_TECHNICAL_BUNDLE",
                   "original_drive_id": DRIVE_ID, "original_offset_seconds": START,
                   "clip_mapping_evidence_sha256": request.clip.evidence_sha256},
                   options={"max_duration_seconds": 70.0,
                            "max_source_bytes": max(1024**2, request.clip.size_bytes),
                            "chunk_seconds": 60, "frame_interval_seconds": 15})
    payload = asdict(job)
    mounts = [Mount(str(scope / leaf), ROOT + "/" + leaf, False)
              for leaf in ("spool", "output", "media", "model-cache")]
    # The speaker directory must itself be a mountpoint: otherwise its writable
    # parent could rename it, moving the child RO mounts off the expected path.
    mounts.append(Mount(str(scope / "model-cache/speaker"), SPEAKER, False))
    mounts += [Mount(str(request.clip.path), ROOT + "/media/clip.mp4", True),
               Mount(str(scope / "pilot.job.json"), ROOT + "/media/pilot.job.json", True),
               Mount(str(request.asr_model), MODEL, True)]
    mounts += [Mount(str(request.speaker_cache / name), SPEAKER + "/" + name, True)
               for name in sorted(SPEAKER_HASHES)]
    return Plan(name, scope, request.image_id, RUNNER_ARGV, tuple(sorted(environment().items())),
                tuple(mounts), deadline, encoded(payload), canonical_job_hash(job),
                request.limits.deadline_epoch - request.limits.stop_reserve_seconds)


def validate_plan(plan: Plan, request: Request, observation: Observation, *, now: float) -> None:
    """Exact equality rejects added argv, env, network, mount or output locators."""
    require(plan == build_plan(request, observation, now=now), "PLAN_ALLOWLIST_MISMATCH")


def parse_stdout(raw: bytes, *, max_bytes: int) -> tuple[dict, dict]:
    require(len(raw) <= max_bytes, "LOG_BYTES_LIMIT")
    lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
    require(len(lines) == 2, "TWO_SCHEMA_RECORDS_REQUIRED")
    ready, manifest = (strict_json(line) for line in lines)
    require(isinstance(ready, dict) and ready.get("schema") == "universal-video-container-readiness-v1"
            and ready.get("status") == "READY" and ready.get("fallback_used") is False,
            "READINESS_RECORD_INVALID")
    require(isinstance(manifest, dict) and manifest.get("contract") == CONTRACT_VERSION and
            manifest.get("status") in {"COMPLETED", "REVIEW"}, "MANIFEST_RECORD_INVALID")
    return ready, manifest


def _write_new(path: Path, value: bytes) -> None:
    safe_path(path.parent, directory=True)
    with path.open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


class _WriteGuard:
    """Single-owner sink: pre-write byte cap and cooperative clock checks.

    No guarded data write or fsync starts after an observed cutoff; cleanup
    still closes every opened stream. A blocked write/fsync/close
    cannot be interrupted here: the outer lifecycle deadline remains required.
    This is not a quota for native runner/ffmpeg writes or concurrent writers.
    """
    def __init__(self, max_bytes, deadline_epoch, clock, *, prefix):
        require(type(max_bytes) is int and max_bytes > 0, "INVALID_WRITE_CAP")
        require(type(deadline_epoch) in (int, float) and math.isfinite(deadline_epoch),
                "INVALID_WRITE_DEADLINE")
        self.maximum, self.used = max_bytes, 0
        self.deadline, self.clock, self.last = deadline_epoch, clock, None
        self.prefix = prefix

    def check(self):
        now = self.clock()
        require(type(now) in (int, float) and math.isfinite(now)
                and (self.last is None or now >= self.last), "INVALID_WRITE_CLOCK")
        self.last = now
        require(now < self.deadline, self.prefix + "_DEADLINE")
        return now

    def write(self, stream, raw):
        require(type(raw) is bytes and len(raw) <= self.maximum - self.used,
                self.prefix + "_BYTES_LIMIT")
        offset = 0
        while offset < len(raw):
            self.check()
            block = raw[offset:offset + 64 * 1024]
            written = stream.write(block)
            require(type(written) is int and 0 < written <= len(block), "SINK_SHORT_WRITE")
            self.used += written
            offset += written
            self.check()

    def sync(self, stream):
        self.check()
        os.fsync(stream.fileno())
        self.check()


def _bounded_json(value: object, maximum: int) -> bytes:
    # Bound the serialized receipt before opening a sink. This is a disk-byte
    # bound, not a bound on JSON encoder CPU or individual string allocations.
    result = bytearray()
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    for fragment in encoder.iterencode(value):
        raw = fragment.encode("utf-8")
        require(len(raw) <= maximum - len(result), "RECEIPT_BYTES_LIMIT")
        result.extend(raw)
    return bytes(result)


def _write_bounded_new(path: Path, raw: bytes, *, max_bytes: int,
                       deadline_epoch: float, clock) -> float:
    guard = _WriteGuard(max_bytes, deadline_epoch, clock, prefix="RECEIPT_WRITE")
    require(type(raw) is bytes and len(raw) <= max_bytes, "RECEIPT_BYTES_LIMIT")
    guard.check()
    safe_path(path.parent, directory=True)
    guard.check()
    # Caller owns the private parent; never replace a file or follow a leaf link.
    with path.open("xb", buffering=0) as stream:
        guard.write(stream, raw)
        guard.sync(stream)
    return guard.check()  # Includes close latency, not syscall cancellation.


def _inventory(plan: Plan, limits: Limits) -> dict[str, str]:
    inventory = tree_hashes(plan.scope / "output", max_bytes=limits.max_output_bytes,
                            max_files=limits.max_output_files)
    for relative in inventory:
        file_identity(plan.scope / "output" / relative, cap=limits.max_file_bytes)
    job_id = json.loads(plan.job_json)["job_id"]
    for parent, dirs, _files in os.walk(plan.scope / "output", followlinks=False):
        for name in dirs:
            relative = (Path(parent) / name).relative_to(plan.scope / "output").as_posix()
            require(relative in {job_id, job_id + "/frames"}, "UNDECLARED_OUTPUT_DIRECTORY")
    return inventory


def _scratch_layout() -> tuple[set[str], set[str]]:
    dirs = {"spool", "output", "media", "model-cache", "model-cache/asr", "model-cache/speaker"}
    dirs.update("spool/" + leaf for leaf in SPOOL_LEAVES)
    placeholders = {"media/clip.mp4", "media/pilot.job.json"}
    placeholders.update("model-cache/speaker/" + name for name in SPEAKER_HASHES)
    return dirs, placeholders


def _scratch_identity(plan: Plan) -> dict[str, tuple[int, int]]:
    dirs, placeholders = _scratch_layout()
    identities = {}
    for relative in sorted(dirs | placeholders | {""}):
        info = safe_path(plan.scope / relative, directory=relative in dirs or relative == "")
        identities[relative] = (info.st_dev, info.st_ino)
    return identities


def _check_scratch(plan: Plan, limits: Limits, identity: dict) -> dict[str, int]:
    """Post-run detection over EVERY writable mount, not runtime enforcement.

    Empty mount-target placeholders are deliberately created before the run.
    The real clip/job/model files are overlaid read-only in the container and
    are not writable scratch. No spool jobs or cache downloads are allowed.
    """
    require(_scratch_identity(plan) == identity, "SCRATCH_PATH_REPLACED")
    expected_dirs, placeholders = _scratch_layout()
    files = entries = total = 0
    for leaf in ("spool", "output", "media", "model-cache"):
        for parent, dirs, names in os.walk(plan.scope / leaf, followlinks=False):
            entries += len(dirs) + len(names)
            require(entries <= limits.max_output_files, "FILE_COUNT_LIMIT")
            for name in dirs:
                path = Path(parent) / name
                safe_path(path, directory=True)
                relative = path.relative_to(plan.scope).as_posix()
                require(leaf == "output" or relative in expected_dirs, "UNEXPECTED_SCRATCH_DIRECTORY")
            for name in names:
                path = Path(parent) / name
                info = safe_path(path)
                total += info.st_size
                files += 1
                require(info.st_size <= limits.max_file_bytes and total <= limits.max_output_bytes,
                        "FILE_BYTES_LIMIT")
                relative = path.relative_to(plan.scope).as_posix()
                require(leaf == "output" or (relative in placeholders and info.st_size == 0),
                        "UNEXPECTED_SCRATCH_FILE")
    return {"writable_bytes": total, "writable_files": files, "writable_entries": entries}


def _finish_bundle(plan: Plan, request: Request, execution: Execution) -> dict:
    limits = request.limits
    require(len(execution.stdout) + len(execution.stderr) <= limits.max_log_bytes, "LOG_BYTES_LIMIT")
    require(execution.exit_code == 0, "RUNNER_EXIT_NONZERO")
    ready, manifest = parse_stdout(execution.stdout, max_bytes=limits.max_log_bytes)
    require(ready.get("source_commit") == BASELINE, "READINESS_CODE_MISMATCH")
    expected_speaker = dict(zip(("segmentation_sha256", "embedding_sha256"), SPEAKER_HASHES.values()))
    require(ready.get("speaker_models") == expected_speaker, "READINESS_MODEL_MISMATCH")
    expected_mounts = sorted([ROOT + "/spool", ROOT + "/output", ROOT + "/media",
                              ROOT + "/model-cache", SPEAKER])
    require(ready.get("mounts") == expected_mounts, "READINESS_MOUNTS_MISMATCH")
    require(manifest.get("status") == "COMPLETED", "NATIVE_REVIEW_NOT_SUCCESS")
    job_id = "pilot-" + request.run_id
    bundle = plan.scope / "output" / job_id
    before = _inventory(plan, limits)
    disk = strict_json((bundle / "manifest.json").read_text(encoding="utf-8"))
    require(disk == manifest, "STDOUT_DISK_MANIFEST_MISMATCH")
    media = manifest.get("media", {})
    require(media.get("sha256") == request.clip.sha256 and media.get("size_bytes") == request.clip.size_bytes
            and media.get("duration_seconds") == request.clip.duration_seconds, "RESULT_SOURCE_MISMATCH")
    require(manifest.get("source", {}).get("kind") == "local_path" and
            manifest["source"].get("path") == ROOT + "/media/clip.mp4", "RESULT_SOURCE_PATH_MISMATCH")
    require(manifest.get("processing_revision") == BASELINE and
            manifest.get("processing_whisper_model") == MODEL, "RESULT_CODE_MODEL_MISMATCH")
    require(bool(manifest.get("frames")), "FRAME_EVIDENCE_REQUIRED")
    proof = verify_result(bundle, expected_job_id=job_id, expected_profile="bridge_lesson",
                          expected_job_hash=plan.job_hash, max_file_bytes=limits.max_file_bytes,
                          max_total_bytes=limits.max_output_bytes, evidence_phase="GENERATION_FINALIZATION")
    require(proof.get("state") == "PASS", "NATIVE_CONFORMANCE_FAILED")
    expected_files = {job_id + "/" + item["relative_name"] for item in proof["artifacts"]}
    require(set(before) == expected_files, "UNDECLARED_OUTPUT_ARTIFACT")
    rows = [strict_json(line) for line in (bundle / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    speaker = strict_json((bundle / "speaker_diarization.json").read_text(encoding="utf-8"))
    roles = verify_speaker_roles(rows, speaker)
    require(before == _inventory(plan, limits), "OUTPUT_RACE")
    return {"native_conformance": proof, "role_verifier": roles,
            "roles": roles["role_map"] if roles["status"] == "PASS" else "UNKNOWN",
            "person_identity": "NOT_INFERRED", "output_hashes": before}


def run_pilot(request: Request, *, executor: Executor, clock=time.time) -> dict:
    """One attempt, no retries. SYNTHETIC success is never a live IBM PASS.

    The durable attempt key is source+interval (not run_id or clip encoding).
    UNKNOWN/missing final receipt therefore forbids another heavy attempt in
    the same dedicated parent. Changing the parent is not a retry mechanism.
    """
    require(executor.synthetic is True, "LIVE_EXECUTION_BLOCKED:" + ",".join(LIVE_GAPS))
    safe_path(request.parent, directory=True)
    attempt = request.parent / (".ibm-video-" + fingerprint([DRIVE_ID, START, END]) + ".attempt.json")
    # Reject replay before rehashing large model files or invoking the executor.
    require(not attempt.exists(), "DUPLICATE_OR_UNKNOWN_ATTEMPT")
    observation = executor.observe(request)
    require(observation.synthetic is True, "SYNTHETIC_EXECUTOR_REQUIRED")
    admitted_at = clock()
    plan = build_plan(request, observation, now=admitted_at)
    identities = _inputs(request, observation)
    _write_new(attempt, encoded({"schema": "ibm-video-pilot-attempt-v1", "state": "UNKNOWN",
                               "run_id": request.run_id, "retry_allowed": False,
                               "scope": str(plan.scope), "synthetic": True}))
    plan.scope.mkdir(exist_ok=False)
    receipt = {"schema": "ibm-video-pilot-receipt-v1", "state": "UNKNOWN", "synthetic": True,
               "live_admitted": False, "live_gaps": list(LIVE_GAPS), "retry_allowed": False,
               "run_id": request.run_id, "result_scope": "IBM_TECHNICAL_BUNDLE",
               "source": {**asdict(request.clip), "path": str(request.clip.path)},
               "original_offset_seconds": START, "clip_mapping": {"clip": [0, 70], "original": [START, END]},
               "code_commit": BASELINE, "code_hashes": dict(request.code_hashes),
               "image_id": request.image_id, "model_hashes": dict(request.model_hashes),
               "speaker_model_hashes": dict(SPEAKER_HASHES), "limits": asdict(request.limits),
               "resources": {"cpus": 4, "memory_bytes": 8 * 1024**3, "pids": 512, "tmp_bytes": 1024**3},
               "costs": {"actual_usd": None, "status": "NOT_OBSERVED_SYNTHETIC",
                         "budget_ceiling_usd": request.limits.budget_usd},
               "stages": {stage: "BLOCKED" for stage in resolve_profile("bridge_lesson").stages},
               "delivery": "UNVERIFIED_RETAIN_ALL", "cleanup": "NOT_ATTEMPTED",
               "export": {"status": "DEFERRED", "deadline_epoch": plan.export_deadline_epoch,
                          "reserved_seconds": request.limits.export_reserve_seconds},
               "roles": "UNKNOWN", "person_identity": "NOT_INFERRED"}
    receipt["stages"].update({stage: "DEFERRED" for stage in DOWNSTREAM})
    execution = None
    observed_elapsed = peak_memory = None
    last_clock = admitted_at

    def sample_clock():
        nonlocal last_clock
        value = clock()
        require(type(value) in (int, float) and math.isfinite(value) and value >= last_clock,
                "INVALID_OR_BACKWARD_CLOCK")
        last_clock = value
        return value

    started = None
    try:
        started = sample_clock()
        dirs, placeholders = _scratch_layout()
        for leaf in sorted(dirs, key=lambda name: (name.count("/"), name)):
            (plan.scope / leaf).mkdir()
        for relative in sorted(placeholders):
            _write_new(plan.scope / relative, b"")
        scratch_identity = _scratch_identity(plan)
        _write_new(plan.scope / "pilot.job.json", plan.job_json)
        job_identity = file_identity(plan.scope / "pilot.job.json", cap=256 * 1024)
        require(identities == _inputs(request, executor.observe(request)), "INPUT_RACE")
        require(sample_clock() < plan.compute_deadline_epoch, "DEADLINE_BEFORE_START")
        execution = executor.execute_one(plan, request.limits)
        require(isinstance(execution, Execution), "LOST_EXECUTION_RECEIPT")
        require(execution.created_new is True and execution.owner == plan.name and
                isinstance(execution.container_id, str) and HEX64.fullmatch(execution.container_id) is not None,
                "CONTAINER_OWNERSHIP_UNPROVED")
        require(execution.stopped is True, "CONTAINER_STOP_UNPROVED")
        require(sample_clock() <= plan.compute_deadline_epoch, "COMPUTE_DEADLINE_EXCEEDED")
        require(type(execution.elapsed_seconds) in (int, float) and math.isfinite(execution.elapsed_seconds)
                and 0 <= execution.elapsed_seconds <= plan.compute_deadline_epoch - started,
                "EXECUTION_TIMING_INVALID")
        observed_elapsed = execution.elapsed_seconds
        require(type(execution.peak_memory_bytes) is int and
                0 <= execution.peak_memory_bytes <= 8 * 1024**3, "RESOURCE_LIMIT_EXCEEDED")
        peak_memory = execution.peak_memory_bytes
        require(identities == _inputs(request, executor.observe(request)), "INPUT_RACE")
        require(job_identity == file_identity(plan.scope / "pilot.job.json", cap=256 * 1024), "JOB_RACE")
        receipt["scratch_accounting"] = _check_scratch(plan, request.limits, scratch_identity)
        receipt.update(_finish_bundle(plan, request, execution))
        require(receipt["scratch_accounting"] == _check_scratch(plan, request.limits, scratch_identity),
                "SCRATCH_RACE")
        receipt["stages"].update({stage: "EXECUTED" for stage in RUNNER_EXECUTED_STAGES
                                  if stage in receipt["stages"]})
        receipt["state"] = "SYNTHETIC_TECHNICAL_BUNDLE"
    except Exception as exc:
        # A stopped failure is still not permission to rerun; unknown paths are
        # retained for inspection. Never persist arbitrary exception text/logs.
        receipt["error_code"] = str(exc) if isinstance(exc, PilotBlocked) else type(exc).__name__
    finally:
        if isinstance(execution, Execution):
            # Never copy unvalidated executor metrics (NaN/object/etc.) into a
            # durable UNKNOWN receipt: JSON serialization must remain possible.
            receipt["peak_memory_bytes"] = peak_memory
            if (execution.created_new is True and execution.owner == plan.name and execution.stopped is True
                    and isinstance(execution.container_id, str) and HEX64.fullmatch(execution.container_id)):
                receipt["container_id"] = execution.container_id
                try:
                    executor.remove_owned(execution.container_id, plan.name)
                    receipt["cleanup"] = "EXACT_OWNED_CONTAINER_REMOVED"
                except Exception:
                    receipt["cleanup"] = "UNKNOWN_RETAIN_RESULTS"
                    receipt["state"] = "UNKNOWN"
        # Verification/cleanup are local preparation, not external delivery.
        # They must not consume the export window while delivery is unverified.
        finalization_deadline = plan.compute_deadline_epoch
        try:
            finished = sample_clock()
        except Exception:
            finished = None
            receipt["state"], receipt["error_code"] = "UNKNOWN", "INVALID_FINAL_CLOCK"
        if finished is not None and finished > finalization_deadline:
            receipt["state"] = "UNKNOWN"
            receipt.setdefault("error_code", "FINALIZATION_DEADLINE_EXCEEDED")
        receipt["timings"] = {"started_epoch": started, "finished_epoch": finished,
                              "compute_deadline_epoch": plan.compute_deadline_epoch,
                              "finalization_deadline_epoch": finalization_deadline,
                              "export_deadline_epoch": plan.export_deadline_epoch,
                              "stop_deadline_epoch": request.limits.deadline_epoch,
                              "observed_elapsed_seconds": observed_elapsed,
                              "receipt_persisted_observed_epoch": None}
        cap = min(MAX_RECEIPT_BYTES, request.limits.max_file_bytes)
        receipt["receipt_persistence"] = {"status": "CANDIDATE_ONLY", "byte_cap": cap,
                                          "deadline_enforcement": "COOPERATIVE_ONLY",
                                          "restart_acceptance": "EXTERNAL_VERIFIER_REQUIRED"}
        # Even fully written bytes are NOT a restart-readable deadline PASS:
        # fsync/close can stall after the last check. Never put an authoritative
        # success marker on disk; the existing durable UNKNOWN attempt survives.
        candidate = {**receipt, "state": "UNKNOWN", "candidate_state": receipt["state"]}
        try:
            raw = _bounded_json(candidate, cap)
            receipt["timings"]["receipt_persisted_observed_epoch"] = _write_bounded_new(
                plan.scope / "adapter-receipt.json", raw, max_bytes=cap,
                deadline_epoch=finalization_deadline, clock=sample_clock)
        except Exception as exc:
            receipt["state"] = "UNKNOWN"
            receipt.setdefault("error_code", str(exc) if isinstance(exc, PilotBlocked) else type(exc).__name__)
            receipt["receipt_persistence"]["status"] = "UNCONFIRMED_RETAIN_UNKNOWN_ATTEMPT"
    return receipt


# The functions below are preparation/data primitives for the EXISTING lifecycle
# owner. They never create/start/stop/remove a container or touch a provider API.
# This Windows/offline revision has not exercised Linux enforcement or the
# daemon-side mount handoff. run_pilot remains synthetic-only.
BLOCKED_LINK_SYSCALLS = frozenset({"link", "linkat", "symlink", "symlinkat",
                                 "io_uring_setup", "io_uring_enter", "io_uring_register"})
HOST_ENV = {"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "MKE2FS_CONFIG": "/dev/null"}


def tightened_seccomp(raw: bytes, expected_sha256: str) -> bytes:
    """Only remove ALLOW entries from a pinned existing default-deny profile.

    No unconfined/allow-all replacement and no daemon-wide configuration edit.
    Includes/excludes and architecture conditions remain byte-value equivalent.
    Unknown profile/rule shapes fail closed instead of silently dropping policy.
    """
    require(len(raw) <= 1024**2 and hashlib.sha256(raw).hexdigest() == expected_sha256,
            "SECCOMP_BASELINE_HASH_MISMATCH")
    profile = strict_json(raw.decode("utf-8"))
    require(isinstance(profile, dict) and profile.get("defaultAction") == "SCMP_ACT_ERRNO"
            and set(profile) <= {"defaultAction", "defaultErrnoRet", "architectures", "archMap", "syscalls"}
            and isinstance(profile.get("syscalls"), list) and bool(profile["syscalls"]),
            "SECCOMP_BASELINE_SHAPE")
    rules = []
    for rule in profile["syscalls"]:
        require(isinstance(rule, dict) and set(rule) <= {"names", "action", "args", "comment", "includes", "excludes", "errnoRet"}
                and rule.get("action") in {"SCMP_ACT_ALLOW", "SCMP_ACT_ERRNO"}
                and isinstance(rule.get("names"), list) and bool(rule["names"])
                and all(isinstance(n, str) and re.fullmatch(r"[a-z0-9_]+", n) for n in rule["names"]),
                "SECCOMP_RULE_SHAPE")
        # Leave existing denial rules intact; only narrow permission grants.
        names = [n for n in rule["names"] if n not in BLOCKED_LINK_SYSCALLS] if rule["action"] == "SCMP_ACT_ALLOW" else rule["names"]
        if names:
            rules.append({**rule, "names": names})
    return encoded({**profile, "syscalls": rules})


def _linux_root_only() -> None:
    # ioctl encodings below are intentionally qualified only for Linux x86-64.
    require(sys.platform == "linux" and os.uname().machine == "x86_64" and os.geteuid() == 0,
            "LINUX_X86_64_ROOT_REQUIRED")


def _private_root_ancestry(path: Path) -> None:
    safe_path(path, directory=True)
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o022, "MUTABLE_HOST_ANCESTRY")


def _verity(fd: int, *, enable: bool) -> str:
    import fcntl  # Windows can import the module; only a Linux owner calls this.
    if enable:
        # struct fsverity_enable_arg: v1, SHA256, 4096-byte blocks, no salt/signature.
        fcntl.ioctl(fd, 0x40806685, struct.pack("=IIIIQIIQ11Q", 1, 1, 4096, 0, 0, 0, 0, 0, *([0] * 11)))
    measured = bytearray(struct.pack("=HH", 0, 32) + bytes(32))
    fcntl.ioctl(fd, 0xC0046686, measured, True)
    algorithm, size = struct.unpack_from("=HH", measured)
    require(algorithm == 1 and size == 32, "VERITY_MEASUREMENT_INVALID")
    return bytes(measured[4:]).hex()  # Merkle file digest, NOT the ordinary SHA256.


def _fd_sha256(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    for chunk in iter(lambda: os.read(fd, 1024**2), b""):
        digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SealedInput:
    path: Path
    fd: int
    device: int
    inode: int
    size: int
    sha256: str
    verity_digest: str

    def check(self) -> None:
        _private_root_ancestry(self.path.parent)
        info, opened = safe_path(self.path), os.fstat(self.fd)
        require((info.st_dev, info.st_ino, info.st_size) ==
                (opened.st_dev, opened.st_ino, opened.st_size) ==
                (self.device, self.inode, self.size), "SEALED_INPUT_PATH_REPLACED")
        require(info.st_uid == 0 and not info.st_mode & 0o222, "SEALED_INPUT_WRITABLE")
        require(_verity(self.fd, enable=False) == self.verity_digest, "VERITY_CHANGED")


def _copy_sealed(source: Path, target: Path, expected_sha: str, *, deadline: float, clock) -> SealedInput:
    before = file_identity(source, cap=8 * 1024**3)
    require(before[1] == expected_sha, "SNAPSHOT_SOURCE_HASH")
    _private_root_ancestry(target.parent)
    # Exclusive regular copy, never a hardlink/reflink or writable alias.
    source_info = safe_path(source)
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(source_fd, "rb") as reader, target.open("xb") as writer:
        opened = os.fstat(reader.fileno())
        require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
                (opened.st_dev, opened.st_ino) == (source_info.st_dev, source_info.st_ino), "SNAPSHOT_SOURCE_RACE")
        copied = 0
        for block in iter(lambda: reader.read(1024**2), b""):
            require(clock() < deadline, "PREPARATION_DEADLINE")
            copied += len(block)
            require(copied <= before[0], "SNAPSHOT_SOURCE_GREW")
            writer.write(block)
        writer.flush()
        os.fsync(writer.fileno())
    require(file_identity(source, cap=8 * 1024**3) == before, "SNAPSHOT_SOURCE_RACE")
    fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        measured = _verity(fd, enable=True)  # EOPNOTSUPP/ENOTTY is a blocker, no fallback.
        require(_fd_sha256(fd) == expected_sha, "SNAPSHOT_SEALED_HASH")
        os.chmod(target, 0o444)
        info = os.fstat(fd)
        require(info.st_size == before[0] and info.st_nlink == 1, "SNAPSHOT_COPY_INVALID")
        return SealedInput(target, fd, info.st_dev, info.st_ino, info.st_size, expected_sha, measured)
    except BaseException:
        os.close(fd)
        raise


def _allocate_image(path: Path, size: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.posix_fallocate(fd, 0, size)
        os.fsync(fd)
    finally:
        os.close(fd)


def _mounted_capacity(mountpoint: Path, image: Path) -> tuple[int, int, int]:
    """Read kernel mount identity and its exact loop backing file, not CLI prose."""
    rows = Path("/proc/self/mountinfo").read_text().splitlines()
    matches = []
    for row in rows:
        left, right = row.split(" - ", 1)
        fields, fs = left.split(), right.split()
        target = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
        if target == str(mountpoint):
            matches.append((fields, fs))
    require(len(matches) == 1, "OUTPUT_MOUNT_NOT_UNIQUE")
    fields, fs = matches[0]
    require(fields[3] == "/" and {"rw", "nosuid", "nodev", "noexec"} <= set(fields[5].split(","))
            and fs[0] == "ext4" and re.fullmatch(r"/dev/loop[0-9]+", fs[1]), "OUTPUT_MOUNT_INVALID")
    backing = Path("/sys/dev/block") / fields[2] / "loop/backing_file"
    actual = backing.read_text().strip()
    require(Path("/" + actual.lstrip("/")) == image, "OUTPUT_BACKING_FILE_MISMATCH")
    info, capacity = mountpoint.stat(), os.statvfs(mountpoint)
    require(fields[2] == f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}", "OUTPUT_MOUNT_DEVICE_MISMATCH")
    return info.st_dev, capacity.f_files, capacity.f_blocks * capacity.f_frsize


@dataclass(frozen=True)
class LinuxPrepared:
    """Owned local storage only. The caller retains ALL lifecycle authority.

    Keep pins open through the owner's stopped-container confirmation; call
    close_pins afterward. No automatic deletion/unmount/export/Stop exists here.
    A pathname is not an fd-to-Docker proof: require a same-host trusted daemon,
    private root ancestry, single owner and actual mount handoff qualification.
    """
    plan: Plan
    control_scope: Path
    image: Path
    image_identity: tuple[int, int]
    device: int
    inode_capacity: int
    filesystem_bytes: int
    file_bytes: int
    seccomp: Path
    pins: tuple[SealedInput, ...]
    scratch_identity: dict

    def check(self) -> None:
        _linux_root_only()
        _private_root_ancestry(self.control_scope)
        info = safe_path(self.image)
        require((info.st_dev, info.st_ino) == self.image_identity, "OUTPUT_IMAGE_REPLACED")
        require(info.st_uid == 0 and not info.st_mode & 0o077, "OUTPUT_IMAGE_NOT_PRIVATE")
        require(_mounted_capacity(self.plan.scope, self.image) ==
                (self.device, self.inode_capacity, self.filesystem_bytes), "OUTPUT_CAPACITY_CHANGED")
        identities = _scratch_identity(self.plan)
        require(identities == self.scratch_identity, "SCRATCH_PATH_REPLACED")
        require(all(device == self.device for device, _ in identities.values()),
                "SCRATCH_OUTSIDE_BOUNDED_FILESYSTEM")
        # This is a pre-launch check: all exposed writable leaves must still be
        # the empty layout we created. ext4's root lost+found is not exposed.
        expected_dirs, placeholders = _scratch_layout()
        for leaf in ("spool", "output", "media", "model-cache"):
            for parent, dirs, files in os.walk(self.plan.scope / leaf, followlinks=False):
                for name in dirs:
                    path = Path(parent) / name
                    info = safe_path(path, directory=True)
                    require(info.st_dev == self.device, "SCRATCH_OUTSIDE_BOUNDED_FILESYSTEM")
                    require(path.relative_to(self.plan.scope).as_posix() in expected_dirs,
                            "UNEXPECTED_INITIAL_SCRATCH_DIRECTORY")
                for name in files:
                    path = Path(parent) / name
                    info = safe_path(path)
                    require(info.st_dev == self.device, "SCRATCH_OUTSIDE_BOUNDED_FILESYSTEM")
                    require(path.relative_to(self.plan.scope).as_posix() in placeholders and info.st_size == 0,
                            "UNEXPECTED_INITIAL_SCRATCH_FILE")
        for pin in self.pins:
            pin.check()
        snapshot = self.control_scope / "inputs"
        actual = set()
        for parent, dirs, files in os.walk(snapshot, followlinks=False):
            for name in dirs:
                _private_root_ancestry(Path(parent) / name)
            actual.update(Path(parent) / name for name in files)
        require(actual == {pin.path for pin in self.pins}, "SNAPSHOT_TREE_CHANGED")

    def docker_create_argv(self, *, clock=time.time) -> tuple[str, ...]:
        self.check()
        require(clock() < self.plan.compute_deadline_epoch, "DEADLINE_BEFORE_START")
        args = self.plan.docker_create_argv()
        # Insert before the immutable image ID, preserving the exact native CMD.
        split = len(args) - len(RUNNER_ARGV) - 1
        prefix = tuple(a.replace("size=1g,mode=1777", "size=1g,nr_inodes=512,mode=1777") for a in args[:split])
        return prefix + (f"--ulimit=fsize={self.file_bytes}:{self.file_bytes}", "--ulimit=core=0:0",
                         "--ipc=none", "--security-opt=seccomp=" + str(self.seccomp)) + args[split:]

    def evidence(self) -> dict:
        return {"schema": "ibm-video-linux-enforcement-v1", "host_qualification": "NOT_TESTED",
                "docker_mount_handoff": "NOT_TESTED", "live_admitted": False,
                "filesystem_bytes": self.filesystem_bytes, "inode_capacity": self.inode_capacity,
                "per_file_bytes": self.file_bytes, "max_regular_logical_bytes": self.inode_capacity * self.file_bytes,
                "tmp_bytes": 1024**3, "tmp_inodes": 512, "dev_shm": "NOT_MOUNTED_IPC_NONE",
                "inputs": [{"path": str(p.path), "sha256": p.sha256, "verity_digest": p.verity_digest,
                            "device": p.device, "inode": p.inode, "bytes": p.size} for p in self.pins],
                "retention": "KEEP_FILESYSTEM_IMAGE_INPUTS_AND_RECEIPTS_UNTIL_VERIFIED_DELIVERY"}

    def close_pins(self) -> None:
        for pin in self.pins:
            os.close(pin.fd)


def prepare_linux_enforcement(request: Request, observation: Observation, *, default_seccomp: Path,
                              default_seccomp_sha256: str, run_host, clock=time.time) -> LinuxPrepared:
    """Executable local preparation, called under the existing owner's deadline.

    run_host(argv, *, env, deadline_epoch, max_output_bytes) -> bytes must execute
    the exact tuple with no shell and raise on nonzero/timeout/overflow. Only two
    fixed local commands are submitted: mkfs of the new owned regular image and
    mount of that image. Nothing is installed, and no existing device is formatted.
    The owner's outer deadline MUST also cover copying and fs-verity ioctls.
    All unsupported/missing host mechanisms block. Tested only with Linux mocks.
    """
    _linux_root_only()
    _private_root_ancestry(request.parent)
    attempt = request.parent / (".ibm-video-" + fingerprint([DRIVE_ID, START, END]) + ".attempt.json")
    require(not attempt.exists(), "DUPLICATE_OR_UNKNOWN_ATTEMPT")
    plan = build_plan(request, observation, now=clock())
    require(16 * 1024**2 <= request.limits.max_output_bytes and request.limits.max_output_files >= 32,
            "LINUX_FILESYSTEM_CAP_TOO_SMALL")
    file_identity(default_seccomp, cap=1024**2)
    with default_seccomp.open("rb") as source:
        raw = source.read(1024**2 + 1)
    policy = tightened_seccomp(raw, default_seccomp_sha256)
    _write_new(attempt, encoded({"schema": "ibm-video-pilot-attempt-v1", "state": "UNKNOWN",
                               "run_id": request.run_id, "scope": str(plan.scope), "retry_allowed": False}))
    plan.scope.mkdir(mode=0o700)
    control, snapshot, bounded = plan.scope, plan.scope / "inputs", plan.scope / "bounded"
    snapshot.mkdir(mode=0o700)
    bounded.mkdir(mode=0o700)
    image = control / "output.ext4"
    pins = []
    try:
        _allocate_image(image, request.limits.max_output_bytes)
        require(safe_path(image).st_size == request.limits.max_output_bytes,
                "OUTPUT_IMAGE_SIZE")
        info = image.stat()
        image_identity = (info.st_dev, info.st_ino)
        commands = (("/usr/sbin/mkfs.ext4", "-F", "-q", "-b", "4096", "-I", "256", "-N",
                     str(request.limits.max_output_files), "-m", "0", "-O", "none,extent,filetype,has_journal", str(image)),
                    ("/usr/bin/mount", "-t", "ext4", "-o", "loop,rw,nosuid,nodev,noexec", str(image), str(bounded)))
        for argv in commands:
            require(clock() < plan.compute_deadline_epoch, "PREPARATION_DEADLINE")
            run_host(argv, env=dict(HOST_ENV), deadline_epoch=plan.compute_deadline_epoch, max_output_bytes=16 * 1024)
        device, inodes, fs_bytes = _mounted_capacity(bounded, image)
        require(0 < inodes <= request.limits.max_output_files and 0 < fs_bytes <= request.limits.max_output_bytes,
                "ACTUAL_FILESYSTEM_CAP_EXCEEDS_ADMISSION")
        fsize = min(request.limits.max_file_bytes, request.limits.max_output_bytes // inodes)
        # ASR uses 60-second chunks, but speaker v3 extracts the entire clip as
        # 16kHz mono PCM16 WAV. Cover all 70 seconds plus a small header margin;
        # this minimum is not a frame-size, storage-sufficiency or speed proof.
        require(fsize >= 70 * 16000 * 2 + 4096, "FILE_CAP_TOO_SMALL_FOR_NATIVE_WAV")
        dirs, placeholders = _scratch_layout()
        for relative in sorted(dirs, key=lambda p: (p.count("/"), p)):
            path = bounded / relative
            path.mkdir(mode=0o750)
            os.chown(path, 10001, 10001)
        for relative in sorted(placeholders):
            _write_new(bounded / relative, b"")
        _write_new(control / "pilot.job.json", plan.job_json)
        _write_new(control / "seccomp.source.json", policy)
        sources = [(request.clip.path, "clip.mp4", request.clip.sha256),
                   (control / "pilot.job.json", "pilot.job.json", hashlib.sha256(plan.job_json).hexdigest()),
                   (control / "seccomp.source.json", "seccomp.json", hashlib.sha256(policy).hexdigest())]
        sources += [(request.asr_model / name, "asr/" + name, digest) for name, digest in request.model_hashes]
        sources += [(request.speaker_cache / name, "speaker/" + name, digest) for name, digest in sorted(SPEAKER_HASHES.items())]
        for source, relative, digest in sources:
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            pins.append(_copy_sealed(source, target, digest, deadline=plan.compute_deadline_epoch, clock=clock))
        # Ancestors cannot be changed by the container or another unprivileged
        # host process. Trusted root/daemon and single ownership remain explicit.
        for parent, dirs, _ in os.walk(snapshot, topdown=False):
            os.chmod(parent, 0o555 if Path(parent) != snapshot else 0o500)
        mounts = [Mount(str(bounded / leaf), ROOT + "/" + leaf, False)
                  for leaf in ("spool", "output", "media", "model-cache")]
        mounts.append(Mount(str(bounded / "model-cache/speaker"), SPEAKER, False))
        mounts += [Mount(str(snapshot / "clip.mp4"), ROOT + "/media/clip.mp4", True),
                   Mount(str(snapshot / "pilot.job.json"), ROOT + "/media/pilot.job.json", True),
                   Mount(str(snapshot / "asr"), MODEL, True)]
        mounts += [Mount(str(snapshot / "speaker" / name), SPEAKER + "/" + name, True) for name in sorted(SPEAKER_HASHES)]
        guarded = replace(plan, scope=bounded, mounts=tuple(mounts))
        prepared = LinuxPrepared(guarded, control, image, image_identity, device, inodes, fs_bytes, fsize,
                                 snapshot / "seccomp.json", tuple(pins), _scratch_identity(guarded))
        prepared.check()
        require(clock() < plan.compute_deadline_epoch, "PREPARATION_DEADLINE")
        _write_new(control / "linux-enforcement.json", encoded(prepared.evidence()))
        return prepared
    except BaseException:
        for pin in pins:
            os.close(pin.fd)
        # Leave marker, image and any mounted filesystem to the existing owner.
        # Never retry preparation, unmount unknown targets or delete evidence.
        raise


def capture_bounded_logs(stdout_fd: int, stderr_fd: int, destination: Path, *, max_bytes: int,
                         deadline_epoch: float, clock=time.time) -> tuple[bytes, bytes]:
    """Drain both pipes concurrently, persist a bounded prefix, signal overflow.

    The caller owns the pipes, container, deadline and Stop response. On any
    exception its existing finally/containment path must run; never retry work.
    Saved prefix remains on error; its fsync durability may be unconfirmed.
    In-flight blocking syscalls still need the owner's external deadline.
    """
    require(type(max_bytes) is int and 0 < max_bytes <= 4 * 1024**2 and stdout_fd != stderr_fd
            and all(type(fd) is int and fd >= 0 for fd in (stdout_fd, stderr_fd)),
            "INVALID_LOG_CAPTURE")
    safe_path(destination, directory=True)
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    files = {}
    guard = _WriteGuard(max_bytes, deadline_epoch, clock, prefix="LOG_CAPTURE")
    try:
        with selectors.DefaultSelector() as selector:
            for fd, name in ((stdout_fd, "stdout"), (stderr_fd, "stderr")):
                guard.check()
                files[name] = (destination / (name + ".log")).open("xb", buffering=0)
                selector.register(fd, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline_epoch - guard.check()
                for key, _events in selector.select(min(1.0, remaining)):
                    guard.check()  # select may wake at or after the cutoff.
                    room = max_bytes - sum(map(len, captured.values()))
                    chunk = os.read(key.fd, min(64 * 1024, room + 1))
                    guard.check()  # Never write bytes obtained after the cutoff.
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    prefix = chunk[:room]
                    guard.write(files[key.data], prefix)
                    captured[key.data].extend(prefix)
                    require(len(chunk) <= room, "LOG_BYTES_LIMIT")
        for stream in files.values():
            guard.sync(stream)
    finally:
        close_error = None
        for stream in files.values():
            try:
                stream.close()
            except BaseException as exc:
                close_error = close_error or exc
        if close_error is not None:
            raise close_error
    guard.check()
    return bytes(captured["stdout"]), bytes(captured["stderr"])


if __name__ == "__main__":
    print(json.dumps({"schema": "ibm-video-pilot-v1", "status": "BLOCKED",
                      "live_gaps": LIVE_GAPS, "instruction": "Use an injected synthetic executor in offline tests."}))
    raise SystemExit(2)
