"""PBN factory seam tests with an explicit synthetic binding-provider double.

No POSIX ownership, caller, worker or server qualification is claimed here.
Video owns the real StagedBinding implementation and its metadata tests.
"""
import copy
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from tools import diana20_scale_pbn_profile as m
from tools.diana20_cards_pbn_adapter import adapt_diana20_row
from tests.test_diana20_scale_pbn_profile import kit, scale_row, rebind


CONTENT_SHA = "04802ed4f4f5cb0d86b00aafb9014bd3cd5962382febf75333114125d1b2ae83"
POLICY = "ROOT_OWNED_IMMUTABLE_INPUTS_EXECUTOR_993_985_V1"


@pytest.fixture
def stage(kit, tmp_path, monkeypatch):
    root = tmp_path
    path = root / "SOURCE_SCALE_PROFILE.json"
    path.write_bytes(kit["path"].read_bytes())
    # Only synthetic source fixtures replace this one compiled content-entry pin.
    # The real frozen payload check uses the unmodified constant separately.
    monkeypatch.setattr(m, "STAGED_PROFILE_SHA256", kit["options"]["expected_sha256"], raising=False)
    api = ModuleType("video_adapters.diana20.staged_binding")
    class StagedBinding:
        def __init__(self):
            self.staged_root = str(root)
            self.run_id = "SYNTHETIC_PBN_FACTORY_ONLY"
            self.source_video_sha256 = kit["verified"].source_sha256
            self.content_manifest_sha256 = CONTENT_SHA
            self.target_binding_sha256 = "7" * 64
            self.owner_policy = POLICY
            self.calls = 0
            self.failure = None
        def recheck(self):
            self.calls += 1
            if self.failure: raise ValueError("SYNTHETIC_BINDING_" + self.failure)
    api.StagedBinding = StagedBinding
    monkeypatch.setitem(sys.modules, api.__name__, api)
    binding = StagedBinding()
    return {"kit": kit, "path": path, "binding": binding, "api": api,
            "args": {"expected_sha256": kit["options"]["expected_sha256"],
                     "staged_binding": binding, "expected_run_id": binding.run_id}}


def load(stage, **changes):
    return m.load_staged_scale_pbn_profile(stage["path"], **{**stage["args"], **changes})


def staged_row(stage, profile, **kwargs):
    row = scale_row(stage["kit"], **kwargs)
    runtime = row["payload"]["recognizer_update"]["runtime"]
    runtime.update(json.loads(profile.runtime_json))
    runtime["vendor_modules"] = json.loads(profile.vendor_json)
    rebind(row)
    return row


def test_exact_existing_capability_and_only_paths_rebound(stage):
    profile = load(stage)
    assert type(profile) is m.VerifiedScalePBNProfile
    old = json.loads(stage["kit"]["verified"].runtime_json)
    new = json.loads(profile.runtime_json)
    assert set(old) == set(new)
    for key in old:
        if key not in ("worker_path", "helpers"): assert new[key] == old[key]
    root = str(Path(stage["binding"].staged_root) / "bundle" / "geometry")
    assert new["worker_path"] == str(Path(root) / "diana20_geometry_worker.py")
    for name, info in new["helpers"].items():
        assert info["sha256"] == old["helpers"][name]["sha256"]
        assert info["path"] == str(Path(root) / name)
    before_vendor = json.loads(stage["kit"]["verified"].vendor_json)
    after_vendor = json.loads(profile.vendor_json)
    assert set(before_vendor) == set(after_vendor)
    assert all(after_vendor[k]["sha256"] == before_vendor[k]["sha256"] for k in before_vendor)
    assert profile.provenance()["runtime_qualified"] is False
    assert profile.provenance()["installation_verified"] is False
    assert profile.provenance()["runtime_status"] == "HOLD"


@pytest.mark.parametrize("status", ["SELECTED", "UNKNOWN", "TIE"])
def test_existing_adapter_retains_unknown_and_never_owns_cards(stage, status):
    profile = load(stage)
    row = staged_row(stage, profile, status=status)
    before = copy.deepcopy(row)
    record, claims = adapt_diana20_row(row, scale_profile=profile)
    assert record["deal"] is None and row == before
    assert claims["effective_unknown"] == row["payload"]["unknown"]
    assert claims["scale_source_profile"]["runtime_qualified"] is False


def test_binding_rechecked_at_each_use(stage):
    profile = load(stage)
    row = staged_row(stage, profile)
    before = stage["binding"].calls
    profile.verify_payload(row["payload"])
    assert stage["binding"].calls > before
    for cause in ("OWNER", "MODE", "PROCESS_UID", "WORK_MODE", "MANIFEST_BYTES"):
        stage["binding"].failure = cause
        with pytest.raises(ValueError, match="SYNTHETIC_BINDING_" + cause): profile.verify_payload(row["payload"])
    stage["binding"].failure = None


