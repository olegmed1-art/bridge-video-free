"""Source/profile contracts using explicitly synthetic byte fixtures.

No worker, video, geometry or transport execution. Real saved source snapshots
are authenticated separately by the private local evidence protocol.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools import diana20_scale_pbn_profile as profile_module
from tools.diana20_cards_pbn_adapter import adapt_diana20_row, export_diana20_jsonl, installed_runtime_pins
from tests.test_diana20_cards_pbn import row_fixture, encoded


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(root, name, raw):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return dict(path=name, bytes=len(raw), sha256=sha(raw))


@pytest.fixture
def kit(tmp_path, monkeypatch):
    bundle, caller, assets = (tmp_path / n for n in ("bundle", "caller", "assets"))
    version = profile_module.BUNDLE_VERSION
    target = "/synthetic/code-bundles/" + version
    source = row_fixture()["payload"]["source_video_sha256"]
    sprite_pins = {}
    for variant in profile_module.VARIANT_CARD_SIZE:
        raw = ("SYNTHETIC_SPRITE_BYTES_" + str(variant)).encode()
        save(assets, f"{variant}/all.png", raw)
        sprite_pins[variant] = sha(raw)
    monkeypatch.setattr(profile_module, "pinned_sprite_sha256", sprite_pins.__getitem__)
    pins = {v: (sprite_pins[v], *size) for v, size in profile_module.VARIANT_CARD_SIZE.items()}
    helpers = {"component_margin_candidate.py": b"VERSION = 'SYNTHETIC_CANDIDATE'\n",
               "release_layer.py": b"VERSION = 'diana20-installed-margin-r1-20261020'\n",
               "release_scoring.py": b"# SYNTHETIC_SCORER_NOT_EXECUTED\n"}
    files = [save(bundle, "geometry/" + name, raw) for name, raw in helpers.items()]
    vendor_names = ["bridge_contracts/__init__.py", "bridge_contracts/video_deal.py", "bridge_vision/__init__.py",
        "bridge_vision/anchor_registration.py", "bridge_vision/bridgit_compass_mapping_candidate2.py",
        "bridge_vision/bridgit_diana20_layout.py", "bridge_vision/bridgit_geometry_candidate.py",
        "bridge_vision/bridgit_partial_pixel_observer.py", "bridge_vision/bridgit_rank_suit_candidate.py",
        "bridge_vision/diana20_gambler_scale.py", "bridge_vision/diana20_layout_profile.json",
        "bridge_vision/diana20_layout_ui_pins.json", "bridge_vision/gambler_card_scale.py",
        "bridge_vision/gambler_classic_reference.py", "bridge_vision/gambler_reference_authority.py",
        "bridge_vision/native_cards.py", "bridge_vision/profiled_challenger.py"]
    vendor = []
    for name in vendor_names:
        item = save(bundle, "geometry/vendor/" + name, b"{}\n" if name.endswith(".json") else b"# SYNTHETIC_NOT_EXECUTED\n")
        files.append(item)
        vendor.append(dict(item, path=name))
    worker = (f"VERSION = {version!r}\nHELPERS = { {k:sha(v) for k,v in helpers.items()}!r}\n"
              f"FROZEN = { {i['path']:i['sha256'] for i in vendor}!r}\n").encode()
    files.append(save(bundle, "geometry/diana20_geometry_worker.py", worker))
    vendor_raw = encoded(dict(schema="diana20-card-worker-pins-v1", files=vendor))
    files.append(save(bundle, "geometry/CARD_WORKER_PINS.json", vendor_raw))
    ipc = (f"GEOMETRY_WORKER_SHA = {sha(worker)!r}\nGEOMETRY_PINS_SHA = {sha(vendor_raw)!r}\n"
           f"SOURCE_SHA = {source!r}\nNATIVE_SCALE_PINS = {pins!r}\n").encode()
    files.append(save(bundle, "runtime/diana20_visual_ipc.py", ipc))
    files.append(save(bundle, "runtime/diana20_release_layer.py", helpers["release_layer.py"]))
    manifest = dict(schema="diana20-pixel-scale-source-bundle/v1", version=version, base="installed-margin",
        worker_sha256=sha(worker), vendor_pins_sha256=sha(vendor_raw), files=files,
        **{k: False for k in ("geometry_enabled", "factor_identity_fix_enabled", "installed", "installation_allowed",
                             "automatic_promotion_allowed", "assets_included", "lifecycle_or_active_entry_included")})
    raw = encoded(manifest)
    save(bundle, "SOURCE_BUNDLE.json", raw)
    save(bundle, "CODE_BUNDLE.json", raw)
    pointer = encoded(dict(version=version, code_root=target, manifest_sha256=sha(raw)))
    runtime = dict(version=version, worker_path=target + "/geometry/diana20_geometry_worker.py",
        worker_sha256=sha(worker), margin_enabled=True, geometry_enabled=False, factor_identity_fix_enabled=False,
        scale_selection_version="diana20-pixel-native-scale-v1")
    component_files = [dict(path=target + "/geometry/diana20_geometry_worker.py", sha256=sha(worker)),
                       dict(path=target + "/geometry/CARD_WORKER_PINS.json", sha256=sha(vendor_raw))]
    component_files += [dict(path=target + f"/assets/all-v{v}.png", sha256=d) for v,d in sprite_pins.items()]
    component_files += [dict(path="/synthetic/ACTIVE_RECOGNIZER.py", sha256="e"*64, private=True),
                        dict(path="/synthetic/CURRENT_RECOGNIZER.json", sha256=sha(pointer), private=True)]
    component = encoded(dict(runtime=runtime, dispatch_enabled=False, source_sha256=source, entry="/synthetic/ACTIVE_RECOGNIZER.py",
        pins_sha256=sha(vendor_raw), pins=target + "/geometry/CARD_WORKER_PINS.json", vendor=target + "/geometry/vendor",
        asset_root=target + "/assets", sprite=target + "/assets/all-v1.png", files=component_files,
        authority=dict(canonical=False, complete_deal=False, human_verified=False)))
    caller_files = [save(caller, "CURRENT_RECOGNIZER.proposed.json", pointer),
        save(caller, "ops/diana20-recognizer-component.json", component),
        save(caller, "universal_video/diana20_prepared_frames.py", b"# SYNTHETIC_NOT_EXECUTED\n"),
        save(caller, "universal_video/diana20_recognizer.py",
             f"COMPONENT_SHA256 = {sha(component)!r}\nNATIVE_SCALE_PINS = {pins!r}\n".encode())]
    proposal = encoded(dict(schema="diana20-scale-caller-proposal/v1", applied=False,
        owner_lifecycle_dispatch_changed=False, target=target, bundle_manifest_sha256=sha(raw), files=caller_files))
    save(caller, "CALLER_PROPOSAL.json", proposal)
    profile = encoded(dict(profile_version=profile_module.PROFILE_VERSION, bundle_manifest_sha256=sha(raw),
                           caller_proposal_sha256=sha(proposal), runtime_root=target, source_sha256=source))
    profile_path = tmp_path / "profile.json"
    profile_path.write_bytes(profile)
    options = dict(expected_sha256=sha(profile), bundle_root=bundle, caller_root=caller, asset_root=assets)
    verified = profile_module.load_scale_pbn_profile(profile_path, **options)
    return dict(path=profile_path, options=options, verified=verified, pins=pins)


def scale_row(kit, variant=5, status="SELECTED"):
    row = row_fixture()
    payload, update = row["payload"], row["payload"]["recognizer_update"]
    scale = dict(adapter_version="diana20-pixel-native-scale-v1", template_transform="NONE_NATIVE_ONLY",
        selection_status=status, status="MEASURED", width_state="measured", height_state="measured",
        selected_variant=variant, selected_sprite_sha256=kit["pins"][variant][0],
        native_size=list(kit["pins"][variant][1:]), width_px=kit["pins"][variant][1], height_px=kit["pins"][variant][2], matcher_attempts=1)
    runtime = json.loads(kit["verified"].runtime_json)
    runtime.update(vendor_modules=json.loads(kit["verified"].vendor_json), margin_applied=variant == 5,
                   sprite_sha256=scale["selected_sprite_sha256"], card_scale=scale)
    if status in ("UNKNOWN", "TIE"):
        scale.update(selected_variant=None, selected_sprite_sha256=None, native_size=None, matcher_attempts=0,
                     width_state="unknown", height_state="unknown", status=status, width_px=None, height_px=None)
        runtime.update(margin_applied=False, sprite_sha256=None)
        payload.pop("recognizer_version")
        payload.update(status="SCALE_" + status, cards=[])
        update.update(effective_cards=[], original_card_count=0)
    payload.update(card_scale=scale, sprite_sha256=runtime["sprite_sha256"])
    update["runtime"] = runtime
    rebind(row)
    return row


def rebind(row):
    payload = row["payload"]
    payload["recognizer_update"]["original_payload_sha256"] = sha(encoded({k:v for k,v in payload.items() if k != "recognizer_update"}))


@pytest.mark.parametrize("variant", range(1,9))
def test_all_eight_scales_use_explicit_profile_preserve_original(kit, variant):
    row = scale_row(kit, variant)
    before = encoded(row)
    record, claims = adapt_diana20_row(row, scale_profile=kit["verified"])
    assert record["deal"] is None and encoded(row) == before
    assert record["diana20_row_sha256"] == sha(before)
    assert claims["effective_unknown"] == row["payload"]["unknown"]
    assert claims["scale_source_profile"]["runtime_status"] == "HOLD"
    assert "installed_release_claim" not in claims
    with pytest.raises(ValueError, match="RUNTIME_PIN_version"):
        adapt_diana20_row(row)


@pytest.mark.parametrize("status", ["UNKNOWN", "TIE"])
def test_empty_scale_abstention_remains_unknown(kit, status):
    row = scale_row(kit, status=status)
    record, claims = adapt_diana20_row(row, scale_profile=kit["verified"])
    assert record["deal"] is None and claims["effective_cards"] == []
    row["payload"]["status"] = "FUTURE_UNKNOWN_STATUS"
    with pytest.raises(ValueError, match="BASE_VERSION"):
        adapt_diana20_row(row, scale_profile=kit["verified"])


@pytest.mark.parametrize("key,value", [("version", "diana20-pixel-scale-source-v1-tie-candidate"),
    ("worker_sha256", "f"*64), ("worker_path", "/other/worker.py"), ("layer_version", "wrong"),
    ("candidate_version", "wrong"), ("geometry_enabled", True), ("factor_identity_fix_enabled", True),
    ("margin_enabled", 1), ("scale_selection_version", "future"), ("sprite_sha256", "f"*64),
    ("helpers", {}), ("vendor_modules", {})])
def test_wrong_runtime_identity_rejected_without_fallback(kit, key, value):
    row = scale_row(kit)
    row["payload"]["recognizer_update"]["runtime"][key] = value
    with pytest.raises(ValueError):
        adapt_diana20_row(row, scale_profile=kit["verified"])


@pytest.mark.parametrize("field,value", [("width_state", "inferred"), ("height_px", 120),
    ("selected_sprite_sha256", "f"*64), ("selected_variant", True), ("matcher_attempts", 2),
    ("native_size", [109, 180]), ("template_transform", "RESIZED")])
def test_forged_scale_rejected(kit, field, value):
    row = scale_row(kit)
    row["payload"]["card_scale"][field] = value
    rebind(row)
    with pytest.raises(ValueError):
        adapt_diana20_row(row, scale_profile=kit["verified"])


@pytest.mark.parametrize("index", range(40))
def test_every_snapshot_file_rehashed_at_use(kit, index):
    profile = kit["verified"]
    assert len(profile.checks) == 40
    path = Path(profile.checks[index][0])
    path.write_bytes(path.read_bytes() + b"TAMPER")
    with pytest.raises(ValueError, match="FILE_HASH"):
        adapt_diana20_row(scale_row(kit), scale_profile=profile)


def test_profile_hash_version_and_arbitrary_object_rejected(kit):
    with pytest.raises(ValueError, match="FILE_HASH"):
        profile_module.load_scale_pbn_profile(kit["path"], **dict(kit["options"], expected_sha256="f"*64))
    profile = json.loads(kit["path"].read_bytes())
    profile["profile_version"] = "future"
    kit["path"].write_bytes(encoded(profile))
    with pytest.raises(ValueError, match="PROFILE_VERSION"):
        profile_module.load_scale_pbn_profile(kit["path"], **dict(kit["options"], expected_sha256=sha(encoded(profile))))
    with pytest.raises(ValueError, match="VERIFIED_SCALE_PROFILE_REQUIRED"):
        adapt_diana20_row(row_fixture(), scale_profile={})
    with pytest.raises(ValueError, match="VERIFIED_PROFILE_REQUIRED"):
        profile_module.VerifiedScalePBNProfile()


def test_non_v5_addition_and_provenance_replay_rejected(kit):
    for mutate in (
        lambda r: r["receipt"].update(frame_sha256="b"*64),
        lambda r: r["receipt"].update(timestamp_ms=2000),
        lambda r: r["receipt"].update(source_sha256="b"*64),
        lambda r: r["payload"]["recognizer_update"].update(original_payload_sha256="f"*64),
        lambda r: r["payload"]["recognizer_update"].update(effective_unknown=[]),
        lambda r: r["payload"]["recognizer_update"].update(additional_machine_candidate_count=1),
        lambda r: r["payload"]["recognizer_update"].update(human_verified=True),
    ):
        row = scale_row(kit, 3)
        mutate(row)
        with pytest.raises(ValueError):
            adapt_diana20_row(row, scale_profile=kit["verified"])


def test_export_reuses_legacy_writer_and_binds_exact_original_jsonl(kit, tmp_path):
    row = scale_row(kit)
    raw = encoded(row) + b"\n"
    source = tmp_path / "rows.jsonl"
    source.write_bytes(raw)
    output = tmp_path / "output"
    output.mkdir()
    result = export_diana20_jsonl(source, output, expected_sha256=sha(raw), scale_profile=kit["verified"])
    assert (output / result["jsonl_copy"]).read_bytes() == raw
    assert result["native_deals_asserted"] == 0 and result["canonical_promotion_allowed"] is False
    assert result["scale_source_profile"]["runtime_qualified"] is False
    pbn = (output / result["pbn_outputs"][0]["path"]).read_bytes()
    assert b'[Deal "?"]' in pbn and b'SYNTHETIC_TIE_5H_6H' in pbn
    assert source.read_bytes() == raw
    with pytest.raises(ValueError, match="SCALE_QUALIFIED_RECOVERY_UNSUPPORTED"):
        export_diana20_jsonl(source, output, expected_sha256=sha(raw), scale_profile=kit["verified"],
                            qualified_reconstruction_by_row_sha256={sha(encoded(row)): {}})


def test_legacy_profile_and_admission_unchanged():
    assert installed_runtime_pins()["version"] == "diana20-installed-margin-worker-r1"
    assert installed_runtime_pins()["worker_sha256"] == "d907fc1fbbe2b1ee3f061b81b4fd60180d58ee14b1478c94ea53bfb9a674e56a"
    record, claims = adapt_diana20_row(row_fixture())
    assert record["deal"] is None and "scale_source_profile" not in claims


@pytest.mark.parametrize("selection,outer", [("UNKNOWN", "SCALE_TIE"), ("TIE", "SCALE_UNKNOWN"),
                                           ("SELECTED", "SCALE_UNKNOWN")])
def test_empty_scale_status_must_match_selection(kit, selection, outer):
    row = scale_row(kit, status=selection)
    row["payload"]["status"] = outer
    rebind(row)
    with pytest.raises(ValueError, match="EMPTY_SCALE_STATUS"):
        adapt_diana20_row(row, scale_profile=kit["verified"])


def reseal(kit):
    """Deliberately trust a modified synthetic manifest to test inner bindings."""
    bundle, caller = kit["options"]["bundle_root"], kit["options"]["caller_root"]
    manifest = json.loads((bundle / "SOURCE_BUNDLE.json").read_bytes())
    for item in manifest["files"]:
        raw = (bundle / item["path"]).read_bytes()
        item.update(bytes=len(raw), sha256=sha(raw))
    raw = encoded(manifest)
    (bundle / "SOURCE_BUNDLE.json").write_bytes(raw)
    (bundle / "CODE_BUNDLE.json").write_bytes(raw)
    proposal = json.loads((caller / "CALLER_PROPOSAL.json").read_bytes())
    proposal["bundle_manifest_sha256"] = sha(raw)
    for item in proposal["files"]:
        value = (caller / item["path"]).read_bytes()
        item.update(bytes=len(value), sha256=sha(value))
    proposal_raw = encoded(proposal)
    (caller / "CALLER_PROPOSAL.json").write_bytes(proposal_raw)
    profile = json.loads(kit["path"].read_bytes())
    profile.update(bundle_manifest_sha256=sha(raw), caller_proposal_sha256=sha(proposal_raw))
    kit["path"].write_bytes(encoded(profile))
    return dict(kit["options"], expected_sha256=sha(encoded(profile)))


@pytest.mark.parametrize("target,field,value,reason", [
    ("bundle", "version", "diana20-pixel-scale-source-v1-tie-candidate", "BUNDLE_VERSION"),
    ("bundle", "geometry_enabled", True, "BUNDLE_AUTHORITY"),
    ("bundle", "worker_sha256", "f"*64, "WORKER_VERSION_HASH"),
    ("bundle", "vendor_pins_sha256", "f"*64, "VENDOR_MANIFEST"),
    ("pointer", "version", "other", "POINTER_BINDING"),
    ("pointer", "manifest_sha256", "f"*64, "POINTER_BINDING"),
    ("pointer", "code_root", "/other", "POINTER_BINDING"),
    ("component", "pins_sha256", "f"*64, "CALLER_COMPONENT_HASH"),
])
def test_internally_mixed_authenticated_manifest_is_rejected(kit, target, field, value, reason):
    paths = {"bundle": kit["options"]["bundle_root"] / "SOURCE_BUNDLE.json",
             "pointer": kit["options"]["caller_root"] / "CURRENT_RECOGNIZER.proposed.json",
             "component": kit["options"]["caller_root"] / "ops/diana20-recognizer-component.json"}
    path = paths[target]
    data = json.loads(path.read_bytes())
    data[field] = value
    path.write_bytes(encoded(data))
    with pytest.raises(ValueError, match=reason):
        profile_module.load_scale_pbn_profile(kit["path"], **reseal(kit))


@pytest.mark.parametrize("bad_path", ["../escape.py", "/absolute.py", "geometry/../escape.py", "C:/drive.py", "geometry\\x.py"])
def test_manifest_path_escape_rejected(kit, bad_path):
    path = kit["options"]["bundle_root"] / "SOURCE_BUNDLE.json"
    manifest = json.loads(path.read_bytes())
    manifest["files"][0]["path"] = bad_path
    raw = encoded(manifest)
    path.write_bytes(raw)
    profile = json.loads(kit["path"].read_bytes())
    profile["bundle_manifest_sha256"] = sha(raw)
    kit["path"].write_bytes(encoded(profile))
    with pytest.raises(ValueError, match="FILE_PATH"):
        profile_module.load_scale_pbn_profile(kit["path"], **dict(kit["options"], expected_sha256=sha(encoded(profile))))


def test_omitted_vendor_module_and_foreign_hash_rejected(kit):
    for wrong_hash in (False, True):
        row = scale_row(kit)
        modules = row["payload"]["recognizer_update"]["runtime"]["vendor_modules"]
        name = next(iter(modules))
        if wrong_hash:
            modules[name]["sha256"] = "f"*64
        else:
            del modules[name]
        with pytest.raises(ValueError, match="VENDOR"):
            adapt_diana20_row(row, scale_profile=kit["verified"])
