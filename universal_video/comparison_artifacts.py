"""Lossless, typed comparison evidence; no general artifact allow-list expansion.

A package fits the existing 256 MiB combined publication quota or fails closed.
Opaque 4 MiB parts preserve original evidence bytes including rejected pairs.
Raw nonempty process logs are never publishable. No upload/cleanup is performed
by the builder. Attach its declaration before generating the server review.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat

SCHEMA = "universal-video-comparison-artifacts-v1"
RESULT_KIND = "recognizer-comparison-v1"
PART_BYTES = 4 * 1024**2
MAX_TOTAL_BYTES = 256 * 1024**2
MAX_FILES = 4096
MAX_AUCTION_SNAPSHOTS = 128
MAX_AUCTION_BYTES = 128 * 1024**2
MAX_JSON_BYTES = 8 * 1024**2
LEGACY_SCOPE = "PRIMARY_VISUAL_ONLY; NO_ASR_AUCTION_DDS_OR_PUBLISHER"
AUCTION_SCOPE = "PRIMARY_VISUAL_WITH_OPTIONAL_EMBEDDED_PROFILE_AUCTION; NO_ASR_DDS_OR_PUBLISHER"
AUCTION_RUNNER = "recognizer-comparison-v1-auction-scope-r3"
AUCTION_VERSION = "bridgit-primary-video-gambler-v2-auction-candidate3"
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")
CASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
CREDENTIAL = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|Bearer\s+\S+|"
    r"(?:postgres(?:ql)?|mysql)://[^\s]+|"
    r"(?:gh[pousr]_|github_pat_|AIza)[A-Za-z0-9_\-]{12,}|"
    r"\"(?:access_token|refresh_token|client_secret|private_key|password)\"\s*:",
    re.IGNORECASE,
)


def fail(message):
    raise RuntimeError(message)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            fail("duplicate JSON key")
        value[key] = item
    return value


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        fail("nonfinite JSON")
    return value


def screen_decoded(value):
    if isinstance(value, str):
        if CREDENTIAL.search(value):
            fail("credential-like decoded string is not publishable")
    elif isinstance(value, dict):
        forbidden = {"accesstoken", "refreshtoken", "clientsecret", "privatekey",
                     "password", "apikey", "authorization", "credentials",
                     "credential", "databaseurl", "dsn"}
        for key, item in value.items():
            if re.sub(r"[_\s-]", "", key.lower()) in forbidden:
                fail("credential-like decoded key is not publishable")
            screen_decoded(key)
            screen_decoded(item)
    elif isinstance(value, list):
        for item in value:
            screen_decoded(item)


def decode(raw):
    try:
        text = raw.decode("utf-8")
        if CREDENTIAL.search(text):
            fail("credential-like text is not publishable")
        value = json.loads(text, object_pairs_hook=_pairs, parse_float=_float,
                           parse_constant=lambda _: fail("nonfinite JSON"))
        # Escaped JSON keys/values must not bypass the textual refusal gate.
        canonical = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if CREDENTIAL.search(canonical):
            fail("credential-like decoded JSON is not publishable")
        screen_decoded(value)
        return value
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("invalid comparison JSON") from exc


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"


def _hex(value, pattern=HEX64):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        fail("invalid digest")
    return value


def _int(value, maximum=MAX_TOTAL_BYTES):
    if type(value) is not int or not 0 <= value <= maximum:
        fail("invalid bounded count")
    return value


def safe_path(name):
    if (not isinstance(name, str) or len(name) > 240 or
            not re.fullmatch(r"[A-Za-z0-9_./-]+", name) or
            any(x in {"", ".", ".."} for x in name.split("/")) or
            PurePosixPath(name).is_absolute()):
        fail("unsafe comparison path")
    return name


def checked(path, *, directory=False):
    path = Path(path)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            fail("symlink comparison path")
    info = path.lstat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            fail("comparison directory required")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fail("comparison regular unlinked file required")
    return info


def read(path, maximum=MAX_TOTAL_BYTES):
    before = checked(path)
    if before.st_size > maximum:
        fail("comparison file quota")
    with Path(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    after = checked(path)
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(raw) != before.st_size or identity(before) != identity(after):
        fail("comparison file changed")
    return raw


def _json(path):
    value = decode(read(path, MAX_JSON_BYTES))
    if not isinstance(value, dict):
        fail("comparison JSON object required")
    return value


def allowed(name, case):
    if name in {"seal.json", "comparison.json", "baseline-config.json", "candidate-config.json", "source-clip-binding.json", "embedded-profile.json"}:
        return True
    for variant in ("baseline", "candidate"):
        prefix = variant + "/" + case + "-" + variant + "/"
        if not name.startswith(prefix):
            continue
        leaf = name[len(prefix):]
        return bool(
            leaf in {"process.log", "worker-status.json", "result.json", "evidence/events.jsonl"} or
            re.fullmatch(r"recognizer/primary_[0-9]{10}\.png", leaf) or
            re.fullmatch(r"recognizer/auction-evidence/auction-[0-9]{10}-[0-9a-f]{12}\.png", leaf) or
            re.fullmatch(r"evidence/decoded/[0-9]{5}\.png", leaf) or
            re.fullmatch(r"evidence/attempts/[0-9]{5}/(?:frame-[01]\.png|backend-result\.json)", leaf)
        )
    return False


def inventory_paths(root, case):
    checked(root, directory=True)
    result = []
    entries = 0
    def walk(directory):
        nonlocal entries
        for path in sorted(directory.iterdir()):
            entries += 1
            if entries > 2 * MAX_FILES:
                fail("comparison tree count quota")
            checked(path, directory=path.is_dir())
            name = safe_path(path.relative_to(root).as_posix())
            if path.is_dir():
                walk(path)
            else:
                if not allowed(name, case):
                    fail("unexpected comparison artifact")
                result.append(name)
                if len(result) > MAX_FILES:
                    fail("comparison file count quota")
    walk(root)
    if len({n.casefold() for n in result}) != len(result):
        fail("comparison path collision")
    return result


def validate_files(files, binding):
    """Re-run structural validation over original or reassembled evidence bytes."""
    binding_keys = {"job_id", "job_hash", "source_file_id", "source_version", "source_sha256",
                    "case_id", "source_offset_ms", "runner_commit", "runner_sha256",
                    "sealed_manifest_sha256", "clip_binding_sha256", "input_sha256", "revisions"}
    if not isinstance(binding, dict) or set(binding) != binding_keys:
        fail("invalid comparison binding schema")
    if (not isinstance(binding["input_sha256"], dict) or
            set(binding["input_sha256"]) != {"video", "reference", "profile", "sprite", "gold"} or
            not isinstance(binding["revisions"], dict) or set(binding["revisions"]) != {"baseline", "candidate"}):
        fail("invalid comparison input/revision schema")
    for value in binding["input_sha256"].values():
        _hex(value)
    for value in binding["revisions"].values():
        _hex(value, HEX40)
    case = binding["case_id"]
    for name, raw in files.items():
        if not allowed(safe_path(name), case):
            fail("unexpected comparison artifact")
        if name.endswith("process.log"):
            if raw:
                fail("nonempty raw process log is not publishable")
        elif name.endswith(".png"):
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                fail("comparison PNG signature")
        elif name.endswith(".json"):
            if len(raw) > MAX_JSON_BYTES:
                fail("comparison JSON quota")
            decode(raw)
        elif name.endswith(".jsonl"):
            if len(raw) > MAX_JSON_BYTES:
                fail("comparison events quota")
            for line in raw.splitlines():
                decode(line)
    required = {"seal.json", "comparison.json", "baseline-config.json", "candidate-config.json", "source-clip-binding.json"}
    if not required <= files.keys():
        fail("comparison root receipts missing")
    clip_binding = decode(files["source-clip-binding.json"])
    if not isinstance(clip_binding, dict):
        fail("clip binding object required")
    _int(clip_binding.get("source_offset_ms"), 43200000)
    if (clip_binding.get("schema") != "universal-video-source-clip-binding-v1" or
            clip_binding.get("status") != "PASS" or
            clip_binding.get("source_file_id") != binding["source_file_id"] or
            clip_binding.get("source_version") != binding["source_version"] or
            clip_binding.get("source_sha256") != binding["source_sha256"] or
            clip_binding.get("clip_sha256") != binding["input_sha256"]["video"] or
            clip_binding.get("source_offset_ms") != binding["source_offset_ms"] or
            hashlib.sha256(files["source-clip-binding.json"]).hexdigest() != binding["clip_binding_sha256"]):
        fail("independent clip-to-original binding missing")
    _hex(clip_binding.get("verification_receipt_sha256"))
    if not 1 <= _int(clip_binding.get("duration_ms"), 120000) <= 120000:
        fail("comparison clip duration outside bound")
    seal, comparison = decode(files["seal.json"]), decode(files["comparison.json"])
    if not isinstance(seal, dict) or not isinstance(comparison, dict):
        fail("comparison receipt objects required")
    _int(seal.get("source_offset_ms"), 43200000)
    if (seal.get("schema") != RESULT_KIND or comparison.get("schema") != RESULT_KIND or
            seal.get("case_id") != case or
            seal.get("manifest_sha256") != binding["sealed_manifest_sha256"] or
            comparison.get("manifest_sha256") != binding["sealed_manifest_sha256"] or
            seal.get("runner_sha256") != binding["runner_sha256"] or
            seal.get("gold_sha256") != binding["input_sha256"]["gold"] or
            comparison.get("gold_sha256") != binding["input_sha256"]["gold"] or
            comparison.get("accuracy_evaluated") is not False or
            comparison.get("promotion_allowed") is not False or
            comparison.get("status") not in {"CAPTURED_UNSCORED", "REPLAY_ERROR"} or
            seal.get("source_offset_ms") != binding["source_offset_ms"]):
        fail("comparison receipt binding mismatch")
    # Failed/interrupted producers cannot attest complete retained evidence.
    # Preserve their entire local tree; never publish a complete-package receipt
    # or authorize cleanup from partial/error output.
    if comparison["status"] != "CAPTURED_UNSCORED":
        fail("incomplete comparison cannot publish or authorize cleanup; evidence retained")
    scope = seal.get("scope")
    if scope not in {LEGACY_SCOPE, AUCTION_SCOPE}:
        fail("unsupported comparison result scope")
    auction_scope = scope == AUCTION_SCOPE
    if auction_scope:
        for receipt in (seal, comparison):
            if receipt.get("scope") != AUCTION_SCOPE or receipt.get("runner_version") != AUCTION_RUNNER:
                fail("comparison auction runner scope/version mismatch")
        profile_raw = files.get("embedded-profile.json")
        if profile_raw is None or hashlib.sha256(profile_raw).hexdigest() != binding["input_sha256"]["profile"]:
            fail("sealed embedded profile missing or changed")
        profile = decode(profile_raw)
        if not isinstance(profile, dict):
            fail("embedded profile object required")
    elif "embedded-profile.json" in files or any("/auction-evidence/" in n for n in files):
        fail("auction artifacts require qualified runner scope")
    for name, sha in binding["input_sha256"].items():
        if seal.get("inputs", {}).get(name, {}).get("sha256") != sha:
            fail("comparison sealed input mismatch")
    for variant in ("baseline", "candidate"):
        if seal.get("runtimes", {}).get(variant, {}).get("sha") != binding["revisions"][variant]:
            fail("comparison revision mismatch")
        config = decode(files[variant + "-config.json"])
        if not isinstance(config, dict):
            fail("comparison child config required")
        _int(config.get("source_offset_ms"), 43200000)
        if (config.get("variant") != variant or config.get("job_id") != case + "-" + variant or
                config.get("sha") != binding["revisions"][variant] or
                config.get("manifest_sha256") != binding["sealed_manifest_sha256"] or
                config.get("source_offset_ms") != binding["source_offset_ms"] or
                "gold" in config.get("inputs", {})):
            fail("comparison child binding mismatch")
        for name, sha in binding["input_sha256"].items():
            if name != "gold" and config.get("inputs", {}).get(name, {}).get("sha256") != sha:
                fail("comparison child input mismatch")
        prefix = variant + "/" + case + "-" + variant + "/"
        if prefix + "process.log" not in files:
            fail("comparison process log missing")
        event_name = prefix + "evidence/events.jsonl"
        events = [decode(line) for line in files.get(event_name, b"").splitlines()]
        captured = {}
        terminals = {}
        referenced = set()
        for event in events:
            if not isinstance(event, dict):
                fail("invalid comparison event")
            if event.get("event") == "FRAME_DECODED":
                item = event["evidence"]
                name = prefix + "evidence/" + safe_path(item["path"])
                if name not in files or hashlib.sha256(files[name]).hexdigest() != item["sha256"]:
                    fail("decoded frame missing or changed")
                referenced.add(name)
            if event.get("event") in {"BACKEND_RETURN", "BACKEND_ERROR"}:
                number = _int(event.get("attempt"), 512)
                if not number or number in terminals:
                    fail("duplicate backend outcome")
                terminals[number] = event
            if event.get("event") == "PAIR_CAPTURED":
                timestamps = event.get("observation_timestamps_ms")
                if not isinstance(timestamps, list) or len(timestamps) != 2:
                    fail("attempt observation timestamps missing")
                for timestamp in timestamps:
                    _int(timestamp, 120000)
                number = _int(event.get("attempt"), 512)
                if not number or number in captured or len(event.get("frames", [])) != 2:
                    fail("invalid attempted pair")
                names = []
                for i, item in enumerate(event["frames"]):
                    expected = f"attempts/{number:05d}/frame-{i}.png"
                    if item.get("path") != expected:
                        fail("attempt pair path mismatch")
                    name = prefix + "evidence/" + expected
                    if name not in files or hashlib.sha256(files[name]).hexdigest() != item["sha256"]:
                        fail("attempted or rejected frame missing")
                    names.append(name)
                    referenced.add(name)
                captured[number] = names
        if set(terminals) != set(captured):
            fail("attempt backend outcome missing")
        for number, event in terminals.items():
            result_name = prefix + f"evidence/attempts/{number:05d}/backend-result.json"
            if event["event"] == "BACKEND_RETURN":
                if result_name not in files:
                    fail("backend result missing")
                result = decode(files[result_name])
                if not isinstance(result, dict) or result.get("status") != event.get("status"):
                    fail("backend result outcome mismatch")
            elif result_name in files:
                fail("conflicting backend error and result")
        pair_pngs = {n for n in files if n.startswith(prefix + "evidence/attempts/") and n.endswith(".png")}
        if pair_pngs != {n for names in captured.values() for n in names}:
            fail("orphan attempted pair evidence")
        decoded = {n for n in files if n.startswith(prefix + "evidence/decoded/")}
        if decoded != {n for n in referenced if "/decoded/" in n}:
            fail("orphan decoded frame evidence")
        for n in files:
            if n.startswith(prefix + "evidence/attempts/") and n.endswith("backend-result.json"):
                if int(n.split("/")[-2]) not in captured:
                    fail("orphan backend result")
        run = comparison.get("runs", {}).get(variant)
        if not isinstance(run, dict):
            fail("comparison run missing")
        status_name = prefix + "worker-status.json"
        if run.get("status") not in {"RETURNED", "ERROR", "TIMEOUT", "PROCESS_ERROR"}:
            fail("unsupported comparison child state")
        status = decode(files[status_name]) if status_name in files else None
        if status is not None:
            if (not isinstance(status, dict) or status.get("variant") != variant or
                    status.get("job_id") != case + "-" + variant or
                    status.get("source_sha") != binding["revisions"][variant]):
                fail("comparison worker identity mismatch")
            if auction_scope and (status.get("scope") != AUCTION_SCOPE or
                    status.get("runner_version") != AUCTION_RUNNER or status.get("accuracy_evaluated") is not False):
                fail("comparison worker auction scope/version mismatch")
        if run["status"] in {"RETURNED", "ERROR"}:
            if status is None or run != {**status, "exit_code": run.get("exit_code")}:
                fail("comparison summary and worker receipt mismatch")
            if type(run.get("exit_code")) is not int:
                fail("comparison exit code required")
        if run["status"] == "RETURNED" or (status is not None and status.get("status") == "RETURNED"):
            if prefix + "result.json" not in files or event_name not in files:
                fail("returned comparison artifacts missing")
            _int(status.get("attempts"), 512)
            if (status.get("status") != "RETURNED" or status.get("attempts") != len(captured)):
                fail("comparison status count mismatch")
            recorded_pngs = {n for n in files if n.startswith(prefix + "evidence/") and n.endswith(".png")}
            if (_int(status.get("retained_frames"), 512) != len(recorded_pngs) or
                    _int(status.get("evidence_bytes"), 512 * 1024**2) != sum(len(files[n]) for n in recorded_pngs)):
                fail("comparison decoded/attempt evidence counters mismatch")
            duration = (status.get("metadata") or {}).get("duration_seconds")
            if type(duration) not in {int, float} or not 0 < duration <= 120:
                fail("comparison media duration missing or over quota")
        elif run["status"] == "ERROR" and status.get("status") != "ERROR":
            fail("comparison error receipt mismatch")
        if prefix + "result.json" in files:
            if status is None:
                fail("result without worker identity")
            validate_result(files, prefix, binding, variant, status, auction_scope=auction_scope, output=config.get("output"))
    runs = comparison.get("runs")
    if not isinstance(runs, dict) or set(runs) != {"baseline", "candidate"}:
        fail("exact comparison run inventory required")
    returned = all(r.get("status") == "RETURNED" and type(r.get("exit_code")) is int and r["exit_code"] == 0
                   for r in runs.values())
    if comparison["status"] != ("CAPTURED_UNSCORED" if returned else "REPLAY_ERROR"):
        fail("comparison summary state mismatch")


def validate_result(files, prefix, binding, variant, status, *, auction_scope=False, output=None):
    result = decode(files[prefix + "result.json"])
    versions = {"baseline": "bridgit-primary-video-gambler-v2",
                "candidate": "bridgit-primary-video-gambler-v2-observation-guards-candidate2"}
    if (not isinstance(result, dict) or result.get("version") not in ({versions[variant], AUCTION_VERSION} if auction_scope and variant == "candidate" else {versions[variant]}) or
            status.get("version") != result["version"] or
            status.get("result_status") != result.get("status") or
            result.get("status") not in {"PRIMARY_COMPLETE", "PRIMARY_PARTIAL_COVERAGE", "NO_FULL_LAYOUT_ACCEPTED"} or
            result.get("canonical_promotion_allowed") is not False or
            result.get("gambler_sprite_sha256") != binding["input_sha256"]["sprite"] or
            result.get("scan_ms") != 1000 or type(result.get("scan_ms")) is not int or
            result.get("attempt_gap_ms") != 15000 or type(result.get("attempt_gap_ms")) is not int or
            result.get("template_card_size") != {"width": 109.0, "height": 147.0}):
        fail("invalid primary visual result schema or settings")
    size = result.get("source_size")
    if not isinstance(size, dict) or set(size) != {"width", "height"}:
        fail("primary source size missing")
    if not all(1 <= _int(n, 16384) <= 16384 for n in size.values()):
        fail("primary source size invalid")
    deals = result.get("deals")
    if not isinstance(deals, list) or len(deals) > 64:
        fail("primary deal inventory invalid")
    if bool(deals) != (result["status"] != "NO_FULL_LAYOUT_ACCEPTED"):
        fail("primary result status/deal mismatch")
    screenshots = set()
    for deal in deals:
        if not isinstance(deal, dict) or deal.get("canonical_promotion_allowed") is not False:
            fail("primary deal schema invalid")
        timestamp = _int(deal.get("timestamp_ms"), 120000)
        expected = f"primary_{timestamp:010d}.png"
        path = deal.get("screenshot")
        if (not isinstance(path, str) or ".." in PurePosixPath(path).parts or
                not path.endswith("/recognizer/" + expected)):
            fail("unsafe primary screenshot locator")
        name = prefix + "recognizer/" + expected
        if (name in screenshots or name not in files or
                hashlib.sha256(files[name]).hexdigest() != _hex(deal.get("screenshot_sha256"))):
            fail("accepted screenshot missing or changed")
        screenshots.add(name)
    auction_names = {n for n in files if n.startswith(prefix + "recognizer/auction-evidence/")}
    if result["version"] == AUCTION_VERSION:
        validate_auction(files, prefix, binding, result.get("auction_recognition"), status, output)
    elif auction_names or "auction_recognition" in result or (auction_scope and status.get("auction_result_status") != "NOT_REPORTED"):
        fail("auction evidence requires candidate3 result")
    if screenshots != {n for n in files if n.startswith(prefix + "recognizer/") and n not in auction_names}:
        fail("primary screenshot complete inventory mismatch")


def validate_auction(files, prefix, binding, auction, status, output):
    """Validate retention/binding only; do not attest visual accuracy or bridge truth."""
    if (not isinstance(output, str) or len(output) > 4096 or
            not PurePosixPath(output).is_absolute() or str(PurePosixPath(output)) != output or
            ".." in PurePosixPath(output).parts or "\\" in output or
            not output.endswith("/candidate/" + binding["case_id"] + "-candidate")):
        fail("auction producer output binding invalid")
    names = {n for n in files if n.startswith(prefix + "recognizer/auction-evidence/")}
    profile = decode(files["embedded-profile.json"])
    calibration = profile.get("auction")
    if calibration is None:
        if auction != {"status": "UNAVAILABLE", "reason": "NO_AUCTION_PROFILE", "auctions": []} or names:
            fail("auction without sealed calibration")
        if status.get("auction_result_status") != "UNAVAILABLE":
            fail("auction worker/result status mismatch")
        return
    if not isinstance(calibration, dict) or calibration.get("schema") != "bridge-auction-cell-profile/v1":
        fail("unsupported sealed auction calibration")
    if (not isinstance(auction, dict) or auction.get("schema") != "bridge-visual-auction/v3" or
            auction.get("status") != "OBSERVED" or
            auction.get("canonical_promotion_allowed") is not False or
            status.get("auction_result_status") != auction.get("status") or
            auction.get("source_id") != binding["input_sha256"]["video"] or
            auction.get("calibration_profile") != calibration or
            auction.get("profile_sha256") != hashlib.sha256(json.dumps(calibration, sort_keys=True).encode()).hexdigest() or
            auction.get("reference_pixel_sha256") != calibration.get("reference_pixel_sha256") or
            auction.get("confidence_kind") != "UNCALIBRATED_TEMPLATE_SIMILARITY" or
            auction.get("supported_layout") != "CALIBRATED_FIXED_FOUR_COLUMN_TABLE_ONLY"):
        fail("auction schema/profile/source/status mismatch or incomplete retention")
    _hex(auction.get("reference_pixel_sha256"))
    issues = auction.get("coverage_issues")
    if not isinstance(issues, list) or len(issues) > 128 or not all(isinstance(x, str) for x in issues):
        fail("auction coverage inventory invalid")
    if issues:
        fail("auction coverage status mismatch")
    occurrences = auction.get("auctions")
    if not isinstance(occurrences, list) or len(occurrences) > 128:
        fail("auction occurrence quota")
    referenced = set()
    occurrence_ids = set()
    snapshot_bytes = 0
    for occurrence in occurrences:
        if (not isinstance(occurrence, dict) or occurrence.get("source_id") != binding["input_sha256"]["video"] or
                occurrence.get("canonical_promotion_allowed") is not False or type(occurrence.get("complete")) is not bool):
            fail("auction occurrence binding invalid")
        oid = _hex(occurrence.get("board_occurrence_id"))
        if oid in occurrence_ids:
            fail("duplicate auction occurrence")
        occurrence_ids.add(oid)
        _hex(occurrence.get("anchor_pixel_sha256"))
        start = _int(occurrence.get("start_ms"), 120000)
        end = _int(occurrence.get("end_ms"), 120000)
        if end < start:
            fail("auction occurrence interval invalid")
        observations = occurrence.get("observations")
        if not isinstance(observations, list) or len(observations) > 128:
            fail("auction snapshot quota")
        by_locator = {}
        last_timestamp = -1
        for observation in observations:
            if not isinstance(observation, dict):
                fail("auction observation required")
            timestamp = _int(observation.get("timestamp_ms"), 120000)
            pixels = _hex(observation.get("frame_pixel_sha256"))
            digest = _hex(observation.get("frame_sha256"))
            expected = f"auction-{timestamp:010d}-{pixels[:12]}.png"
            path = observation.get("frame_path")
            if (timestamp <= last_timestamp or not start <= timestamp <= end or not isinstance(path, str) or
                    ".." in PurePosixPath(path).parts or "\\" in path or
                    path != output + "/recognizer/auction-evidence/" + expected):
                fail("unsafe auction frame locator or timestamp")
            last_timestamp = timestamp
            name = prefix + "recognizer/auction-evidence/" + expected
            if name in referenced or name not in files or hashlib.sha256(files[name]).hexdigest() != digest:
                fail("auction snapshot missing, duplicate or changed")
            referenced.add(name)
            snapshot_bytes += len(files[name])
            if len(referenced) > MAX_AUCTION_SNAPSHOTS or snapshot_bytes > MAX_AUCTION_BYTES:
                fail("auction evidence quota")
            by_locator[(timestamp, digest, path)] = observation
        # Every secondary reference must bind to a retained observation in its occurrence.
        def references(value):
            if isinstance(value, dict):
                if "frame_path" in value:
                    key = (value.get("timestamp_ms"), value.get("frame_sha256"), value.get("frame_path"))
                    if key not in by_locator:
                        fail("auction secondary evidence reference mismatch")
                for item in value.values():
                    references(item)
            elif isinstance(value, list):
                for item in value:
                    references(item)
        references(occurrence)
    if referenced != names:
        fail("auction snapshot complete inventory mismatch")


def build_comparison_package(comparison_dir, destination, *, sealed_manifest_path,
                             sealed_manifest_sha256, runner_commit, runner_sha256,
                             job_id, job_hash, source_file_id, source_version, source_sha256,
                             clip_binding_path, clip_binding_sha256):
    """Build a lossless package; caller attaches declaration before server review."""
    root, destination = Path(comparison_dir), Path(destination)
    _hex(sealed_manifest_sha256); _hex(runner_commit, HEX40); _hex(runner_sha256)
    _hex(job_hash); _hex(source_sha256); _hex(clip_binding_sha256)
    if not isinstance(source_version, str) or not re.fullmatch(r"[1-9][0-9]*", source_version):
        fail("original version required")
    if not isinstance(source_file_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", source_file_id):
        fail("original source ID required")
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", job_id):
        fail("parent job ID required")
    manifest_raw = read(sealed_manifest_path, MAX_JSON_BYTES)
    if hashlib.sha256(manifest_raw).hexdigest() != sealed_manifest_sha256:
        fail("sealed comparison manifest changed")
    manifest = decode(manifest_raw)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("inputs"), dict) or set(manifest["inputs"]) != {"video", "reference", "profile", "sprite", "gold"}:
        fail("exact sealed comparison inputs required")
    case = manifest.get("case_id")
    if (manifest.get("schema") != RESULT_KIND or manifest.get("gold_frozen_before_outputs") is not True or
            not isinstance(case, str) or not CASE.fullmatch(case)):
        fail("sealed comparison gold required")
    revisions = {v: _hex(manifest[v]["sha"], HEX40) for v in ("baseline", "candidate")}
    inputs = {n: _hex(manifest["inputs"][n]["sha256"])
              for n in ("video", "reference", "profile", "sprite", "gold")}
    for item in manifest["inputs"].values():
        if hashlib.sha256(read(item["path"])).hexdigest() != item["sha256"]:
            fail("comparison input changed")
    binding = {"job_id": job_id, "job_hash": job_hash, "source_file_id": source_file_id,
               "source_version": source_version, "source_sha256": source_sha256,
               "case_id": case, "source_offset_ms": _int(manifest["source_offset_ms"], 43200000),
               "runner_commit": runner_commit, "runner_sha256": runner_sha256,
               "sealed_manifest_sha256": sealed_manifest_sha256,
               "clip_binding_sha256": clip_binding_sha256,
               "input_sha256": inputs, "revisions": revisions}
    names = inventory_paths(root, case)
    files, total = {}, 0
    for name in names:
        raw = read(root / name)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            fail("comparison total quota; evidence retained")
        files[name] = raw
    clip_receipt = read(clip_binding_path, MAX_JSON_BYTES)
    if hashlib.sha256(clip_receipt).hexdigest() != clip_binding_sha256:
        fail("source clip receipt seal mismatch")
    if "source-clip-binding.json" in files and files["source-clip-binding.json"] != clip_receipt:
        fail("conflicting clip binding")
    files["source-clip-binding.json"] = clip_receipt
    total += 0 if "source-clip-binding.json" in names else len(clip_receipt)
    if total > MAX_TOTAL_BYTES:
        fail("comparison clip receipt plus bytes quota")
    if decode(files["seal.json"]).get("scope") == AUCTION_SCOPE:
        profile_raw = read(manifest["inputs"]["profile"]["path"], MAX_JSON_BYTES)
        if "embedded-profile.json" in files and files["embedded-profile.json"] != profile_raw:
            fail("conflicting sealed embedded profile")
        if "embedded-profile.json" not in files:
            total += len(profile_raw)
        if total > MAX_TOTAL_BYTES:
            fail("comparison embedded profile plus bytes quota")
        files["embedded-profile.json"] = profile_raw
    validate_files(files, binding)
    # Recheck every source file and tree immediately before committing package.
    if names != inventory_paths(root, case) or any(read(root / n) != files[n] for n in names):
        fail("comparison tree changed")
    if (read(sealed_manifest_path, MAX_JSON_BYTES) != manifest_raw or
            read(clip_binding_path, MAX_JSON_BYTES) != clip_receipt or
            any(hashlib.sha256(read(item["path"])).hexdigest() != item["sha256"]
                for item in manifest["inputs"].values())):
        fail("comparison immutable inputs changed during build")
    checked(destination.parent, directory=True)
    if destination.exists() or destination.is_symlink():
        fail("comparison package destination already exists")
    if root.resolve() == destination.resolve() or root.resolve() in destination.resolve().parents:
        fail("comparison package must be outside source tree")
    destination.mkdir(mode=0o700)
    names = sorted(files)
    inventory, parts, pending, offset = [], [], bytearray(), 0
    def flush():
        raw = bytes(pending)
        name = f"part-{len(parts):05d}.bin"
        with (destination / name).open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        parts.append({"file": name, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        pending.clear()
    for name in names:
        raw = files[name]
        inventory.append({"path": name, "offset": offset, "size_bytes": len(raw),
                          "sha256": hashlib.sha256(raw).hexdigest()})
        offset += len(raw)
        for start in range(0, len(raw), PART_BYTES):
            remaining = raw[start:start + PART_BYTES]
            while remaining:
                space = PART_BYTES - len(pending)
                pending.extend(remaining[:space]); remaining = remaining[space:]
                if len(pending) == PART_BYTES:
                    flush()
    if pending:
        flush()
    index = {"schema": SCHEMA, "result_kind": RESULT_KIND, "binding": binding,
             "file_count": len(inventory), "total_bytes": offset, "files": inventory,
             "part_count": len(parts), "parts": parts, "accuracy_evaluated": False,
             "promotion_allowed": False}
    raw = encoded(index)
    if len(raw) > 1024**2 or offset + len(raw) > MAX_TOTAL_BYTES:
        fail("comparison index plus bytes quota; evidence retained")
    with (destination / "index.json").open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    if os.name != "nt":
        fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {"schema": SCHEMA, "directory": "comparison",
            "manifest_sha256": hashlib.sha256(raw).hexdigest()}


def collect_comparison_paths(job_dir, manifest):
    """Validate all parts AND every original file; return only typed package paths."""
    job_dir = Path(job_dir)
    declaration = manifest.get("comparison_artifacts")
    root = job_dir / "comparison"
    if declaration is not None or root.exists() or root.is_symlink():
        actual_manifest = decode(read(job_dir / "manifest.json", 5 * 1024**2))
        if actual_manifest != manifest:
            fail("comparison parent manifest changed")
    if declaration is None:
        if root.exists() or root.is_symlink():
            fail("undeclared comparison package")
        return []
    if (not isinstance(declaration, dict) or set(declaration) != {"schema", "directory", "manifest_sha256"} or
            declaration["schema"] != SCHEMA or declaration["directory"] != "comparison"):
        fail("invalid comparison package declaration")
    checked(root, directory=True)
    raw = read(root / "index.json", 1024**2)
    if hashlib.sha256(raw).hexdigest() != _hex(declaration["manifest_sha256"]):
        fail("comparison index changed")
    index = decode(raw)
    keys = {"schema", "result_kind", "binding", "file_count", "total_bytes", "files",
            "part_count", "parts", "accuracy_evaluated", "promotion_allowed"}
    if (not isinstance(index, dict) or set(index) != keys or index["schema"] != SCHEMA or
            index["result_kind"] != RESULT_KIND or index["accuracy_evaluated"] is not False or
            index["promotion_allowed"] is not False):
        fail("unsupported comparison package")
    binding = index["binding"]
    if not isinstance(binding, dict):
        fail("comparison binding object required")
    if (binding.get("job_id") != manifest.get("job_id") or binding.get("job_hash") != manifest.get("job_hash") or
            binding.get("source_file_id") != (manifest.get("source") or {}).get("file_id") or
            binding.get("source_sha256") != (manifest.get("media") or {}).get("sha256")):
        fail("comparison source or parent job mismatch")
    if (binding.get("source_version") != str((manifest.get("source") or {}).get("version")) or
            not isinstance(binding.get("source_version"), str) or
            not re.fullmatch(r"[1-9][0-9]*", binding["source_version"])):
        fail("comparison source version mismatch")
    for key in ("job_hash", "source_sha256", "runner_sha256", "sealed_manifest_sha256", "clip_binding_sha256"):
        _hex(binding.get(key))
    _hex(binding.get("runner_commit"), HEX40)
    if not isinstance(binding.get("case_id"), str) or not CASE.fullmatch(binding["case_id"]):
        fail("invalid comparison case")
    _int(binding.get("source_offset_ms"), 43200000)
    parts, inventory = index["parts"], index["files"]
    if not isinstance(parts, list) or not isinstance(inventory, list):
        fail("invalid comparison inventory")
    if _int(index["part_count"], 64) != len(parts) or _int(index["file_count"], MAX_FILES) != len(inventory):
        fail("comparison count mismatch")
    total = _int(index["total_bytes"])
    if total + len(raw) > MAX_TOTAL_BYTES:
        fail("comparison combined quota")
    expected = {"index.json"} | {f"part-{i:05d}.bin" for i in range(len(parts))}
    if {p.name for p in root.iterdir()} != expected:
        fail("comparison part inventory mismatch")
    content = bytearray()
    for i, item in enumerate(parts):
        if not isinstance(item, dict) or set(item) != {"file", "size_bytes", "sha256"} or item["file"] != f"part-{i:05d}.bin":
            fail("unsafe comparison part")
        piece = read(root / item["file"], PART_BYTES)
        if (len(piece) != _int(item["size_bytes"], PART_BYTES) or not piece or
                hashlib.sha256(piece).hexdigest() != _hex(item["sha256"])):
            fail("comparison part hash/size mismatch")
        if i < len(parts) - 1 and len(piece) != PART_BYTES:
            fail("noncanonical comparison part")
        content.extend(piece)
        if len(content) > total:
            fail("comparison byte count mismatch")
    if len(content) != total:
        fail("comparison bytes missing")
    files, position = {}, 0
    for row in inventory:
        if not isinstance(row, dict) or set(row) != {"path", "offset", "size_bytes", "sha256"}:
            fail("invalid comparison file record")
        name = safe_path(row["path"])
        if name in files or name.casefold() in {n.casefold() for n in files}:
            fail("duplicate comparison file")
        size = _int(row["size_bytes"])
        if _int(row["offset"]) != position or position + size > total:
            fail("comparison file offset mismatch")
        file_raw = bytes(content[position:position + size])
        if hashlib.sha256(file_raw).hexdigest() != _hex(row["sha256"]):
            fail("comparison original file hash mismatch")
        files[name] = file_raw
        position += size
    if position != total or list(files) != sorted(files):
        fail("comparison complete canonical inventory required")
    validate_files(files, binding)
    return [root / "index.json", *(root / p["file"] for p in parts)]


def verify_comparison_original(job_dir, source):
    """Bind typed package to the independently re-read immutable original pin."""
    root = Path(job_dir) / "comparison"
    if not root.exists():
        return
    binding = _json(root / "index.json")["binding"]
    if (binding["source_file_id"] != source["file_id"] or
            binding["source_version"] != str(source["before"]["version"]) or
            binding["source_sha256"] != source["sha256_before"]):
        fail("comparison original intake pin mismatch")
