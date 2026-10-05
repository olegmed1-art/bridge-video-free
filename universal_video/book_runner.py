"""Bounded PDF reproduction primitives. New review implementation v4.

Only qualified operator bindings may call the resident Drive transport or the
Linux sandbox. The result is private staging evidence, never a WORLD publish
receipt. No dependency installation or alternate executor fallback exists.
"""
import hashlib
import os
from pathlib import Path
import shutil
import signal
import stat
import struct
import zlib
import subprocess
import sys
import time

from bridge_contracts.book_material import digest, validate_bundle
from .book_contract import (
    BookJobError, INPUT_NAMES, digest_field, exact_fields, require,
    strict_json, validate_book_job,
)
from .drive_adapter import access_token, download_file, file_metadata, original_snapshot
from .drive_results import PublishArtifact, artifact_set_sha256
from .durable_drive import SOURCE_RECEIPT, atomic_json, read_receipt


def regular(path, maximum):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= maximum,
            "unsafe or oversized regular file")
    return info


def file_digest(path, maximum=64 * 1024 * 1024):
    regular(path, maximum)
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def json_file(path):
    regular(path, 1024 * 1024)
    value = strict_json(path.read_text(encoding="utf-8"))
    require(type(value) is dict, "JSON object required")
    return value


def safe_directory(path, create=False):
    require(not path.is_symlink(), "unsafe directory link")
    if create:
        path.mkdir(mode=0o750, exist_ok=True)
    require(path.is_dir(), "directory unavailable")



def _no_acl(fd):
    # POSIX ACLs can grant write independently of the apparent owner/mode.
    require(not any(name in {"system.posix_acl_access", "system.posix_acl_default"}
                    for name in os.listxattr(fd)), "unreviewed filesystem ACL")


def protected_read(path, maximum=1024 * 1024):
    """Root-owned immutable file, anchored one component at a time to open FDs."""
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "protected absolute path required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            info = os.fstat(fd)
            require(info.st_uid == 0 and not info.st_mode & 0o022,
                    "protected ancestor ownership/mode")
            _no_acl(fd)
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if index < len(path.parts) - 2:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
                and not before.st_mode & 0o022 and before.st_size <= maximum,
                "protected file ownership/mode/size")
        _no_acl(fd)
        blocks, count = [], 0
        while True:
            block = os.read(fd, min(65536, maximum + 1 - count))
            if not block:
                break
            count += len(block)
            require(count <= maximum, "protected read cap")
            blocks.append(block)
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size,
                                        after.st_mtime_ns, after.st_ctime_ns),
                "protected file changed during read")
        return b"".join(blocks)
    finally:
        os.close(fd)


def private_directory(path, *, persistent=False):
    """Worker-only namespaces; inbox writers cannot replace an ancestor."""
    path = Path(path)
    require(path.is_absolute(), "private absolute path required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            info = os.fstat(fd)
            require(info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022,
                    "private ancestor ownership/mode")
            _no_acl(fd)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        require(info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022,
                "private directory ownership/mode")
        _no_acl(fd)
        if persistent:
            require(_filesystem(fd)[0] in {0xef53, 0x58465342},
                    "durable ledger requires persistent ext4/xfs")
        return info.st_dev
    finally:
        os.close(fd)


def _filesystem(fd):
    import ctypes
    # Linux statfs has long-sized fields; use oversized aligned storage so no
    # ABI tail is truncated. Only f_type is read; capacity comes from fstatvfs.
    words = (ctypes.c_long * 32)()
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.fstatfs(fd, ctypes.byref(words)) == 0, "filesystem proof unavailable")
    volume = os.fstatvfs(fd)
    return words[0] & 0xffffffff, volume.f_blocks * volume.f_frsize


def sandbox_tmp_bytes(job):
    return 8 * 1024 * 1024


