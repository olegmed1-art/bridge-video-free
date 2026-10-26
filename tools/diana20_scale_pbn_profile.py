"""Explicit source-only trust profile for the installed-Margin scale bundle.

No imports or execution of authenticated source bundles. The external profile
digest is a trust input, never obtained from a recognizer response. This proves
saved byte compatibility, not installation, execution or recognition accuracy.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from bridge_vision.gambler_classic_reference import VARIANT_CARD_SIZE
from bridge_vision.gambler_reference_authority import pinned_sprite_sha256
from tools.diana20_scale_contract import validate_native_scale_response

PROFILE_VERSION = "diana20-scale-pbn-source-profile/v1-installed-margin"
BUNDLE_VERSION = "diana20-pixel-scale-source-v1-installed-margin"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SEAL = object()


def _need(ok, reason):
    if not ok:
        raise ValueError("DIANA20_SCALE_PBN_" + reason)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result, "DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("DIANA20_SCALE_PBN_NONFINITE")


def _json(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)


def _read(path, digest, size=None, limit=4 * 1024 * 1024):
    _need(type(digest) is str and bool(_SHA.fullmatch(digest)), "HASH_FORMAT")
    path = Path(path)
    _need(not any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                  for p in (path, *path.parents)), "LINKED_PATH")
    _need(path.is_file() and 0 < path.stat().st_size <= limit, "FILE_SIZE")
    raw = path.read_bytes()
    _need(len(raw) <= limit and (size is None or len(raw) == size)
          and _hash(raw) == digest, "FILE_HASH")
    return raw


def _files(root, manifest):
    entries = manifest.get("files")
    _need(type(entries) is list and 0 < len(entries) <= 64, "FILES")
    result, checks = {}, []
    for item in entries:
        _need(type(item) is dict and set(item) == {"path", "sha256", "bytes"}, "FILE_ENTRY")
        name = item["path"]
        _need(type(name) is str and bool(name) and "\\" not in name
              and ":" not in name and not PurePosixPath(name).is_absolute()
              and all(p not in ("", ".", "..") for p in name.split("/"))
              and name not in result, "FILE_PATH")
        _need(type(item["bytes"]) is int and item["bytes"] > 0, "FILE_BYTES")
        path = Path(root) / name
        result[name] = _read(path, item["sha256"], item["bytes"])
        checks.append((str(path.absolute()), item["sha256"], item["bytes"], 4 * 1024 * 1024))
    return result, checks


def _literal(raw, name):
    values = [n.value for n in ast.parse(raw).body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    _need(len(values) == 1, "SOURCE_CONSTANT")
    return ast.literal_eval(values[0])


@dataclass(frozen=True, init=False)
class VerifiedScalePBNProfile:
    """Immutable source snapshot capability; only the loader constructs it."""
    profile_sha256: str
    runtime_json: bytes
    vendor_json: bytes
    checks: tuple
    source_sha256: str
    _seal: object

    def __init__(self, *, _seal=None, **fields):
        _need(_seal is _SEAL, "VERIFIED_PROFILE_REQUIRED")
        for key, value in fields.items():
            object.__setattr__(self, key, value)
        object.__setattr__(self, "_seal", _seal)

    def verify_payload(self, payload):
        _need(self._seal is _SEAL, "VERIFIED_PROFILE_REQUIRED")
        # Rehash the snapshot at use time: a loaded profile is not a permission
        # to consume changed files later. These are source bytes, not live pins.
        for path, digest, size, limit in self.checks:
            _read(path, digest, size, limit)
        pins = {v: (pinned_sprite_sha256(v), *size) for v, size in VARIANT_CARD_SIZE.items()}
        _need(validate_native_scale_response(payload, pins), "SCALE_RESPONSE")
        status = payload["card_scale"]["selection_status"]
        if status in ("UNKNOWN", "TIE"):
            _need(payload.get("status") == "SCALE_" + status and payload.get("recognizer_version") is None,
                  "EMPTY_SCALE_STATUS")
        else:
            _need(payload.get("status") not in ("SCALE_UNKNOWN", "SCALE_TIE"), "EMPTY_SCALE_STATUS")
        runtime = payload["recognizer_update"]["runtime"]
        expected = _json(self.runtime_json)
        for key, value in expected.items():
            _need(runtime.get(key) == value and (type(value) is not bool or type(runtime.get(key)) is bool),
                  "RUNTIME_PIN_" + key)
        modules = runtime.get("vendor_modules")
        allowed = _json(self.vendor_json)
        _need(type(modules) is dict and set(modules) == set(allowed), "VENDOR_MODULES")
        for name, value in modules.items():
            _need(value == allowed[name], "VENDOR_PIN")
        _need(payload.get("source_video_sha256") == self.source_sha256, "SOURCE_BINDING")
        return expected

    def provenance(self):
        return {"profile_version": PROFILE_VERSION, "profile_sha256": self.profile_sha256,
                "source_files_verified": len(self.checks), "runtime_qualified": False,
                "installation_verified": False, "runtime_status": "HOLD"}


def load_scale_pbn_profile(profile_path, *, expected_sha256, bundle_root, caller_root, asset_root):
    """Authenticate an explicitly chosen saved source profile and all payloads.

    The profile is private deployment evidence. Its SHA must be provided by the
    caller's trusted configuration, independently of input rows and manifests.
    No auto-discovery, legacy fallback, wildcard digest or tie-mode admission.
    """
    raw = _read(profile_path, expected_sha256)
    profile = _json(raw)
    _need(type(profile) is dict and set(profile) == {"profile_version", "bundle_manifest_sha256",
          "caller_proposal_sha256", "runtime_root", "source_sha256"}, "PROFILE_FIELDS")
    _need(profile["profile_version"] == PROFILE_VERSION, "PROFILE_VERSION")
    root = profile["runtime_root"]
    _need(type(root) is str and root.startswith("/") and not root.startswith("//")
          and str(PurePosixPath(root)) == root and ".." not in root.split("/")
          and "\\" not in root and root.endswith("/" + BUNDLE_VERSION), "RUNTIME_ROOT")
    _need(type(profile["source_sha256"]) is str and bool(_SHA.fullmatch(profile["source_sha256"])), "SOURCE_SHA")
    checks = [(str(Path(profile_path).absolute()), expected_sha256, len(raw), 4 * 1024 * 1024)]
    loaded = []
    for directory, name, key in ((bundle_root, "SOURCE_BUNDLE.json", "bundle_manifest_sha256"),
                                  (caller_root, "CALLER_PROPOSAL.json", "caller_proposal_sha256")):
        path = Path(directory) / name
        content = _read(path, profile[key])
        manifest = _json(content)
        files, file_checks = _files(directory, manifest)
        checks += [(str(path.absolute()), profile[key], len(content), 4 * 1024 * 1024), *file_checks]
        loaded.append((manifest, files))
    (bundle, files), (caller, callers) = loaded
    duplicate = Path(bundle_root) / "CODE_BUNDLE.json"
    duplicate_raw = _read(duplicate, profile["bundle_manifest_sha256"])
    checks.append((str(duplicate.absolute()), profile["bundle_manifest_sha256"], len(duplicate_raw), 4 * 1024 * 1024))
    _need(bundle.get("schema") == "diana20-pixel-scale-source-bundle/v1"
          and bundle.get("version") == BUNDLE_VERSION and bundle.get("base") == "installed-margin", "BUNDLE_VERSION")
    _need(all(bundle.get(k) is False for k in ("geometry_enabled", "factor_identity_fix_enabled", "installed",
          "installation_allowed", "automatic_promotion_allowed", "assets_included", "lifecycle_or_active_entry_included")), "BUNDLE_AUTHORITY")
    _need(caller.get("schema") == "diana20-scale-caller-proposal/v1" and caller.get("applied") is False
          and caller.get("owner_lifecycle_dispatch_changed") is False and caller.get("target") == root
          and caller.get("bundle_manifest_sha256") == profile["bundle_manifest_sha256"], "CALLER_BINDING")
    _need(set(callers) == {"CURRENT_RECOGNIZER.proposed.json", "ops/diana20-recognizer-component.json",
          "universal_video/diana20_prepared_frames.py", "universal_video/diana20_recognizer.py"}, "CALLER_FILES")
    worker = files["geometry/diana20_geometry_worker.py"]
    _need(_literal(worker, "VERSION") == BUNDLE_VERSION and _hash(worker) == bundle.get("worker_sha256"), "WORKER_VERSION_HASH")
    helpers = _literal(worker, "HELPERS")
    _need(set(helpers) == {"component_margin_candidate.py", "release_layer.py", "release_scoring.py"}, "HELPERS")
    for name, digest in helpers.items():
        _need(_hash(files["geometry/" + name]) == digest, "HELPER_HASH")
    vendor_raw = files["geometry/CARD_WORKER_PINS.json"]
    _need(_hash(vendor_raw) == bundle.get("vendor_pins_sha256"), "VENDOR_MANIFEST")
    vendor = _json(vendor_raw)
    frozen = _literal(worker, "FROZEN")
    _need({i["path"]: i["sha256"] for i in vendor["files"]} == frozen
          and len(vendor["files"]) == len(frozen), "VENDOR_FROZEN")
    _need(set(files) == {"geometry/diana20_geometry_worker.py", "geometry/CARD_WORKER_PINS.json",
          "runtime/diana20_visual_ipc.py", "runtime/diana20_release_layer.py"}
          | {"geometry/" + name for name in helpers} | {"geometry/vendor/" + name for name in frozen}
          and len(files) == 24 and len(frozen) == 17, "BUNDLE_CLOSURE")
    _need(files["runtime/diana20_release_layer.py"] == files["geometry/release_layer.py"], "LAYER_COPY")
    vendor_modules = {}
    for item in vendor["files"]:
        name = item["path"]
        data = files["geometry/vendor/" + name]
        _need(_hash(data) == item["sha256"] and len(data) == item["bytes"], "VENDOR_HASH")
        if name.endswith(".py"):
            module = name.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
            vendor_modules[module] = {"path": root + "/geometry/vendor/" + name, "sha256": item["sha256"]}
    component = _json(callers["ops/diana20-recognizer-component.json"])
    _need(_literal(callers["universal_video/diana20_recognizer.py"], "COMPONENT_SHA256")
          == _hash(callers["ops/diana20-recognizer-component.json"]), "CALLER_COMPONENT_HASH")
    pointer = _json(callers["CURRENT_RECOGNIZER.proposed.json"])
    _need(pointer == {"version": BUNDLE_VERSION, "code_root": root,
          "manifest_sha256": profile["bundle_manifest_sha256"]}, "POINTER_BINDING")
    expected = {"version": BUNDLE_VERSION, "worker_path": root + "/geometry/diana20_geometry_worker.py",
                "worker_sha256": bundle["worker_sha256"], "margin_enabled": True, "geometry_enabled": False,
                "factor_identity_fix_enabled": False, "scale_selection_version": "diana20-pixel-native-scale-v1"}
    _need(component.get("runtime") == expected and component.get("dispatch_enabled") is False
          and component.get("source_sha256") == profile["source_sha256"]
          and component.get("pins_sha256") == bundle["vendor_pins_sha256"]
          and component.get("pins") == root + "/geometry/CARD_WORKER_PINS.json"
          and component.get("vendor") == root + "/geometry/vendor", "COMPONENT_BINDING")
    _need(all(type(component["runtime"][k]) is bool for k in
              ("margin_enabled", "geometry_enabled", "factor_identity_fix_enabled")), "COMPONENT_FLAG_TYPES")
    _need(component.get("asset_root") == root + "/assets" and component.get("sprite") == root + "/assets/all-v1.png"
          and component.get("authority") == {"canonical": False, "complete_deal": False, "human_verified": False}
          and all(v is False for v in component["authority"].values()), "COMPONENT_AUTHORITY_ASSETS")
    component_files = component.get("files")
    _need(type(component_files) is list and len(component_files) == 12
          and all(type(item) is dict for item in component_files), "COMPONENT_FILES")
    by_path = {item.get("path"): item for item in component_files}
    _need(len(by_path) == 12, "COMPONENT_FILE_DUPLICATE")
    local_claims = {root + "/geometry/diana20_geometry_worker.py": bundle["worker_sha256"],
                    root + "/geometry/CARD_WORKER_PINS.json": bundle["vendor_pins_sha256"]}
    local_claims.update({root + f"/assets/all-v{v}.png": pinned_sprite_sha256(v) for v in VARIANT_CARD_SIZE})
    for path, digest in local_claims.items():
        _need(path in by_path and by_path[path].get("sha256") == digest, "COMPONENT_FILE_PIN")
    # The two remaining entries are lifecycle dependencies, not included in the
    # source kit. Preserve HOLD explicitly; do not claim they were verified.
    external = [v for k, v in by_path.items() if k not in local_claims]
    _need(len(external) == 2 and all(v.get("private") is True for v in external), "EXTERNAL_DEPENDENCIES")
    current = [v for v in external if str(v.get("path", "")).endswith("/CURRENT_RECOGNIZER.json")]
    active = [v for v in external if str(v.get("path", "")).endswith("/ACTIVE_RECOGNIZER.py")]
    _need(len(current) == len(active) == 1
          and current[0].get("sha256") == _hash(callers["CURRENT_RECOGNIZER.proposed.json"])
          and type(active[0].get("sha256")) is str and bool(_SHA.fullmatch(active[0]["sha256"])), "EXTERNAL_POINTER_BINDING")
    entry = component.get("entry")
    _need(type(entry) is str and entry.startswith("/") and str(PurePosixPath(entry)) == entry
          and ".." not in entry.split("/") and active[0]["path"] == entry
          and current[0]["path"] == str(PurePosixPath(entry).parent / "CURRENT_RECOGNIZER.json"), "EXTERNAL_ENTRY_BINDING")
    ipc = files["runtime/diana20_visual_ipc.py"]
    _need(_literal(ipc, "GEOMETRY_WORKER_SHA") == bundle["worker_sha256"]
          and _literal(ipc, "GEOMETRY_PINS_SHA") == bundle["vendor_pins_sha256"]
          and _literal(ipc, "SOURCE_SHA") == profile["source_sha256"], "IPC_BINDING")
    pins = {v: (pinned_sprite_sha256(v), *size) for v, size in VARIANT_CARD_SIZE.items()}
    _need(_literal(ipc, "NATIVE_SCALE_PINS") == pins
          and _literal(callers["universal_video/diana20_recognizer.py"], "NATIVE_SCALE_PINS") == pins, "CALLER_SPRITE_PINS")
    expected.update(layer_version=_literal(files["geometry/release_layer.py"], "VERSION"),
                    candidate_version=_literal(files["geometry/component_margin_candidate.py"], "VERSION"),
                    helpers={name: {"path": root + "/geometry/" + name, "sha256": digest} for name, digest in helpers.items()})
    _need(expected["layer_version"] == "diana20-installed-margin-r1-20261020", "LAYER_VERSION")
    for variant in VARIANT_CARD_SIZE:
        path = Path(asset_root) / str(variant) / "all.png"
        digest = pinned_sprite_sha256(variant)
        data = _read(path, digest, limit=16 * 1024 * 1024)
        checks.append((str(path.absolute()), digest, len(data), 16 * 1024 * 1024))
    return VerifiedScalePBNProfile(_seal=_SEAL, profile_sha256=expected_sha256,
        runtime_json=json.dumps(expected, sort_keys=True).encode(), vendor_json=json.dumps(vendor_modules, sort_keys=True).encode(),
        checks=tuple(checks), source_sha256=profile["source_sha256"])