def test_post_read_metadata_change_rejected(stage, monkeypatch):
    profile = load(stage)
    row = staged_row(stage, profile)
    original = m._read
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        stage["binding"].failure = "MODE_CHANGED_DURING_SOURCE_READ"
        return result
    monkeypatch.setattr(m, "_read", changed)
    with pytest.raises(ValueError, match="MODE_CHANGED_DURING_SOURCE_READ"):
        profile.verify_payload(row["payload"])


@pytest.mark.parametrize("change", ["dict", "subclass", "missing_module"])
def test_no_duck_type_or_unsafe_missing_provider_fallback(stage, monkeypatch, change):
    binding = stage["binding"]
    if change == "dict": binding = dict(vars(binding))
    if change == "subclass":
        class Sub(stage["api"].StagedBinding): pass
        binding = Sub()
    if change == "missing_module": monkeypatch.setitem(sys.modules, stage["api"].__name__, None)
    with pytest.raises(ValueError, match="STAGED_BINDING"):
        load(stage, staged_binding=binding)


@pytest.mark.parametrize("field,value", [("content_manifest_sha256", "8"*64),
    ("source_video_sha256", "8"*64), ("owner_policy", "ANY_OWNER"), ("run_id", "WRONG_RUN")])
def test_independent_binding_identity_mismatch(stage, field, value):
    setattr(stage["binding"], field, value)
    with pytest.raises(ValueError, match="STAGED_"): load(stage)


def test_binding_identity_cannot_change_after_factory(stage):
    profile = load(stage)
    row = staged_row(stage, profile)
    stage["binding"].target_binding_sha256 = "8"*64
    with pytest.raises(ValueError, match="STAGED_"): profile.verify_payload(row["payload"])


def test_profile_cannot_be_read_from_alternate_root(stage, tmp_path):
    outside = tmp_path / "other-profile.json"
    outside.write_bytes(stage["path"].read_bytes())
    with pytest.raises(ValueError, match="STAGED_"):
        m.load_staged_scale_pbn_profile(outside, **stage["args"])


def test_live_entries_are_never_opened(stage, monkeypatch):
    original = Path.open
    def no_live(path, *args, **kwargs):
        assert path.name not in ("ACTIVE_RECOGNIZER.py", "CURRENT_RECOGNIZER.json")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", no_live)
    profile = load(stage)
    profile.verify_payload(staged_row(stage, profile)["payload"])


@pytest.mark.parametrize("kind", ["worker", "helper", "vendor"])
def test_legacy_runtime_paths_not_accepted_as_staged(stage, kind):
    profile = load(stage)
    row = staged_row(stage, profile)
    old = json.loads(stage["kit"]["verified"].runtime_json)
    runtime = row["payload"]["recognizer_update"]["runtime"]
    if kind == "worker": runtime["worker_path"] = old["worker_path"]
    elif kind == "helper": runtime["helpers"] = old["helpers"]
    else: runtime["vendor_modules"] = json.loads(stage["kit"]["verified"].vendor_json)
    with pytest.raises(ValueError): profile.verify_payload(row["payload"])


def test_frozen_worker_bytes_still_rehashed_at_use(stage):
    profile = load(stage)
    row = staged_row(stage, profile)
    worker = Path(stage["binding"].staged_root) / "bundle/geometry/diana20_geometry_worker.py"
    worker.write_bytes(worker.read_bytes() + b"# MUTANT\n")
    with pytest.raises(ValueError, match="FILE_HASH"): profile.verify_payload(row["payload"])


def test_scale_profile_cannot_carry_ad_owner_between_adjacent_frames(stage):
    profile = load(stage)
    for index in (25949, 25950):
        row = staged_row(stage, profile)
        row["sequence"] = index
        row["receipt"]["timestamp_ms"] = row["payload"]["source_timestamp_ms"] = index*40
        row["receipt"]["frame_id"] = f"SYNTHETIC-{index}"
        row["payload"]["cards"][0]["card"] = "AD"
        row["payload"]["cards"][0]["seat"] = "E"
        row["payload"]["recognizer_update"]["effective_cards"] = copy.deepcopy(row["payload"]["cards"])
        rebind(row)
        record, claims = adapt_diana20_row(row, scale_profile=profile)
        assert record["deal"] is None
        assert claims["effective_cards"][0]["status"] == "MACHINE_HAND_CANDIDATE"
        assert claims["scale_source_profile"]["runtime_qualified"] is False