def resource_boundaries(job, media_root, results_root, progress_root):
    """Observe enforced tree-wide limits and finite mounts; never configure them."""
    private_directory(progress_root, persistent=True)
    capacities = {}
    for root in (media_root, results_root):
        device = private_directory(root)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            kind, capacity = _filesystem(fd)
            require(kind == 0x01021994 and 0 < capacity <= 512 * 1024 * 1024,
                    "bounded tmpfs workspace required; other quota is unqualified")
            capacities[device] = capacity
        finally:
            os.close(fd)
    require(sum(capacities.values()) + sandbox_tmp_bytes(job) + 1024 * 1024
            <= job.payload["limits"]["workspace_mib"] * 1024 * 1024,
            "aggregate workspace mount budget")
    return {**cgroup_boundaries(job), "workspace_capacities": sorted(capacities.values())}


def cgroup_boundaries(job):
    rows = Path("/proc/self/cgroup").read_text().splitlines()
    unified = [row[3:] for row in rows if row.startswith("0::")]
    require(len(unified) == 1 and unified[0].startswith("/")
            and ".." not in Path(unified[0]).parts and unified[0] != "/",
            "dedicated cgroup-v2 required")
    group = Path("/sys/fs/cgroup") / unified[0].lstrip("/")
    def number(name):
        raw = protected_read(group / name, 128).decode().strip()
        require(raw.isdecimal(), "finite cgroup limit required")
        return int(raw)
    memory, processes = number("memory.max"), number("pids.max")
    require(number("memory.swap.max") == 0, "cgroup swap must be disabled")
    cpu = protected_read(group / "cpu.max", 128).decode().split()
    require(0 < memory <= job.payload["limits"]["memory_mib"] * 1024 * 1024
            and 0 < processes <= 16 and len(cpu) == 2
            and all(v.isdecimal() and int(v) > 0 for v in cpu)
            and int(cpu[0]) <= int(cpu[1]) * job.payload["limits"]["cpu"],
            "aggregate cgroup limits exceed contract")
    status = dict(row.split(":", 1) for row in Path("/proc/self/status").read_text().splitlines()
                  if ":" in row)
    require(int(status.get("CapEff", "1").strip(), 16) == 0,
            "worker has effective capabilities")
    return {"memory_max": memory, "pids_max": processes, "cpu_max": [int(v) for v in cpu]}


def source_boundaries(binding, code_root):
    """Measure approved source and actual loaded source-defined Python code."""
    import ast
    import inspect
    import marshal
    import types
    require(sys.flags.isolated and sys.dont_write_bytecode
            and getattr(sys.flags, "safe_path", False),
            "resident must start with python -I -B")
    inventory = binding.get("source_bundle")
    require(type(inventory) is dict and 1 <= len(inventory) <= 200,
            "approved immutable source inventory missing")
    measured, sources = {}, {}
    for relative, pin in inventory.items():
        require(type(relative) is str and relative.endswith(".py")
                and not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                "unsafe source inventory name")
        exact_fields(pin, {"bytes", "sha256"})
        require(type(pin["bytes"]) is int and 0 < pin["bytes"] <= 1024 * 1024,
                "source file cap")
        digest_field(pin["sha256"])
        raw = protected_read(code_root / relative)
        require(len(raw) == pin["bytes"] and hashlib.sha256(raw).hexdigest() == pin["sha256"],
                "approved source bytes differ")
        measured[relative] = dict(pin)
        sources[relative] = raw
    require(digest(measured) == binding.get("source_bundle_sha256"),
            "source bundle hash differs")
    for package in ("bridge_contracts", "universal_video"):
        package_root = code_root / package
        require(package_root.is_dir(), "approved source package absent")
        for path in package_root.rglob("*"):
            require(not path.is_symlink() and "__pycache__" not in path.parts
                    and path.suffix != ".pyc", "mutable/cached source forbidden")
            if path.is_file():
                relative = path.relative_to(code_root).as_posix()
                require(relative in inventory, "unlisted mounted/runtime source")
    for name, module in tuple(sys.modules.items()):
        if name.split(".")[0] not in {"bridge_contracts", "universal_video"} or module is None:
            continue
        path = Path(getattr(module, "__file__", "")).absolute()
        require(path.is_relative_to(code_root), "loaded project module outside source bundle")
        relative = path.relative_to(code_root).as_posix()
        require(relative in sources and path.suffix == ".py", "loaded module unlisted or cached")
        raw = sources[relative]
        compiled = compile(raw, str(path), "exec", dont_inherit=True, optimize=sys.flags.optimize)
        code_by_name = {}
        def record(code):
            code_by_name[code.co_qualname] = code
            for child in code.co_consts:
                if isinstance(child, types.CodeType):
                    record(child)
        record(compiled)
        tree = ast.parse(raw)
        def check(nodes, owner, prefix=""):
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    check(node.body, getattr(owner, node.name), prefix + node.name + ".")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    value = getattr(owner, node.name)
                    actual = inspect.unwrap(value.fget if isinstance(value, property) else value)
                    if isinstance(actual, (staticmethod, classmethod)):
                        actual = actual.__func__
                    expected = code_by_name.get(prefix + node.name)
                    require(expected is not None and hasattr(actual, "__code__")
                            and marshal.dumps(actual.__code__) == marshal.dumps(expected),
                            "loaded source-defined code differs from approved source")
        check(tree.body, module)
    return binding["source_bundle_sha256"]


