"""Read-only binding to the existing pinned Oracle admission receiver.

This is NOT a lifecycle Runtime or a Stop/quiescence permit. It adds retained
readback without invoking receive/reconcile/ARM, changing history or importing
the old package during inspect(). Authenticated expected pins must arrive
separately through the existing owner channel, never from the records themselves.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
import types

INSTANCE_ID = "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2"
ROOT = Path("/home/ubuntu/.local/share/bridge-school/ibm-stop-backstop-v6")
PACKAGE_SHA = "2b74dd15c4b3e2c1f3ec80a6a1dc3c5a10ecf12753fcee8eb6020fa92bcf9a4a"
ENTRY_SHA = "8b00560beedb1a5beffbc3826eef119c5584b75e106332c8257f4182edb2a46f"
LAUNCHER_SHA = "aa82c6d0cef72703baeef609b72bf5da5b6c779b4ac4d7d6ffefc0b84b0b508e"
PINS = {
    "collect_admit_receiver": "c956b1d7c46e68c56ed091fb6b1cadba7fedd298d8560a5285557f32427c5131",
    "collect_and_admit": "e03b2cd98c2a747ff7e439441ea1a562532b2f22bc84be73b9cd5a397715c2c5",
    "primary_derivation": "8ab11de4f81ba9ec51e5f63acd786af9a910e1ebb09f0cccd9c0e3509cfcfb6b",
    "operational_reconciliation": "505421504da7d0aff420ff79bd3890679a5a88e00afdcaccfce400901a9e75c3",
}
HISTORY_SHA = "57e52266e7e098b247df38142f243e3a8623cfb7a69ccb7e3a2a0662e0c66903"
WATCHER_SHA = "658a0591ffe9f12671ed4d65a0656411c79407511ba1cdaf52a9029e202c6771"
LEGACY_STOP = {
    "repository": "olegmed1-art/bridge-video-free",
    "workflow": ".github/workflows/ibm-vpc-power-probe.yml",
    "ref": "review/ibm-trial-control-20261001",
    "source_sha": "f0aeba3c80f05874445cf455fda346b832051dbb",
    "lane": "ibm-vpc-independent-stop",
    "force": False,
}
MAX_PROOF = 4 * 1024 * 1024
MAX_WINDOW = 32768
MAX_SOURCE = 131072


class ReadbackRefused(RuntimeError):
    """Fixed code; never render records, provider bodies or supplied arguments."""


def need(ok, code):
    if not ok:
        raise ReadbackRefused(code)


def pin(value, length=64):
    return type(value) is str and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parsed(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "DUPLICATE_RECORD_KEY")
            result[key] = value
        return result
    def bad(_):
        raise ReadbackRefused("NONFINITE_RECORD")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
    except (UnicodeError, ValueError):
        raise ReadbackRefused("RECORD_ENCODING") from None


@dataclass(frozen=True)
class Cycle:
    control_run: int
    nonce: str
    source_sha: str

    def __post_init__(self):
        need(type(self.control_run) is int and self.control_run > 0 and
             pin(self.nonce, 32) and pin(self.source_sha, 40), "CYCLE_INVALID")

    def binding(self):
        return dict(instance_id=INSTANCE_ID, control_run=self.control_run,
                    nonce=self.nonce, source_sha=self.source_sha,
                    runtime_package_sha=PACKAGE_SHA)


@dataclass(frozen=True)
class ExpectedPins:
    request_sha256: str
    proof_sha256: str
    window_sha256: str

    def __post_init__(self):
        need(all(pin(value) for value in asdict(self).values()),
             "AUTHENTICATED_PINS_REQUIRED")


@dataclass(frozen=True)
class RetainedReadback:
    cycle: Cycle
    pins: ExpectedPins
    observed_wall_ms: int
    expires_ms: int
    prior_count: int
    stop_run_id: int
    stop_contract_job_id: int

    def v6_frame(self):
        """Exact existing readback ABI, without extra fields or a new authority."""
        return dict(kind="COLLECT_ADMIT_RETAINED_READBACK_V1",
                    **self.cycle.binding(), **asdict(self.pins),
                    observed_wall_ms=self.observed_wall_ms,
                    expires_ms=self.expires_ms, live_go=False)

    def pair_with(self, original_receipt):
        """Keep the original owner receive() receipt; never reconstruct its ack."""
        expected = dict(kind="COLLECT_ADMIT_HANDOFF_RETAINED",
                        control_run=self.cycle.control_run, nonce=self.cycle.nonce,
                        proof_sha256=self.pins.proof_sha256,
                        window_sha256=self.pins.window_sha256, live_go=False)
        need(type(original_receipt) is dict and set(original_receipt) == set(expected)
             and all(type(original_receipt[k]) is type(v) and original_receipt[k] == v
                     for k, v in expected.items()), "ORIGINAL_HANDOFF_RECEIPT_REQUIRED")
        return dict(receipt=dict(original_receipt), readback=self.v6_frame())

    def metadata(self):
        return dict(kind="IBM_ORACLE_RETAINED_ADMISSION_READBACK_V1",
                    **self.cycle.binding(), **asdict(self.pins),
                    observed_wall_ms=self.observed_wall_ms, expires_ms=self.expires_ms,
                    prior_count=self.prior_count, stop_contract=dict(LEGACY_STOP),
                    stop_run_id=self.stop_run_id,
                    stop_contract_job_id=self.stop_contract_job_id,
                    historical_completion_proven=False,
                    provider_queue_empty_proven=False, live_go=False)

    def bind_candidate(self, instance_id, cycle_nonce, owner_epoch):
        # No silent UUID/control_run alias. The owner bootstrap must reserve the
        # authenticated legacy nonce and retain that association before mutation.
        need(instance_id == INSTANCE_ID and cycle_nonce == self.cycle.nonce and
             type(owner_epoch) is int and owner_epoch > 0, "LIFECYCLE_CYCLE_LINK_MISSING")
        return dict(kind="IBM_LIFECYCLE_ADMISSION_LINK_V1",
                    binding=(instance_id, cycle_nonce, owner_epoch),
                    control_run=self.cycle.control_run,
                    source_sha=self.cycle.source_sha, runtime_package_sha=PACKAGE_SHA,
                    **asdict(self.pins), live_go=False)

    def require_stop_contract(self, supplied):
        need(type(supplied) is dict and set(supplied) == set(LEGACY_STOP) and
             all(type(supplied[k]) is type(v) and supplied[k] == v
                 for k, v in LEGACY_STOP.items()), "STOP_CONTRACT_REBIND_REQUIRED")

    def require_quiescence(self):
        # The old primary query covers native/lab/control, not video, DDS,
        # guest children/leases, operator holds or an atomic producer fence.
        raise ReadbackRefused("QUIESCENCE_PRODUCER_FENCE_NOT_BOUND")


def require_policy(policy):
    fields = ("max_cycle_ms", "grace_ms", "containment_ms", "effect_margin_ms")
    need(all(type(getattr(policy, name, None)) is int and getattr(policy, name) > 0
             for name in fields), "TIMING_POLICY_INVALID")
    need(policy.max_cycle_ms <= 85000 and
         policy.max_cycle_ms + policy.grace_ms + policy.effect_margin_ms <= 180000 and
         sum(getattr(policy, name) for name in fields) <= 270000,
         "LEGACY_STOP_ENVELOPE_MISMATCH")


def identity():
    need(os.name == "posix" and sys.platform.startswith("linux") and
         sys.flags.isolated == 1, "ISOLATED_ORACLE_LINUX_REQUIRED")
    need(os.getuid() == 1001 and socket.gethostname() == "autopilot-lite-vnic",
         "ORACLE_IDENTITY_REQUIRED")


def directory(path):
    """Open and retain the entire known private directory chain without links."""
    fds = []
    links = []
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(fd)
        info = os.fstat(fd)
        need(info.st_uid == 0 and not info.st_mode & 0o022, "DIRECTORY_METADATA")
        for part in Path(path).parts[1:]:
            need(part not in ("", ".", ".."), "DIRECTORY_SCOPE")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            fds.append(child)
            info = os.fstat(child)
            need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, 1001) and
                 not info.st_mode & 0o022, "DIRECTORY_METADATA")
            links.append((fd, part, child, (info.st_uid, info.st_gid, info.st_mode)))
            fd = child
        need(os.fstat(fd).st_uid == 1001, "PRIVATE_DIRECTORY_OWNER")
        if Path(path) in (ROOT / "primary-proofs", ROOT / "operational-windows"):
            need(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, "PRIVATE_DIRECTORY_MODE")
        return fds, links
    except BaseException:
        for fd in reversed(fds):
            os.close(fd)
        raise


def check_chain(links):
    for parent, name, child, original in links:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        opened = os.fstat(child)
        need(stat.S_ISDIR(named.st_mode) and
             (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino) and
             (named.st_uid, named.st_gid, named.st_mode) == original and
             (opened.st_uid, opened.st_gid, opened.st_mode) == original and
             opened.st_uid in (0, 1001) and not opened.st_mode & 0o022,
             "DIRECTORY_REPLACED")


def read_file(fd, name, limit):
    leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(leaf)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == 1001 and
             stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and
             0 < before.st_size <= limit, "PRIVATE_RECORD_METADATA")
        raw = bytearray()
        while len(raw) <= limit:
            part = os.read(leaf, min(65536, limit + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(leaf)
        named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        fields = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid,
                            s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(len(raw) == before.st_size and fields(before) == fields(after) == fields(named),
             "PRIVATE_RECORD_DRIFT")
        return bytes(raw)
    finally:
        os.close(leaf)


def close_chain(fds):
    for fd in reversed(fds):
        os.close(fd)


def entry_constants(raw):
    need(sha(raw) == ENTRY_SHA, "EXISTING_ENTRY_PIN_CHANGED")
    result = {}
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "PACKAGE_SHA", "LAUNCHER_SHA", "PINS", "INSTANCE_ID"
                }:
                    result[target.id] = ast.literal_eval(node.value)
    need(result == dict(PACKAGE_SHA=PACKAGE_SHA, LAUNCHER_SHA=LAUNCHER_SHA,
                        PINS=PINS, INSTANCE_ID=INSTANCE_ID), "EXISTING_ENTRY_SCOPE_CHANGED")
    return result


def load_existing_backend():
    """Only the byte-pinned installed loader; never a source selected by input.

    read() calls this only after the exact saved records exist and match their
    separately authenticated pins. No receive/session/run/reconcile is called.
    """
    identity()
    need(not set(PINS) & set(sys.modules), "PRELOADED_BACKEND_REFUSED")
    fds, links = directory(ROOT)
    try:
        raw = read_file(fds[-1], "ibm_oracle_admission_entry.py", MAX_SOURCE)
        entry_constants(raw)
        check_chain(links)
    finally:
        close_chain(fds)
    name = "_ibm_lifecycle_pinned_admission_entry"
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / "ibm_oracle_admission_entry.py")
    need(name not in sys.modules, "PRELOADED_ENTRY_REFUSED")
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, "exec"), module.__dict__)
        backend = module.load_existing_backend()
        for attribute, source_name in (
            ("receiver", "collect_admit_receiver"),
            ("rules", "primary_derivation"),
            ("operational", "operational_reconciliation"),
        ):
            loaded = getattr(backend, attribute, None)
            need(type(loaded) is types.ModuleType and
                 getattr(loaded, "__source_sha256__", None) == PINS[source_name],
                 "LOADED_BACKEND_PIN_CHANGED")
        return backend
    except BaseException:
        sys.modules.pop(name, None)
        raise ReadbackRefused("EXISTING_BACKEND_UNAVAILABLE") from None


def verify_saved(backend, cycle, pins, proof_raw, window_raw, now_ms):
    """Dataflow contract only; production authority comes from the fixed loader."""
    need(type(cycle) is Cycle and type(pins) is ExpectedPins and
         type(now_ms) is int and now_ms > 0 and
         type(proof_raw) is bytes and 0 < len(proof_raw) <= MAX_PROOF and
         type(window_raw) is bytes and 0 < len(window_raw) <= MAX_WINDOW,
         "READBACK_INPUT")
    need(sha(proof_raw) == pins.proof_sha256 and sha(window_raw) == pins.window_sha256,
         "SAVED_RECORD_PIN_MISMATCH")
    for attribute, source_name in (
        ("receiver", "collect_admit_receiver"), ("rules", "primary_derivation"),
        ("operational", "operational_reconciliation"),
    ):
        need(getattr(getattr(backend, attribute, None), "__source_sha256__", None)
             == PINS[source_name], "BACKEND_PIN_MISMATCH")
    try:
        request = backend.receiver.handoff_request(
            proof_raw, window_raw, cycle.binding(), PINS["primary_derivation"])
        need(type(request) is bytes and 0 < len(request) <= MAX_PROOF and
             sha(request) == pins.request_sha256,
             "AUTHENTICATED_REQUEST_PIN_MISMATCH")
        checked = backend.receiver.verify_request(
            request, pins.request_sha256, cycle.binding(),
            backend.rules, backend.operational, now_ms)
        need(type(checked) is tuple and len(checked) == 3 and
             checked[0] == proof_raw and checked[1] == window_raw and
             type(checked[2]) is dict and checked[2] == parsed(request),
             "EXISTING_VERIFIER_RESULT_MISMATCH")
        window = parsed(window_raw)
        backend.operational.validate_window(window, cycle.binding(), now_ms)
    except ReadbackRefused:
        raise
    except Exception:
        raise ReadbackRefused("EXISTING_ADMISSION_REFUSED") from None
    need(type(window) is dict and type(window.get("expires_ms")) is int and
         now_ms < window["expires_ms"] and window.get("historical_completion_proven") is False,
         "RETAINED_WINDOW_UNQUALIFIED")
    need(type(window.get("priors")) is list and 1 <= len(window["priors"]) <= 8,
         "PRIOR_HISTORY_UNQUALIFIED")
    stop = window.get("independent_stop")
    need(type(stop) is dict and all(type(stop.get(k)) is type(v) and stop[k] == v
         for k, v in LEGACY_STOP.items() if k != "ref"), "STOP_CONTRACT_REBIND_REQUIRED")
    need(type(stop.get("run_id")) is int and stop["run_id"] > 0 and
         type(stop.get("contract_job_id")) is int and stop["contract_job_id"] > 0,
         "STOP_CONTRACT_RECEIPT_MISSING")
    return RetainedReadback(cycle, pins, now_ms, window["expires_ms"],
                            len(window["priors"]), stop["run_id"], stop["contract_job_id"])


def read(cycle, pins, *, clock_ms=lambda: time.time_ns() // 1000000):
    """Read/verify exact saved records; no writes, locks, ARM or provider client."""
    identity()
    need(type(cycle) is Cycle and type(pins) is ExpectedPins, "READBACK_INPUT")
    opened = []
    try:
        for name in ("primary-proofs", "operational-windows"):
            opened.append(directory(ROOT / name))
        proof_name = "proofs-" + str(cycle.control_run) + ".json"
        window_name = "window-" + str(cycle.control_run) + ".json"
        try:
            proof_raw = read_file(opened[0][0][-1], proof_name, MAX_PROOF)
            window_raw = read_file(opened[1][0][-1], window_name, MAX_WINDOW)
        except FileNotFoundError:
            raise ReadbackRefused("ADMISSION_RETAINED_RECORDS_ABSENT") from None
        need(sha(proof_raw) == pins.proof_sha256 and sha(window_raw) == pins.window_sha256,
             "SAVED_RECORD_PIN_MISMATCH")
        backend = load_existing_backend()
        now = clock_ms()
        value = verify_saved(backend, cycle, pins, proof_raw, window_raw, now)
        need(read_file(opened[0][0][-1], proof_name, MAX_PROOF) == proof_raw and
             read_file(opened[1][0][-1], window_name, MAX_WINDOW) == window_raw,
             "RETAINED_READBACK_DRIFT")
        for _, links in opened:
            check_chain(links)
        # Revalidate after all I/O against a new wall time; never stamp an old
        # window with a fresh time without re-running its original verifier.
        return verify_saved(backend, cycle, pins, proof_raw, window_raw, clock_ms())
    except OSError:
        raise ReadbackRefused("RETAINED_READBACK_UNAVAILABLE") from None
    finally:
        for fds, _ in reversed(opened):
            close_chain(fds)


def inspect_existing():
    """Metadata-only live compatibility probe; never loads private package code."""
    identity()
    fds, links = directory(ROOT)
    try:
        raw = read_file(fds[-1], "ibm_oracle_admission_entry.py", MAX_SOURCE)
        entry_constants(raw)
        result = dict(kind="IBM_ORACLE_ADMISSION_BINDING_INSPECTION_V1",
                      instance_id=INSTANCE_ID, package_sha256=PACKAGE_SHA,
                      entry_sha256=ENTRY_SHA, observed_wall_ms=time.time_ns() // 1000000,
                      source_pins=dict(PINS), stop_contract=dict(LEGACY_STOP),
                      retained_counts={}, old_package_code_executed=False,
                      environment_or_argv_read=False, mutations=0, live_go=False)
        for name in ("primary-proofs", "operational-windows", "operational-reconciliations"):
            child, chain = directory(ROOT / name)
            try:
                entries = list(os.scandir(child[-1]))
                need(len(entries) <= 256, "INSPECTION_BOUND")
                result["retained_counts"][name] = len(entries)
                check_chain(chain)
            finally:
                close_chain(child)
        child, chain = directory(ROOT / PACKAGE_SHA)
        try:
            receiver = read_file(child[-1], "collect_admit_receiver.py", MAX_SOURCE)
            need(sha(receiver) == PINS["collect_admit_receiver"], "RECEIVER_PIN_CHANGED")
            functions = {n.name for n in ast.parse(receiver).body if isinstance(n, ast.FunctionDef)}
            result["installed_receiver_has_readback"] = "readback" in functions
            for name, expected in (
                ("manifest.json", PACKAGE_SHA), ("launcher.py", LAUNCHER_SHA),
                ("history_admission.py", HISTORY_SHA), ("watcher.py", WATCHER_SHA),
            ):
                need(sha(read_file(child[-1], name, MAX_SOURCE)) == expected,
                     "INSTALLED_SOURCE_PIN_CHANGED")
            check_chain(chain)
        finally:
            close_chain(child)
        check_chain(links)
        result["first_blocker"] = (
            "ADMISSION_RETAINED_RECORDS_ABSENT"
            if not result["retained_counts"]["primary-proofs"] or
               not result["retained_counts"]["operational-windows"]
            else "QUIESCENCE_PRODUCER_FENCE_NOT_BOUND")
        result["runtime_qualified"] = False
        return result
    finally:
        close_chain(fds)
