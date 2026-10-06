"""Qualified comparison executor; no provisioning or resident-UID fallback.

The default producer remains disabled. This explicit API requires an external,
root-protected, expiring qualification and observes the existing OS boundaries.
Only recognition workers enter the constructed allowlisted mount view.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

from . import comparison_artifacts as artifacts
from .comparison_producer import ComparisonProducerError, FIELDS, _preflight
from .book_runner import (protected_read, private_directory, _filesystem,
                          cgroup_boundaries, source_boundaries)
from .durable_drive import atomic_json

SCHEMA = "comparison-executor-qualification-v1"
MAX_INVENTORY_FILES = 20000
MAX_FILE_BYTES = 256 * 1024**2
RUNTIME_TARGETS = {"/usr", "/lib", "/lib64"}
HOST_IDENTITY_MAP = [[0, 0, 4294967295]]


def refuse(message):
    raise ComparisonProducerError("comparison sandbox: " + message)


def digest(value):
    return hashlib.sha256(artifacts.encoded(value)).hexdigest()


def protected_json(path, *, with_bytes=False):
    raw = protected_read(Path(path), 8 * 1024**2)
    value = artifacts.decode(raw)
    if not isinstance(value, dict):
        refuse("protected JSON object required")
    return (value, raw) if with_bytes else value


def absolute(value):
    if (not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or
            value != str(Path(value)) or ".." in Path(value).parts):
        refuse("canonical absolute path required")
    return Path(value)


def root_owned_directory(path):
    # Shared private_directory returns a device number, not a stat_result.
    private_directory(path)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_uid != 0:
            refuse("root-owned immutable tree required")
    finally:
        os.close(fd)


def inventory(root, records):
    """Verify an immutable allowlisted tree; never bind a broad host root."""
    root = absolute(str(root))
    root_owned_directory(root)
    if not isinstance(records, dict) or not 1 <= len(records) <= MAX_INVENTORY_FILES:
        refuse("bounded complete source inventory required")
    observed = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            refuse("inventory symlink")
        if path.is_dir():
            # Also reject writable/ACL-bearing ancestors and nested directories.
            root_owned_directory(path)
        elif path.is_file():
            name = path.relative_to(root).as_posix()
            if name not in records:
                refuse("unlisted mounted file")
            artifacts.checked(path)
            raw = protected_read(path, MAX_FILE_BYTES)
            observed[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        else:
            refuse("special mounted file")
    if observed != records:
        refuse("immutable source inventory changed")
    return digest(observed)


def cgroup_path():
    rows = Path("/proc/self/cgroup").read_text().splitlines()
    paths = [line[3:] for line in rows if line.startswith("0::")]
    if len(paths) != 1 or paths[0] == "/":
        refuse("exclusive finite cgroup-v2 required")
    return absolute(paths[0])


def cgroup_root(group):
    return Path("/sys/fs/cgroup") / str(absolute(str(group))).lstrip("/")


def group_pids(group):
    """Observe every descendant cgroup, refusing topology changes or read gaps."""
    root = cgroup_root(group)
    def directories():
        found = []
        def error(exc):
            raise exc
        for current, children, _ in os.walk(root, followlinks=False, onerror=error):
            path = Path(current)
            if path.is_symlink() or any((path / child).is_symlink() for child in children):
                refuse("cgroup subtree symlink")
            found.append(path)
        if not found or found[0] != root:
            refuse("cgroup subtree unavailable")
        return sorted(found)
    try:
        before = directories()
        pids = set()
        for path in before:
            rows = (path / "cgroup.procs").read_text().split()
            if any(not row.isdecimal() or int(row) <= 0 for row in rows):
                refuse("invalid cgroup process observation")
            pids.update(int(row) for row in rows)
        if before != directories():
            refuse("cgroup subtree changed during observation")
        return pids
    except OSError as exc:
        refuse("incomplete cgroup subtree observation: " + type(exc).__name__)


def host_identity(q, namespaces):
    """Only an independently verified initial host user namespace is allowed."""
    if (type(q.get("host_user_namespace_inode")) is not int
            or q["host_user_namespace_inode"] <= 0
            or namespaces["user"] != q["host_user_namespace_inode"]):
        refuse("verified initial host user namespace required")
    for name in ("uid", "gid"):
        expected = q.get(name + "_map")
        if expected != HOST_IDENTITY_MAP or any(
                type(value) is not int for row in expected for value in row):
            refuse("unambiguous initial host identity mapping required")
        try:
            observed = [[int(value) for value in row.split()]
                        for row in Path("/proc/self/" + name + "_map").read_text().splitlines()]
        except (OSError, ValueError):
            refuse("host identity mapping observation unavailable")
        if observed != expected:
            refuse("host identity mapping changed or ambiguous")
    # In this verified initial namespace the kernel IDs are host IDs.
    if q["executor_uid"] == q["resident_uid"]:
        refuse("distinct executor/resident host identity required")


def context(q):
    """Observe identity/namespace/cgroup state; qualification never changes it."""
    if not q["qualified_at_unix"] <= time.time() < q["expires_at_unix"]:
        refuse("runtime qualification expired")
    if sys.platform != "linux" or os.geteuid() == 0:
        refuse("qualified non-root Linux executor required")
    if (os.geteuid() != q["executor_uid"] or os.getegid() != q["executor_gid"]
            or os.geteuid() == q["resident_uid"]):
        refuse("separate qualified host identity required")
    namespaces = {name: os.stat("/proc/self/ns/" + name).st_ino
                  for name in ("mnt", "net", "pid", "user")}
    host_identity(q, namespaces)
    if namespaces != q["coordinator_namespaces"]:
        refuse("qualified coordinator namespaces changed")
    if any(namespaces[name] == q["resident_namespaces"][name] for name in ("mnt", "net", "pid")):
        refuse("coordinator must be separated from resident namespaces")
    mount_hash = hashlib.sha256(Path("/proc/self/mountinfo").read_bytes()).hexdigest()
    if mount_hash != q["coordinator_mountinfo_sha256"]:
        refuse("qualified coordinator filesystem changed")
    status = dict(row.split(":", 1) for row in Path("/proc/self/status").read_text().splitlines()
                  if ":" in row)
    if status.get("NoNewPrivs", "").strip() != "1":
        refuse("no-new-privileges required")
    group = cgroup_path()
    if str(group) != q["cgroup_path"] or group_pids(group) != {os.getpid()}:
        refuse("exclusive executor cgroup identity/process set changed")
    # Reuse the book contract's pure measurement helper, not its PDF authority.
    measurement = cgroup_boundaries(SimpleNamespace(payload={"limits": q["limits"]}))
    return {"namespaces": namespaces, "cgroup": str(group), "limits": measurement}


def workspace(q):
    root = absolute(q["workspace_root"])
    private_directory(root, persistent=False)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        kind, capacity = _filesystem(fd)
        volume = os.fstatvfs(fd)
    finally:
        os.close(fd)
    if (kind != 0x01021994 or capacity <= 0
            or capacity > q["limits"]["workspace_mib"] * 1024**2
            or not 0 < volume.f_files <= q["limits"]["workspace_inodes"]):
        refuse("finite aggregate byte/inode tmpfs workspace required")
    return root


def qualify(config, qualification_file):
    # Default refusal occurs before host probes, runner import or subprocess.
    if qualification_file is None:
        refuse("external runtime qualification unavailable")
    q, qualification_bytes = protected_json(qualification_file, with_bytes=True)
    required = {"schema", "qualified_at_unix", "expires_at_unix", "config_sha256",
                "executor_uid", "executor_gid", "resident_uid", "coordinator_namespaces",
                "host_user_namespace_inode", "uid_map", "gid_map",
                "resident_namespaces", "coordinator_mountinfo_sha256", "cgroup_path",
                "workspace_root", "ledger_root", "limits", "bwrap", "python_target",
                "runtime_trees", "source_trees", "qualification_evidence",
                "code_root", "source_bundle", "source_bundle_sha256"}
    if set(q) != required or q["schema"] != SCHEMA or q["config_sha256"] != digest(config):
        refuse("exact job/executor qualification binding required")
    start, end, now = q["qualified_at_unix"], q["expires_at_unix"], time.time()
    if (type(start) not in (int, float) or type(end) not in (int, float)
            or not 0 < end - start <= 3600 or not start <= now < end):
        refuse("runtime qualification expired")
    if (any(type(q[key]) is not int or q[key] <= 0
            for key in ("executor_uid", "executor_gid", "resident_uid"))
            or q["python_target"] != "/usr/bin/python3"):
        refuse("invalid qualified identity/interpreter")
    limits = q["limits"]
    if (not isinstance(limits, dict)
            or set(limits) != {"cpu", "memory_mib", "workspace_mib", "workspace_inodes"}
            or any(type(v) is not int or v <= 0 for v in limits.values())):
        refuse("qualified positive resource ceilings required")
    observation = context(q)
    code_root = absolute(q["code_root"])
    if code_root != Path(__file__).resolve().parent.parent:
        refuse("loaded executor source root differs from qualification")
    # Reuse the published immutable-source/loaded-code checker without any
    # book job dispatch, PDF input grants or capability qualification.
    source_boundaries(q, code_root)
    workspace(q)
    private_directory(absolute(q["ledger_root"]), persistent=True)
    profile = {key: value for key, value in q.items()
               if key not in {"qualified_at_unix", "expires_at_unix", "qualification_evidence"}}
    evidence = q["qualification_evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"path", "sha256"}:
        refuse("independent qualification evidence required")
    raw = protected_read(absolute(evidence["path"]), 8 * 1024**2)
    if hashlib.sha256(raw).hexdigest() != evidence["sha256"]:
        refuse("qualification evidence bytes changed")
    proof = artifacts.decode(raw)
    if (not isinstance(proof, dict) or proof.get("schema") != "comparison-os-controls-v1"
            or proof.get("profile_sha256") != digest(profile)
            or proof.get("state") != "PASS"
            or any(proof.get(key) is not True for key in (
                "credential_canaries_denied", "gold_and_seal_canaries_denied",
                "sibling_canaries_denied", "os_network_denied", "aggregate_limits_enforced",
                "descendants_absent_after_timeout", "parent_death_contained",
                "cgroup_migration_denied", "initial_host_user_namespace_verified"))):
        refuse("independent OS controls not qualified")
    binary = q["bwrap"]
    if not isinstance(binary, dict) or set(binary) != {"path", "sha256"}:
        refuse("pinned sandbox binary required")
    if hashlib.sha256(protected_read(absolute(binary["path"]), 64 * 1024**2)).hexdigest() != binary["sha256"]:
        refuse("sandbox binary changed")
    return q, {**observation,
               "qualification_sha256": hashlib.sha256(qualification_bytes).hexdigest(),
               "evidence_sha256": hashlib.sha256(raw).hexdigest(),
               "profile_sha256": digest(profile)}


def qualification_pin(q, observation):
    """Immutable admission pin; no mid-run renewal or valid-profile substitution."""
    pin = {key: artifacts._hex(observation.get(key))
           for key in ("qualification_sha256", "evidence_sha256", "profile_sha256")}
    pin["decoded_qualification_sha256"] = digest(q)
    return pin


def trees(q, config, sealed):
    mounts = []
    runtimes = q["runtime_trees"]
    if not isinstance(runtimes, list) or not 1 <= len(runtimes) <= 3:
        refuse("curated runtime trees required")
    targets = set()
    for item in runtimes:
        if not isinstance(item, dict) or set(item) != {"root", "target", "files"}:
            refuse("exact runtime tree record required")
        if (item["target"] not in RUNTIME_TARGETS or item["target"] in targets
                or item["root"] in {"/", "/usr", "/lib", "/lib64"}):
            refuse("broad host runtime mount forbidden")
        inventory(item["root"], item["files"])
        mounts.append((str(absolute(item["root"])), item["target"]))
        targets.add(item["target"])
    sources = q["source_trees"]
    if not isinstance(sources, dict) or set(sources) != {"runner", "baseline", "candidate"}:
        refuse("exact recognition source trees required")
    for variant, record in sources.items():
        if not isinstance(record, dict) or set(record) != {"root", "files"}:
            refuse("exact source tree record required")
        expected_root = config["runner_root"] if variant == "runner" else sealed[variant]["root"]
        if record["root"] != expected_root:
            refuse("source root differs from frozen manifest")
        inventory(record["root"], record["files"])
    # Runner and all five frozen inputs are pinned/root-protected; gold stays
    # coordinator-only. Generated worker configs are separately validated.
    runner = absolute(config["runner_root"]) / "tools/recognizer_compare.py"
    artifacts.checked(runner)
    if hashlib.sha256(protected_read(runner)).hexdigest() != config["runner_sha256"]:
        refuse("runner bytes changed")
    for item in sealed["inputs"].values():
        artifacts.checked(absolute(item["path"]))
        if hashlib.sha256(protected_read(absolute(item["path"]), MAX_FILE_BYTES)).hexdigest() != item["sha256"]:
            refuse("frozen comparison input changed")
    return runner, mounts


def safe_target(path):
    path = absolute(str(path))
    if str(path) == "/" or any(path == Path(root) or path.is_relative_to(root)
                              for root in ("/proc", "/dev", "/sys", "/run/secrets",
                                           "/etc", "/home", "/root", "/usr", "/lib", "/lib64")):
        refuse("input/output collides with runtime or forbidden namespace")
    return str(path)


def worker_mounts(config_path, config, q, sealed, runner, runtime_mounts, temp):
    """Gold, seal, sibling configs/results and resident roots are never mounted."""
    variant = config.get("variant")
    expected_keys = {"variant", "job_id", "root", "sha", "source_offset_ms",
                     "inputs", "output", "manifest_sha256"}
    if (set(config) != expected_keys
            or variant not in ("baseline", "candidate")
            or config.get("job_id") != sealed["case_id"] + "-" + variant
            or config.get("source_offset_ms") != sealed["source_offset_ms"]
            or set(config.get("inputs", {})) != {"video", "reference", "profile", "sprite"}
            or config["inputs"] != {k: v for k, v in sealed["inputs"].items() if k != "gold"}
            or config.get("root") != sealed[variant]["root"]
            or config.get("sha") != sealed[variant]["sha"]
            or config.get("manifest_sha256") != q["_sealed_sha256"]):
        refuse("worker inputs/runtime differ from sealed scope")
    runner_name = safe_target(runner)
    source = safe_target(config["root"])
    output = safe_target(config["output"])
    control = safe_target(config_path)
    inputs = [(safe_target(item["path"]), safe_target(item["path"]))
              for item in config["inputs"].values()]
    ro = [*runtime_mounts, (str(runner), runner_name), (str(config_path), control),
          (config["root"], source), *inputs]
    rw = [(str(temp), "/tmp"), (config["output"], output)]
    # No path alias/ancestor may smuggle another role or gold into this view.
    sibling = "candidate" if variant == "baseline" else "baseline"
    protected = [absolute(sealed["inputs"]["gold"]["path"]),
                 absolute(sealed[sibling]["root"]),
                 config_path.parent / "seal.json",
                 config_path.parent / (sibling + "-config.json"),
                 config_path.parent / sibling,
                 *[absolute(path) for path in q["_excluded_paths"]]]
    targets = [absolute(dst) for _, dst in ro + rw]
    if len(set(targets)) != len(targets):
        refuse("duplicate sandbox mount target")
    for src, _ in ro + rw:
        root = absolute(src)
        if any(path == root or path.is_relative_to(root) or root.is_relative_to(path)
               for path in protected):
            refuse("protected coordinator/sibling role overlaps worker mount")
    for i, path in enumerate(targets):
        if any(path.is_relative_to(other) or other.is_relative_to(path)
               for other in targets[i + 1:]):
            refuse("overlapping worker mounts")
    return ro, rw


def command(q, ro, rw, runner, config_path, output, source_root):
    directories = {"/proc", "/dev", "/tmp"}
    for _, target in ro + rw:
        path = absolute(target)
        directories.update(str(parent) for parent in path.parents if str(parent) != "/")
    # Build an empty namespace, not a read-only bind of the resident filesystem.
    args = [q["bwrap"]["path"], "--unshare-all", "--die-with-parent", "--new-session",
            "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "LANG", "C.UTF-8", "--setenv", "GIT_OPTIONAL_LOCKS", "0",
            "--setenv", "GIT_CONFIG_NOSYSTEM", "1",
            "--setenv", "GIT_CONFIG_GLOBAL", "/dev/null",
            "--setenv", "GIT_TERMINAL_PROMPT", "0",
            "--setenv", "GIT_CONFIG_COUNT", "2",
            "--setenv", "GIT_CONFIG_KEY_0", "safe.directory",
            "--setenv", "GIT_CONFIG_VALUE_0", str(absolute(source_root)),
            "--setenv", "GIT_CONFIG_KEY_1", "core.fsmonitor",
            "--setenv", "GIT_CONFIG_VALUE_1", "false"]
    for directory in sorted(directories, key=lambda p: (len(Path(p).parts), p)):
        args.extend(["--dir", directory])
    args.extend(["--proc", "/proc", "--dev", "/dev"])
    for src, dst in ro:
        args.extend(["--ro-bind", src, dst])
    for src, dst in rw:
        args.extend(["--bind", src, dst])
    args.extend(["--remount-ro", "/dev", "--remount-ro", "/",
                 "--disable-userns", "--assert-userns-disabled", "--cap-drop", "ALL",
                 "--chdir", output, "--", q["python_target"], "-I", "-B",
                 str(runner), "_worker", "--config", str(config_path)])
    return args


def await_no_descendants(group, timeout=5):
    end = time.monotonic() + timeout
    while group_pids(group) != {os.getpid()}:
        if time.monotonic() >= end:
            refuse("descendants still present; retained evidence, no continuation")
        time.sleep(0.02)
    return {"state": "ABSENT", "scope": "CGROUP_SUBTREE",
            "cgroup": str(group), "observed_pids": [os.getpid()]}


def launch_worker(config_path, sealed, q, runner, runtime_mounts, workspace_root, kwargs):
    context(q)
    workspace(q)
    raw = artifacts.read(config_path, 1024 * 1024)
    config = artifacts.decode(raw)
    output = absolute(config["output"])
    if (not output.is_relative_to(workspace_root) or
            kwargs.get("cwd") != output or kwargs.get("check") is not False
            or set(kwargs) != {"cwd", "stdout", "stderr", "timeout", "check"}):
        refuse("unexpected runner launch contract")
    private_directory(output)
    expected_output = config_path.parent / config["variant"] / (sealed["case_id"] + "-" + config["variant"])
    if (output != expected_output
            or config_path.name != config["variant"] + "-config.json"):
        refuse("worker control/output location differs from runner protocol")
    stream = kwargs["stdout"]
    log_path = output / "process.log"
    if (getattr(stream, "name", None) != str(log_path)
            or kwargs["stderr"] != subprocess.STDOUT):
        refuse("worker logs must stay inside bounded variant output")
    artifacts.checked(log_path)
    stream_info, path_info = os.fstat(stream.fileno()), log_path.stat()
    if (stream_info.st_dev, stream_info.st_ino) != (path_info.st_dev, path_info.st_ino):
        refuse("worker log descriptor changed")
    timeout = min(kwargs["timeout"], q["_timeout_seconds"],
                  q["expires_at_unix"] - time.time(),
                  q["_deadline"] - time.monotonic())
    if timeout <= 0:
        refuse("qualified job deadline exhausted")
    temp = workspace_root / ("tmp-" + config["variant"])
    temp.mkdir(mode=0o700)
    ro, rw = worker_mounts(config_path, config, q, sealed, runner, runtime_mounts, temp)
    args = command(q, ro, rw, runner, config_path, str(output), config["root"])
    group = cgroup_path()
    with open(os.devnull, "rb") as stdin:
        process = subprocess.Popen(args, stdin=stdin, stdout=kwargs["stdout"],
                                   stderr=kwargs["stderr"], env={}, close_fds=True,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            proof = await_no_descendants(group)
            atomic_json(workspace_root / ("termination-" + config["variant"] + ".json"), proof)
    return subprocess.CompletedProcess(args, code)


def authorized_entry(config):
    """Pure entry validation; explicit replay denial precedes all OS work."""
    if (type(config) is not dict or set(config) != FIELDS
            or config.get("replay_authorized") is not True):
        refuse("exact authorized producer entry required")
    # All fields are scalar; snapshot the entry before binding qualification.
    config = dict(config)
    if (type(config["timeout_seconds"]) is not int
            or not 1 <= config["timeout_seconds"] <= 900):
        refuse("bounded comparison timeout required")
    for key in ("job_id", "source_file_id"):
        if not isinstance(config[key], str) or not config[key].strip():
            refuse("nonempty producer job/source identity required")
    if (not isinstance(config["source_version"], str)
            or not re.fullmatch(r"[1-9][0-9]*", config["source_version"])):
        refuse("positive source version string required")
    for key in ("job_hash", "source_sha256", "runner_sha256",
                "sealed_manifest_sha256", "clip_binding_sha256"):
        artifacts._hex(config[key])
    artifacts._hex(config["runner_commit"], artifacts.HEX40)
    for key in ("runner_root", "sealed_manifest_path", "clip_binding_path"):
        absolute(config[key])
    return config


def run_qualified_comparison(config, *, qualification_file=None):
    """Dedicated executor API; never used as a resident/default launch fallback."""
    config = authorized_entry(config)
    q, observation = qualify(config, qualification_file)
    admitted_pin = qualification_pin(q, observation)
    deadline = time.monotonic() + min(q["expires_at_unix"] - time.time(),
                                    2 * config["timeout_seconds"] + 120)
    sealed = _preflight(config)
    runner, mounts = trees(q, config, sealed)
    root = workspace(q)
    ledger = absolute(q["ledger_root"])
    private_directory(ledger, persistent=True)
    reservation = ledger / ("comparison-" + config["job_hash"])
    reservation.mkdir(mode=0o700)
    fd = os.open(ledger, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    atomic_json(reservation / "attempt.json",
                {"state": "STARTED", "config_sha256": digest(config)})
    attempt = root / ("comparison-" + config["job_hash"])
    attempt.mkdir(mode=0o700)
    atomic_json(attempt / "qualification-observed.json",
                {"config_sha256": digest(config), "observation": observation})
    # Only the existing source-pinned runner is imported. Its prepare/compare
    # and capture protocol are reused; every recognition launch is replaced.
    spec = importlib.util.spec_from_file_location("qualified_comparison_runner", runner)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    usr = next((record for record in q["runtime_trees"] if record["target"] == "/usr"), None)
    if usr is None or "bin/git" not in usr["files"]:
        refuse("pinned Git runtime required")
    git_path = str(absolute(usr["root"]) / "bin/git")
    git_sequence = 0
    def isolated_git(root, *args):
        # Git checkout probes also run only under the qualified coordinator,
        # with no inherited environment/global config or optional write locks.
        nonlocal git_sequence
        context(q)
        budget = min(30, q["expires_at_unix"] - time.time(), deadline - time.monotonic())
        if budget <= 0:
            refuse("qualified Git deadline exhausted")
        git_sequence += 1
        probe = attempt / ("git-" + str(git_sequence))
        probe.mkdir(mode=0o700)
        stdout_path, stderr_path = probe / "stdout.log", probe / "stderr.log"
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            process = subprocess.Popen(
                [git_path, "-c", "safe.directory=" + str(absolute(str(root))),
                 "-c", "core.fsmonitor=false", "-C", str(root), *args],
                env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                     "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
                     "GIT_OPTIONAL_LOCKS": "0"}, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, close_fds=True, start_new_session=True)
            try:
                code = process.wait(timeout=budget)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
                proof = await_no_descendants(cgroup_path())
                atomic_json(probe / "termination.json", proof)
        if code:
            raise subprocess.CalledProcessError(code, process.args)
        return artifacts.read(stdout_path, 1024 * 1024).decode("utf-8").strip()

    module.git = isolated_git
    context(q)
    module.verify_checkout(Path(config["runner_root"]), config["runner_commit"])
    q = {**q, "_sealed_sha256": config["sealed_manifest_sha256"],
         "_timeout_seconds": config["timeout_seconds"], "_deadline": deadline,
         "_excluded_paths": [str(absolute(str(qualification_file))),
                             q["qualification_evidence"]["path"], q["ledger_root"],
                             config["sealed_manifest_path"],
                             config["clip_binding_path"]]}
    def isolated_launch(argv, **kwargs):
        if (len(argv) != 7 or argv[1:3] != ["-I", "-B"]
                or argv[3] != str(runner) or argv[4:6] != ["_worker", "--config"]):
            refuse("unexpected runner worker command")
        return launch_worker(absolute(argv[6]), sealed, q, runner, mounts, attempt, kwargs)
    module.launch_worker = isolated_launch
    raw = attempt / "raw"
    code = module.compare(Path(config["sealed_manifest_path"]),
                          config["sealed_manifest_sha256"], raw,
                          timeout=config["timeout_seconds"])
    # Re-read independent qualification/OS and all immutable inputs before
    # returning captures. No publication/attachment authority is introduced.
    final_q, final_observation = qualify(config, qualification_file)
    if qualification_pin(final_q, final_observation) != admitted_pin:
        refuse("admitted qualification/evidence changed; no renewal permitted")
    if _preflight(config) != sealed:
        refuse("sealed inputs/clip changed during capture")
    trees(q, config, sealed)
    if code:
        refuse("comparison failed; bounded workspace evidence retained")
    atomic_json(reservation / "attempt.json",
                {"state": "CAPTURED_UNSCORED", "config_sha256": digest(config)})
    atomic_json(attempt / "executor-complete.json",
                {"state": "CAPTURED_UNSCORED", "accuracy_evaluated": False,
                 "promotion_allowed": False, "config_sha256": digest(config)})
    return raw