def book_registry(path):
    return strict_json(protected_read(path).decode("utf-8"))


def qualified_runtime(job, binding):
    require(sys.platform == "linux", "qualified Linux required")
    require(os.geteuid() != 0, "qualified non-root resident required")
    require(type(binding) is dict
            and binding.get("book_runtime_qualified") is True
            and binding.get("evidence_qualified") is True
            and binding.get("external_runtime_evidence_ref"),
            "runtime/evidence/resource qualification missing")
    require(binding.get("job_hash") == job.job_hash
            and binding.get("source_file_id") == job.payload["source"]["drive_file_id"]
            and binding.get("code_commit") == job.payload["code_commit"]
             , "runtime identity mismatch")
    source_boundaries(binding, Path(__file__).resolve().parent.parent)
    start, end = binding.get("qualified_at_unix"), binding.get("qualification_expires_at_unix")
    require(type(start) in (int, float) and type(end) in (int, float)
            and 0 < end - start <= 3600 and start <= time.time() < end,
            "runtime qualification expired")
    expected_inputs = {item["role"]: item["sha256"] for item in job.payload["inputs"]}
    require(binding.get("approved_input_sha256") == expected_inputs,
            "reviewed input hashes not authorized")
    pins = binding.get("runtime_binary_sha256")
    exact_fields(pins, {"bwrap", "python3", "pdfinfo", "pdftoppm"})
    binaries = {}
    for name, expected in pins.items():
        digest_field(expected)
        found = shutil.which(name, path="/usr/local/bin:/usr/bin:/bin")
        require(found is not None, "runtime dependency absent")
        resolved = Path(found).resolve()
        require(str(resolved).startswith("/usr/")
                and hashlib.sha256(protected_read(resolved, 64 * 1024 * 1024)).hexdigest()
                == expected, "runtime binary changed")
        binaries[name] = str(resolved)
    return binaries


def stage_book_job(job, media_root):
    safe_directory(media_root)
    require(shutil.disk_usage(media_root).free
            >= job.payload["source"]["bytes"] + sum(item["bytes"] for item in job.payload["inputs"])
            + 5 * job.payload["limits"]["max_output_file_bytes"] + 1024 * 1024,
            "workspace free-space gate failed")
    ready = media_root / "drive-ready"
    safe_directory(ready, create=True)
    target = ready / job.job_id
    safe_directory(target, create=True)
    token = access_token()
    original = job.payload["source"]
    specs = [
        {"role": "original", "name": "source.pdf", **original},
        *job.payload["inputs"],
    ]
    pins, metadata = [], {}
    for spec in specs:
        file_id = spec["drive_file_id"]
        meta = file_metadata(file_id, token)
        mime = ("application/pdf" if spec["role"] == "original" else
                "text/x-python" if spec["role"] == "producer" else "application/json")
        require(meta.get("mimeType") == mime
                and int(meta.get("size", 0)) == spec["bytes"], "input metadata mismatch")
        snapshot = original_snapshot(meta)
        require(snapshot.get("id") == file_id and snapshot.get("version") is not None,
                "immutable source version unavailable")
        pins.append({
            "role": spec["role"], "name": spec["name"], "original": snapshot,
            "sha256": spec["sha256"], "size": spec["bytes"],
        })
        metadata[file_id] = meta
    planned = {
        "schema": "universal-video-source-integrity-v1",
        "job_id": job.job_id, "job_hash": job.job_hash,
        "original": pins[0]["original"], "sha256": original["sha256"],
        "book_inputs": pins,
    }
    pin_path = target / SOURCE_RECEIPT
    if pin_path.exists() or pin_path.is_symlink():
        require(json_file(pin_path) == planned, "immutable staging pin changed")
    else:
        require(not any(target.iterdir()), "unbound staging evidence retained")
        atomic_json(pin_path, planned)  # durable full input binding before transfers
    for spec in specs:
        destination = target / spec["name"]
        partial = target / ("." + spec["name"] + ".part")
        require(not destination.is_symlink() and not partial.is_symlink(),
                "unsafe staged input")
        if not destination.exists():
            partial.unlink(missing_ok=True)  # restart from zero; no Range resume
            download_file(spec["drive_file_id"], partial, token,
                          max_bytes=spec["bytes"], metadata=metadata[spec["drive_file_id"]])
            require(regular(partial, spec["bytes"]).st_size == spec["bytes"]
                    and file_digest(partial) == spec["sha256"], "download digest mismatch")
            pinned = next(item for item in pins if item["role"] == spec["role"])
            require(original_snapshot(file_metadata(spec["drive_file_id"], token))
                    == pinned["original"], "input changed during transfer")
            with partial.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(partial, destination)
        require(regular(destination, spec["bytes"]).st_size == spec["bytes"]
                and file_digest(destination) == spec["sha256"], "cached input changed")
    directory_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    from .durable_drive import verify_source
    verify_source(target, token)  # remote readback of all six pins before execution
    return target


def _sandbox(job, staged, output, code_root, binaries, command, deadline, log):
    require(not log.exists() and not log.is_symlink(), "sandbox log collision")
    args = [
        binaries["bwrap"], "--unshare-all", "--die-with-parent", "--new-session",
        "--clearenv", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--ro-bind", "/usr", "/usr", "--proc", "/proc", "--dev", "/dev",
        "--size", str(sandbox_tmp_bytes(job)), "--tmpfs", "/tmp",
        "--dir", "/job", "--dir", "/input",
        "--ro-bind", str(staged / "source.pdf"), "/input/source.pdf",
        "--bind", str(output), "/job/work", "--dir", "/job/source",
        "--ro-bind", str(code_root / "bridge_contracts"), "/job/source/bridge_contracts",
    ]
    for library_root in ("/lib", "/lib64"):
        if Path(library_root).exists():
            args.extend(["--ro-bind", library_root, library_root])
    # The pinned script expects its two review inputs beside __file__ and the
    # pure package at ../source. Expected output files are not mounted there.
    for role in ("producer", "source_unit", "independent_review"):
        name = INPUT_NAMES[role]
        args.extend(["--ro-bind", str(staged / name), "/job/work/" + name])
    args.extend(["--remount-ro", "/dev", "--remount-ro", "/",
                 "--disable-userns", "--assert-userns-disabled", "--cap-drop", "ALL",
                 "--chdir", "/job/work", "--", *command])
    remaining = deadline - time.monotonic()
    require(remaining > 0, "job deadline exhausted")

    def child_limits():
        import resource
        limits = job.payload["limits"]
        for key, ceiling in (
            (resource.RLIMIT_AS, limits["memory_mib"] * 1024 * 1024),
            (resource.RLIMIT_CPU, limits["wall_seconds"]),
            (resource.RLIMIT_FSIZE, limits["max_output_file_bytes"]),
            (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_NPROC, 16),
        ):
            resource.setrlimit(key, (ceiling, ceiling))
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})

    with log.open("xb") as stream:
        child = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
            env={}, start_new_session=True, preexec_fn=child_limits,
        )
        try:
            code = child.wait(timeout=remaining)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)
            raise
    require(code == 0, "sandbox failed; private log retained")


def png_size(path, maximum):
    """Decode the complete bounded non-interlaced PNG stream, including CRCs.

    Poppler's fixed PNG output is non-interlaced. Unsupported interlacing fails
    closed; it is never treated as a valid image using only its IHDR header.
    """
    regular(path, maximum)
    raw = path.read_bytes()
    require(raw[:8] == b"\x89PNG\r\n\x1a\n", "invalid PNG signature")
    position, header, palette = 8, None, None
    decoder, idat_seen, idat_closed, ended = zlib.decompressobj(), False, False, False
    buffered, previous, rows, expanded = bytearray(), None, 0, 0

    def consume(data):
        nonlocal buffered, previous, rows, expanded
        expanded += len(data)
        require(expanded <= expected, "PNG expansion exceeds exact image size")
        buffered.extend(data)
        while len(buffered) >= row_bytes + 1:
            require(rows < height and buffered[0] <= 4, "invalid PNG scanline")
            filter_type = buffered[0]
            row = bytearray(buffered[1:row_bytes + 1])
            del buffered[:row_bytes + 1]
            if color == 3:
                require(palette is not None, "indexed PNG lacks palette")
                for index in range(row_bytes):
                    left = row[index - bpp] if index >= bpp else 0
                    up = previous[index] if previous is not None else 0
                    corner = previous[index - bpp] if previous is not None and index >= bpp else 0
                    if filter_type == 1:
                        row[index] = (row[index] + left) & 255
                    elif filter_type == 2:
                        row[index] = (row[index] + up) & 255
                    elif filter_type == 3:
                        row[index] = (row[index] + (left + up) // 2) & 255
                    elif filter_type == 4:
                        estimate = left + up - corner
                        distances = [abs(estimate - left), abs(estimate - up), abs(estimate - corner)]
                        predictor = [left, up, corner][distances.index(min(distances))]
                        row[index] = (row[index] + predictor) & 255
                mask = (1 << depth) - 1
                for pixel in range(width):
                    bit = pixel * depth
                    value = (row[bit // 8] >> (8 - depth - bit % 8)) & mask
                    require(value < palette, "PNG palette index out of range")
                previous = row
            rows += 1

    while position < len(raw):
        require(position + 12 <= len(raw), "truncated PNG chunk")
        size = struct.unpack(">I", raw[position:position + 4])[0]
        kind = raw[position + 4:position + 8]
        require(size <= maximum and position + size + 12 <= len(raw), "truncated PNG payload")
        require(all(65 <= value <= 90 or 97 <= value <= 122 for value in kind)
                and not kind[2] & 32,
                "invalid PNG chunk type")
        payload = raw[position + 8:position + 8 + size]
        checksum = struct.unpack(">I", raw[position + 8 + size:position + 12 + size])[0]
        require(zlib.crc32(kind + payload) & 0xffffffff == checksum, "PNG CRC mismatch")
        position += size + 12
        if header is None:
            require(kind == b"IHDR" and size == 13, "PNG lacks first IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            supported = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8},
                         4: {8, 16}, 6: {8, 16}}
            require(0 < width <= 10000 and 0 < height <= 10000
                    and color in supported and depth in supported[color]
                    and compression == filtering == interlace == 0, "unsupported PNG layout")
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            row_bytes = (width * channels * depth + 7) // 8
            bpp = max(1, (channels * depth + 7) // 8)
            expected = (row_bytes + 1) * height
            require(expected <= 64 * 1024 * 1024, "PNG decoded-size cap")
            header = True
            continue
        require(kind != b"IHDR", "duplicate IHDR")
        if kind == b"PLTE":
            require(not idat_seen and palette is None and color not in {0, 4}
                    and size > 0 and size % 3 == 0 and size <= 768, "invalid PNG palette")
            palette = size // 3
            if color == 3:
                require(palette <= 1 << depth, "PNG palette exceeds bit depth")
        elif kind == b"IDAT":
            require(not idat_closed and not ended and (color != 3 or palette is not None),
                    "noncontiguous PNG image data")
            idat_seen = True
            pending = payload
            try:
                while pending:
                    decoded = decoder.decompress(pending, 65536)
                    consume(decoded)
                    tail = decoder.unconsumed_tail
                    require(not decoder.unused_data, "PNG has trailing compressed data")
                    require(len(tail) < len(pending) or decoded, "PNG decoder made no progress")
                    pending = tail
            except zlib.error as exc:
                raise BookJobError("invalid PNG deflate stream") from exc
        elif kind == b"IEND":
            require(size == 0 and idat_seen and decoder.eof and not decoder.unused_data
                    and rows == height and expanded == expected and not buffered
                    and position == len(raw), "incomplete PNG image or trailing bytes")
            ended = True
            break
        else:
            require(kind[0] & 32, "unsupported PNG critical chunk")
            if idat_seen:
                idat_closed = True
    require(ended, "PNG missing IEND")
    return [width, height]


def verify_candidate(job, result):
    for role in ("expected_candidate", "expected_draft_binding"):
        expected = next(item for item in job.payload["inputs"] if item["role"] == role)
        path = result / expected["name"]
        require(regular(path, expected["bytes"]).st_size == expected["bytes"]
                and file_digest(path) == expected["sha256"], "candidate bytes changed")
    bundle = json_file(result / INPUT_NAMES["expected_candidate"])
    validate_bundle(bundle)
    require(len(bundle["objects"]) == 1 and not bundle["relations"]
            and len(bundle["legacy_ids"]) == 1, "candidate cardinality changed")
    atom = bundle["objects"][0]
    scope = job.payload["scope"]
    require(atom["object_id"] == scope["object_id"]
            and list(bundle["legacy_ids"].values()) == [scope["object_id"]]
            and atom["kind"] == "ATOM" and atom["origin"] == "INFERENCE"
            and atom["verification"] == "M0" and atom["review"] == "NOT_REVIEWED"
            and atom["rights"] == "INTERNAL_ALLOWED"
            and atom["content"].get("domain") == "CARD_PLAY",
            "candidate scope or authority changed")
    source = job.payload["source"]
    require(bundle["source"]["rendition_sha256"] == source["sha256"]
            and bundle["source"]["page_count"] == source["pages"]
            and bundle["source"]["locator"]
            == "https://drive.google.com/file/d/" + source["drive_file_id"] + "/view"
            and atom["anchor"]["pdf_page_index"] in scope["pdf_page_indexes"],
            "candidate source/anchor changed")
    draft = json_file(result / INPUT_NAMES["expected_draft_binding"])
    require(draft.get("status") == "DRAFT_NOT_A_TRUSTED_PUBLICATION_RECEIPT"
            and draft.get("publication_performed") is False
            and draft.get("numeric_confidence") is None, "draft promoted")
    receipt = draft.get("runtime_receipt_candidate")
    require(type(receipt) is dict and type(receipt.get("claims")) is list
            and len(receipt["claims"]) == 1, "draft claim binding absent")
    claim = receipt["claims"][0]
    require(type(claim) is dict and type(claim.get("checks")) is dict, "draft checks absent")
    require(claim.get("object_id") == scope["object_id"]
            and claim.get("content_sha256") == digest(atom["content"])
            and claim.get("anchor_sha256") == digest(atom["anchor"])
            and claim.get("checks", {}).get("semantic_dedup")
            == "PENDING_PRODUCTION_READBACK", "draft review binding changed")


def _review(job):
    return {
        "schema": "book-reproduction-review-v1", "technical_status": "REPRODUCED",
        "candidate_sha256": next(item["sha256"] for item in job.payload["inputs"]
                                 if item["role"] == "expected_candidate"),
        "independent_review_sha256": next(item["sha256"] for item in job.payload["inputs"]
                                          if item["role"] == "independent_review"),
        "semantic_dedup": "PENDING_PRODUCTION_READBACK",
        "trusted_publication_receipt": False, "publication": "NOT_PUBLISHED",
        "legacy_render_hash_equivalence": "NOT_ESTABLISHED",
    }


def reproduce_book_job(job, staged, results_root, binding, code_root, deadline):
    binaries = qualified_runtime(job, binding)
    safe_directory(results_root)
    output = results_root / job.job_id
    require(not output.exists() and not output.is_symlink(),
            "prior computation retained; no implicit rerun")
    output.mkdir(mode=0o750)
    scratch = staged / "scratch"
    require(not scratch.exists() and not scratch.is_symlink(), "prior scratch retained")
    scratch.mkdir(mode=0o750)
    safe_directory(code_root / "bridge_contracts")
    _sandbox(job, staged, output, code_root, binaries,
             [binaries["pdfinfo"], "/input/source.pdf"], deadline, scratch / "pdfinfo.log")
    page_rows = [line.split(":", 1)[1].strip()
                 for line in (scratch / "pdfinfo.log").read_text(encoding="utf-8").splitlines()
                 if line.startswith("Pages:")]
    require(page_rows == [str(job.payload["source"]["pages"])], "PDF page count changed")
    (output / "renders").mkdir(mode=0o750)
    rows = []
    for page in job.payload["scope"]["pdf_page_indexes"]:
        name = "renders/p%d" % (page + 1)
        _sandbox(
            job, staged, output, code_root, binaries,
            [binaries["pdftoppm"], "-f", str(page + 1), "-l", str(page + 1),
             "-singlefile", "-r", "150", "-png", "/input/source.pdf", "/job/work/" + name],
            deadline, scratch / ("render-%d.log" % page),
        )
        relative = name + ".png"
        dimensions = png_size(output / relative, job.payload["limits"]["max_output_file_bytes"])
        rows.append({"pdf_page_index": page, "path": relative,
                     "sha256": file_digest(output / relative), "dimensions_px": dimensions})
    _sandbox(job, staged, output, code_root, binaries,
             [binaries["python3"], "-I", "-B", "/job/work/" + INPUT_NAMES["producer"]],
             deadline, scratch / "producer.log")
    # bubblewrap creates empty file mountpoints in the writable output tree.
    for role in ("producer", "source_unit", "independent_review"):
        placeholder = output / INPUT_NAMES[role]
        require(regular(placeholder, 0).st_size == 0, "input mountpoint changed")
        placeholder.unlink()
    for spec in job.payload["inputs"]:
        require(file_digest(staged / spec["name"]) == spec["sha256"],
                "input changed during computation")
    verify_candidate(job, output)
    atomic_json(output / "render_manifest.json", {
        "schema": "book-render-v1", "dpi": 150,
        "renderer_sha256": binding["runtime_binary_sha256"]["pdftoppm"],
        "pages": rows, "legacy_render_hash_equivalence": "NOT_ESTABLISHED",
    })
    atomic_json(output / "review_report.json", _review(job))
    manifest = {
        "schema": "book-reproduction-result-v1", "status": "COMPLETED",
        "job_id": job.job_id, "job_hash": job.job_hash, "profile": job.profile,
        "book_job": job.payload, "processing_revision": job.payload["code_commit"],
        "runtime_binary_sha256": binding["runtime_binary_sha256"],
        "publication_state": "NOT_PUBLISHED",
    }
    atomic_json(output / "manifest.json", manifest)
    verify_book_result(output, expected_job_id=job.job_id, expected_job_hash=job.job_hash)
    return manifest


def collect_book_artifacts(result_dir):
    safe_directory(result_dir)
    manifest = json_file(result_dir / "manifest.json")
    require("book_job" in manifest, "book manifest incomplete")
    job = validate_book_job(manifest["book_job"])
    allowed = {
        "manifest.json", INPUT_NAMES["expected_candidate"], INPUT_NAMES["expected_draft_binding"],
        "review_report.json", "render_manifest.json",
    }
    allowed |= {"renders/p%d.png" % (page + 1)
                for page in job.payload["scope"]["pdf_page_indexes"]}
    internal = {
        "DRIVE_FINALIZATION.json", "DRIVE_FINALIZATION.previous.json",
        ".drive-finalization-marker.json",
    }
    artifacts = []
    for path in sorted(result_dir.rglob("*")):
        require(not path.is_symlink(), "result contains symlink")
        relative = path.relative_to(result_dir).as_posix()
        if path.is_dir():
            require(relative == "renders", "result contains unexpected directory")
            continue
        require(relative in allowed | internal, "artifact not allowlisted")
        maximum = (1024 * 1024 if relative in internal
                   else job.payload["limits"]["max_output_file_bytes"])
        size = regular(path, maximum).st_size
        if relative in internal:
            continue
        artifacts.append(PublishArtifact(
            path, relative, size, file_digest(path),
            hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest(),
        ))
    require({item.relative_name for item in artifacts} == allowed, "artifact set incomplete")
    limits = job.payload["limits"]
    require(len(artifacts) <= limits["max_output_files"]
            and sum(item.size_bytes for item in artifacts) <= limits["max_output_total_bytes"],
            "artifact budget exceeded")
    return artifacts


def verify_book_result(result_dir, *, expected_job_id, expected_job_hash,
                       expected_profile="book_single_atom", expected_source_file_id=None,
                       expected_artifact_set_sha256=None, **kwargs):
    manifest = json_file(result_dir / "manifest.json")
    require("book_job" in manifest, "book manifest incomplete")
    job = validate_book_job(manifest["book_job"])
    require(manifest.get("schema") == "book-reproduction-result-v1"
            and manifest.get("status") == "COMPLETED"
            and manifest.get("job_id") == expected_job_id == job.job_id
            and manifest.get("job_hash") == expected_job_hash == job.job_hash
            and manifest.get("profile") == expected_profile == job.profile
            and manifest.get("processing_revision") == job.payload["code_commit"]
            and manifest.get("publication_state") == "NOT_PUBLISHED", "manifest binding changed")
    if expected_source_file_id is not None:
        require(expected_source_file_id == job.payload["source"]["drive_file_id"],
                "source binding changed")
    exact_fields(manifest.get("runtime_binary_sha256"), {"bwrap", "python3", "pdfinfo", "pdftoppm"})
    for value in manifest["runtime_binary_sha256"].values():
        digest_field(value)
    verify_candidate(job, result_dir)
    renders = json_file(result_dir / "render_manifest.json")
    require(renders.get("schema") == "book-render-v1" and renders.get("dpi") == 150
            and renders.get("renderer_sha256") == manifest["runtime_binary_sha256"]["pdftoppm"]
            and renders.get("legacy_render_hash_equivalence") == "NOT_ESTABLISHED",
            "renderer provenance changed")
    rows = renders.get("pages")
    require(type(rows) is list
            and [row["pdf_page_index"] for row in rows] == job.payload["scope"]["pdf_page_indexes"],
            "render page scope changed")
    for row in rows:
        require(row["path"] == "renders/p%d.png" % (row["pdf_page_index"] + 1)
                and file_digest(result_dir / row["path"]) == row["sha256"]
                and png_size(result_dir / row["path"], job.payload["limits"]["max_output_file_bytes"])
                == row["dimensions_px"], "render bytes changed")
    require(json_file(result_dir / "review_report.json") == _review(job), "review authority changed")
    inventory = artifact_set_sha256(collect_book_artifacts(result_dir))
    if expected_artifact_set_sha256 is not None:
        require(inventory == expected_artifact_set_sha256, "artifact inventory changed")
    return {
        "state": "PASS", "artifact_set_sha256": inventory,
        "technical_bundle_ready": True, "bridge_production_ready": False,
    }
