"""Native worker integration; saved-source tests are explicit private fixtures.

Synthetic frame labels are construction oracles. Real-frame comparison checks
regression equivalence, never accuracy or card identity self-agreement.
"""
import hashlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace, ModuleType

import cv2
import numpy as np
import pytest

from tools.diana20_gambler_scale import NativeScaleSelector, load_native_sprite, select_native_scale
from tools.diana20_scale_contract import validate_native_scale_response
from bridge_vision.gambler_classic_reference import VARIANT_CARD_SIZE, decode_card_cells, validate_sprite_bytes
from bridge_vision.gambler_reference_authority import pinned_sprite_sha256
from scripts.build_diana20_scale_bundle import build, prepare_caller


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def frame(width=109, height=147, second_size=None):
    image = np.full((1010, 1920, 3), (40, 100, 40), np.uint8)
    for x, (w, h) in zip((400, 800), ((width, height), second_size or (width, height))):
        cell = np.zeros((h, w, 3), np.uint8)
        cell[1:-1, 1:-1] = 255
        cell[5:max(8, h // 7), 5:max(7, w // 9)] = 0
        image[25:25 + h, x:x + w] = cell
    return image


def inventory():
    return [SimpleNamespace(variant=v, card_width=w, card_height=h, sprite_sha256=pinned_sprite_sha256(v))
            for v, (w, h) in VARIANT_CARD_SIZE.items()]


@pytest.mark.parametrize("variant,size", VARIANT_CARD_SIZE.items())
def test_native_selector_uses_measured_geometry(variant, size):
    result = select_native_scale(frame(*size), inventory())
    assert result.sprite.variant == variant
    assert result.evidence["width_state"] == result.evidence["height_state"] == "measured"
    assert result.evidence["matcher_attempts"] == 0


def test_missing_nonnative_mixed_and_blank_do_not_select():
    v5 = [inventory()[4]]
    for image, sprites, status in [(frame(71, 96), v5, "UNKNOWN"),
                                  (frame(133, 200), inventory(), "UNKNOWN"),
                                  (frame(second_size=(71, 96)), inventory(), "TIE"),
                                  (np.zeros((1010, 1920, 3), np.uint8), inventory(), "UNKNOWN")]:
        result = select_native_scale(image, sprites)
        assert result.sprite is None
        assert result.evidence["selection_status"] == status
        assert result.evidence["matcher_attempts"] == 0


def test_empty_asset_root_abstains_instead_of_using_legacy_sprite(tmp_path):
    selector = NativeScaleSelector(sprite_path=tmp_path / "absent.png", asset_root=tmp_path)
    assert selector.select(frame()).sprite is None


@pytest.mark.parametrize('kind', ['empty', 'oversized', 'untrusted'])
def test_native_binary_loader_rejects_invalid_assets(tmp_path, kind):
    path = tmp_path / 'all.png'
    if kind == 'oversized':
        with path.open('wb') as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
    else:
        path.write_bytes(b'' if kind == 'empty' else b'not an allowlisted sprite')
    with pytest.raises(ValueError):
        load_native_sprite(path)


def test_bundle_refuses_unpinned_source_without_creating_output(tmp_path):
    source = tmp_path / "base"
    source.mkdir()
    (source / "SOURCE_BUNDLE.json").write_text('{}')
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="pin mismatch"):
        build("tie-candidate", source, output)
    assert not output.exists()


def response_fixture(selected=False, runtime=None):
    selection = select_native_scale(frame() if selected else np.zeros((1010, 1920, 3), np.uint8), inventory())
    scale = selection.evidence
    digest = selection.sprite.sprite_sha256 if selection.sprite else None
    runtime = dict(runtime or {}, card_scale=copy.deepcopy(scale), sprite_sha256=digest,
                   margin_applied=selected, geometry_enabled=False)
    return dict(cards=[], unknown=[], canonical_allowed=False, complete_deal=False,
                card_scale=scale, sprite_sha256=digest,
                recognizer_update=dict(effective_cards=[], effective_unknown=[], additional_machine_candidate_count=0,
                    human_verified=False, canonical_allowed=False, complete_deal=False, runtime=runtime))


@pytest.mark.parametrize('case', ['null_selected', 'missing_variant', 'wrong_size', 'inferred_width',
                                  'bool_variant', 'nan_width', 'two_attempts', 'unknown_effective',
                                  'unknown_additions', 'runtime_scale_drift', 'bool_count', 'negative_count',
                                  'zero_attempt_cards', 'nonv5_addition'])
def test_untrusted_scale_responses_fail_closed(case):
    pins = {v: (pinned_sprite_sha256(v), w, h) for v, (w, h) in VARIANT_CARD_SIZE.items()}
    result = response_fixture(selected=not case.startswith('unknown'))
    assert validate_native_scale_response(result, pins)
    scale, update = result['card_scale'], result['recognizer_update']
    if case == 'null_selected':
        scale['selected_sprite_sha256'] = result['sprite_sha256'] = update['runtime']['sprite_sha256'] = None
    elif case == 'missing_variant':
        scale.pop('selected_variant')
    elif case == 'wrong_size':
        scale['native_size'] = [71, 96]
    elif case == 'inferred_width':
        scale['width_state'] = 'inferred'
    elif case == 'bool_variant':
        scale['selected_variant'] = True
    elif case == 'nan_width':
        scale['width_px'] = float('nan')
    elif case == 'two_attempts':
        scale['matcher_attempts'] = 2
    elif case == 'unknown_effective':
        update['effective_cards'] = [{'card': 'AS'}]
    elif case == 'unknown_additions':
        update['additional_machine_candidate_count'] = 1
    elif case == 'runtime_scale_drift':
        update['runtime']['card_scale']['selected_variant'] = 4
    elif case == 'bool_count':
        update['additional_machine_candidate_count'] = False
    elif case == 'negative_count':
        update['additional_machine_candidate_count'] = -1
    elif case == 'zero_attempt_cards':
        result['cards'] = update['effective_cards'] = [{'card': 'AS'}]
    elif case == 'nonv5_addition':
        scale.update(selected_variant=4, native_size=[89, 120], width_px=89, height_px=120,
                     selected_sprite_sha256=pinned_sprite_sha256(4), matcher_attempts=1)
        result['sprite_sha256'] = update['runtime']['sprite_sha256'] = pinned_sprite_sha256(4)
        update['runtime']['margin_applied'] = False
        update['additional_machine_candidate_count'] = 1
        result['unknown'] = [{'reason': 'CARD_UNCERTAIN'}]
        update['effective_cards'] = [{'card': 'AS'}]
    if case != 'runtime_scale_drift':
        update['runtime']['card_scale'] = copy.deepcopy(scale)
    assert not validate_native_scale_response(result, pins)


@pytest.fixture(scope="module")
def source_root():
    raw = os.environ.get("DIANA20_SAVED_SOURCE_ROOT")
    if not raw:
        pytest.skip("saved pinned Diana20 sources not supplied")
    return Path(raw)


@pytest.fixture(scope="module")
def assets():
    raw = os.environ.get("GAMBLER_CLASSIC_FIXTURE_ROOT")
    if not raw:
        pytest.skip("original pinned Gambler sprites not supplied")
    return Path(raw)


@pytest.fixture(scope="module", params=["installed-margin", "tie-candidate"])
def bundle(request, source_root, tmp_path_factory):
    base = request.param
    output = tmp_path_factory.mktemp(base) / "bundle"
    if base == "installed-margin":
        original = source_root / "diana20-recognizer-release-20261020-r1/staged"
        vendor = source_root / "diana20-geometry-worker/pinned_cards"
        pins = source_root / "diana20-geometry-worker/CARD_WORKER_PINS.json"
        report = build(base, original, output, vendor_root=vendor, vendor_pins=pins)
    else:
        original = source_root / "recognizer-tie-fix-20261022-r1/release-source-bundle/candidate-r3"
        vendor = original / "geometry/vendor"
        pins = original / "geometry/CARD_WORKER_PINS.json"
        report = build(base, original, output)
    return SimpleNamespace(base=base, output=output, report=report, original=original,
                           original_vendor=vendor, original_pins=pins)


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def client(bundle, assets, scratch):
    ipc = module(bundle.output / "runtime/diana20_visual_ipc.py", "scale_ipc")
    instance = ipc.GeometryClient(sys.executable, bundle.output / "geometry/diana20_geometry_worker.py",
        bundle.output / "geometry/vendor", bundle.output / "geometry/CARD_WORKER_PINS.json",
        bundle.report["vendor_pins_sha256"], scratch, assets / "5/all.png", asset_root=assets)
    return ipc, instance


def shot(path, raw, source_sha, pts):
    path.write_bytes(raw)
    return dict(evidence_id="synthetic-or-saved-" + str(pts), path=str(path), sha256=sha(raw),
                source_sha256=source_sha, time=pts / 1000,
                decode_receipt=dict(actual_pts_ms=pts, timestamp_basis="DECODER_ACTUAL_PTS",
                    source_sha256=source_sha, frame_sha256=sha(raw), width=1920, height=1010))


def encoded(image):
    ok, raw = cv2.imencode('.png', image)
    assert ok
    return raw.tobytes()


def native_frame(assets, variant, cards=("AC", "AH", "AS")):
    # Fixture construction independently reads authenticated PNG bytes. The
    # frozen core decoder returns its native depth; video frames are 8-bit.
    sprite = validate_sprite_bytes((assets / str(variant) / "all.png").read_bytes(),
                                   expected_sha256=pinned_sprite_sha256(variant), expected_variant=variant)
    cells = decode_card_cells(sprite)
    image = np.full((1010, 1920, 3), (40, 100, 40), np.uint8)
    for x, card in zip((400, 650, 900), cards):
        cell = cells[card]
        if cell.dtype == np.uint16:
            cell = np.rint(cell.astype(np.float64) * (255 / 65535)).astype(np.uint8)
        h, w = cell.shape[:2]
        target = image[25:25 + h, x:x + w]
        if cell.shape[2] == 4:
            alpha = cell[:, :, 3:4].astype(float) / 255
            target[:] = np.rint(cell[:, :, :3] * alpha + target * (1 - alpha)).astype(np.uint8)
        else:
            target[:] = cell
    return image


@pytest.mark.parametrize("variant", range(1, 9))
def test_two_complex_native_faces_can_safely_abstain(assets, variant):
    selector = NativeScaleSelector(sprite_path=None, asset_root=assets)
    # These independently reviewed pairs do not expose two qualifying faces.
    # This records a coverage limitation; no thresholds are fitted to labels.
    for cards in (("AC", "KD"), ("2S", "JD")):
        result = selector.select(native_frame(assets, variant, cards))
        assert result.sprite is None
        assert result.evidence['selection_status'] == 'UNKNOWN'
        assert result.evidence['matcher_attempts'] == 0


def test_actual_worker_ipc_eight_scales_change_and_abstention(bundle, assets, tmp_path):
    ipc, observer = client(bundle, assets, tmp_path)
    with observer:
        # One process changes scales, revisits v5, then sees unknown/tied frames.
        for index, variant in enumerate([5, 1, 2, 3, 4, 6, 7, 8, 5]):
            item = shot(tmp_path / f"native-{variant}-{index}.png", encoded(native_frame(assets, variant)),
                        ipc.SOURCE_SHA, index * 1000)
            result = observer.observe(item, tmp_path)
            scale = result["card_scale"]
            assert scale["selection_status"] == "SELECTED", scale
            assert scale["selected_variant"] == variant
            assert scale["matcher_attempts"] == 1
            assert result["sprite_sha256"] == pinned_sprite_sha256(variant)
            assert result['recognizer_input_binding']['roi_receipts_validated_against_initial_decoded_frame'] > 0
            assert result['raw_card_candidates'] or any(u.get('kind') == 'HAND_CORNER' for u in result['unknown'])
            assert all(row['card'] in {"AC", "AH", "AS"} for row in result['cards'])
            update = result['recognizer_update']
            assert update['runtime']['margin_applied'] is (variant == 5)
            assert update['runtime']['geometry_enabled'] is False
            assert update['human_verified'] is result['canonical_allowed'] is result['complete_deal'] is False
            if variant != 5:
                assert update['additional_machine_candidate_count'] == 0
                assert update['effective_unknown'] == result['unknown']
        for index, image, status in [(20, np.zeros((1010, 1920, 3), np.uint8), 'UNKNOWN'),
                                    (21, frame(second_size=(71, 96)), 'TIE'),
                                    (22, frame(133, 200), 'UNKNOWN')]:
            result = observer.observe(shot(tmp_path / f"refusal-{index}.png", encoded(image),
                                           ipc.SOURCE_SHA, index * 1000), tmp_path)
            assert result['card_scale']['selection_status'] == status
            assert result['card_scale']['matcher_attempts'] == 0
            assert result['cards'] == result['recognizer_update']['effective_cards'] == []
            assert result['unknown']


def test_old_ipc_rejects_new_pins_before_spawn(bundle, assets, tmp_path):
    old = module(bundle.original / "runtime/diana20_visual_ipc.py", "old_scale_ipc")
    with pytest.raises(old.IPCError, match="pins"):
        old.GeometryClient(sys.executable, bundle.output / "geometry/diana20_geometry_worker.py",
            bundle.output / "geometry/vendor", bundle.output / "geometry/CARD_WORKER_PINS.json",
            bundle.report['vendor_pins_sha256'], tmp_path, assets / "5/all.png")


def test_bundle_preserves_frozen_reference_and_limits_payload_delta(bundle):
    reference = 'bridge_vision/gambler_classic_reference.py'
    assert (bundle.output / 'geometry/vendor' / reference).read_bytes() == (bundle.original_vendor / reference).read_bytes()
    assert set(bundle.report['changed_files']) == {
        'geometry/CARD_WORKER_PINS.json', 'geometry/diana20_geometry_worker.py',
        'geometry/vendor/bridge_vision/bridgit_diana20_layout.py',
        'geometry/vendor/bridge_vision/gambler_card_scale.py',
        'geometry/vendor/bridge_vision/diana20_gambler_scale.py',
        'runtime/diana20_visual_ipc.py',
    }


def test_changed_worker_code_is_rejected_before_spawn(bundle, assets, tmp_path):
    ipc = module(bundle.output / "runtime/diana20_visual_ipc.py", "tamper_ipc")
    bad = tmp_path / "worker.py"
    bad.write_bytes((bundle.output / "geometry/diana20_geometry_worker.py").read_bytes() + b'\n# changed\n')
    with pytest.raises(ipc.IPCError, match="SHA"):
        ipc.GeometryClient(sys.executable, bad, bundle.output / "geometry/vendor",
            bundle.output / "geometry/CARD_WORKER_PINS.json", bundle.report['vendor_pins_sha256'],
            tmp_path, assets / "5/all.png")


def test_prepared_caller_seals_asset_command_and_response_contract(bundle, assets, source_root, tmp_path):
    output = tmp_path / 'prepared'
    receipt = prepare_caller(source_root / 'diana20-prepared-frame-adapter-20261021-r1/candidate',
                             bundle.output, assets, output)
    adapter = module(output / 'universal_video/diana20_recognizer.py', 'prepared_scale_adapter')
    contract = adapter.component()
    assert receipt['applied'] is False
    assert receipt['owner_lifecycle_dispatch_changed'] is False
    assert receipt['asset_variants'] == list(range(1, 9))
    binding = adapter.Binding(tmp_path / 'binding', '0' * 64, tmp_path, tmp_path / 'scratch', contract)
    command = adapter.worker_command(binding)
    assert command[-2:] == ['--asset-root', contract['asset_root']]
    assert 'worker' in command
    assert contract['runtime']['geometry_enabled'] is False
    assert contract['runtime']['factor_identity_fix_enabled'] is (bundle.base == 'tie-candidate')
    assert contract['dispatch_enabled'] is False and contract['binding'] is None
    original = module(source_root / 'diana20-prepared-frame-adapter-20261021-r1/candidate/universal_video/diana20_recognizer.py',
                      'original_prepared_scale_adapter')
    # A pure ABI oracle: no impersonation of the Linux owner or actual preflight.
    req = dict(id='contract-fixture', source_sha256=contract['source_sha256'], frame_sha256='0' * 64, timestamp_ms=0)
    result = response_fixture(runtime=contract['runtime'])
    result.update(source_video_sha256=req['source_sha256'], frame_sha256=req['frame_sha256'], source_timestamp_ms=0)
    value = dict(id=req['id'], status='OK', result=result)
    assert adapter.validate_response(value, req, contract) is value['result']
    with pytest.raises(original.RecognizerError, match='RESPONSE_RUNTIME'):
        original.validate_response(value, req, original.component())
    wrong = copy.deepcopy(value)
    wrong['result']['cards'] = [{'card': 'AC'}]
    with pytest.raises(adapter.RecognizerError, match='RESPONSE_SCALE'):
        adapter.validate_response(wrong, req, contract)
    wrong = copy.deepcopy(value)
    wrong['result']['recognizer_update']['human_verified'] = True
    with pytest.raises(adapter.RecognizerError, match='RESPONSE_AUTHORITY'):
        adapter.validate_response(wrong, req, contract)
    wrong = copy.deepcopy(value)
    wrong['result']['recognizer_update']['effective_cards'] = [{'card': 'AS'}]
    with pytest.raises(adapter.RecognizerError, match='RESPONSE_SCALE'):
        adapter.validate_response(wrong, req, contract)
    package = ModuleType('scale_prepared_test')
    package.__path__ = [str(output / 'universal_video')]
    sys.modules[package.__name__] = package
    sys.modules[package.__name__ + '.diana20_recognizer'] = adapter
    prepared = module(output / 'universal_video/diana20_prepared_frames.py', package.__name__ + '.diana20_prepared_frames')
    argv = prepared.command('/caller-owned/scratch')
    assert len(argv) == 17 and argv[-2:] == ['--asset-root', contract['asset_root']]
    prepared.validate_abi(prepared.expected_abi('/caller-owned/scratch'), '/caller-owned/scratch')


def test_saved_frames_preserve_v5_cards_unknowns_and_margin(bundle, assets, tmp_path):
    manifest_path = os.environ.get("DIANA20_SCALE_FRAME_MANIFEST")
    if not manifest_path:
        pytest.skip("saved real frame comparison manifest not supplied")
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_bytes())
    ipc, new = client(bundle, assets, tmp_path)
    old = module(bundle.original / "runtime/diana20_visual_ipc.py", "baseline_ipc")
    baseline = old.GeometryClient(sys.executable, bundle.original / "geometry/diana20_geometry_worker.py",
        bundle.original_vendor, bundle.original_pins, sha(bundle.original_pins.read_bytes()),
        tmp_path, assets / "5/all.png")
    with new, baseline:
        for index, row in enumerate(manifest['frames']):
            raw = (manifest_path.parent / row['path']).read_bytes()
            assert sha(raw) == row['sha256']
            item = shot(tmp_path / f"saved-{index}.png", raw, ipc.SOURCE_SHA, index * 1000)
            before = baseline.observe(item, tmp_path)
            after = new.observe(item, tmp_path)
            assert after['card_scale']['selected_variant'] == 5
            for key in ('cards', 'unknown', 'raw_card_candidates', 'handrow_receipts', 'ui', 'seat_mapping'):
                assert after.get(key) == before.get(key), key
            for key in ('effective_cards', 'effective_unknown', 'decisions'):
                assert after['recognizer_update'][key] == before['recognizer_update'][key], key


@pytest.mark.parametrize("status", ["UNKNOWN", "TIE"])
@pytest.mark.parametrize("change", ["drop", "reason", "order", "append"])
def test_abstention_unknown_history_is_immutable(status, change):
    result = response_fixture()
    scale = result["card_scale"]
    scale.update(selection_status=status, status=status)
    result["recognizer_update"]["runtime"]["card_scale"] = copy.deepcopy(scale)
    result["unknown"] = [
        {"kind": "CARD_SCALE", "reason": "SYNTHETIC_" + status, "card": None, "seat": None},
        {"kind": "CARD_SCALE", "reason": "SYNTHETIC_BOUNDARY_DIAGNOSTIC", "card": None, "seat": None},
    ]
    update = result["recognizer_update"]
    update["effective_unknown"] = copy.deepcopy(result["unknown"])
    pins = {v: (pinned_sprite_sha256(v), w, h) for v, (w, h) in VARIANT_CARD_SIZE.items()}
    assert validate_native_scale_response(result, pins)
    if change == "drop":
        update["effective_unknown"] = []
    elif change == "reason":
        update["effective_unknown"][0]["reason"] = "REPLACED"
    elif change == "order":
        update["effective_unknown"].reverse()
    else:
        update["effective_unknown"].append({"reason": "INVENTED"})
    assert not validate_native_scale_response(result, pins)


@pytest.fixture
def synthetic_caller_inputs(tmp_path, monkeypatch):
    """Minimal pinned byte fixtures; generated callers are compiled, never run."""
    from scripts import build_diana20_scale_bundle as builder
    from tools import diana20_gambler_scale

    caller, bundle = tmp_path / "caller", tmp_path / "bundle"
    source = (
        "COMPONENT_SHA256 = 'SYNTHETIC'\n"
        "class RecognizerError(RuntimeError):\n    pass\n"
        "def command(c):\n    return ['worker',\n"
        '            "--sprite", c["sprite"]]\n'
        "def validate_response(runtime, contract):\n    if (False\n"
        '            or runtime.get("factor_identity_fix_enabled") is not False):\n        pass\n'
        "    # Keep baseline cards, UNKNOWN history and every observation field intact.\n"
    ).encode()
    prepared = (
        "def command(c):\n    return ['worker',\n"
        '            "--sprite", c["sprite"]]\n'
        'def validate_abi(abi):\n    return len(abi["argv"]) == 15\n'
    ).encode()
    contract = dict(entry="/synthetic/ACTIVE_RECOGNIZER.py", runtime=dict(geometry_enabled=False),
                    files=[dict(path="/synthetic/ACTIVE_RECOGNIZER.py", sha256="e" * 64)])
    for name, raw, pin_name in (
        ("universal_video/diana20_recognizer.py", source, "CALLER_SHA"),
        ("universal_video/diana20_prepared_frames.py", prepared, "PREPARED_SHA"),
        ("ops/diana20-recognizer-component.json", json.dumps(contract).encode(), "COMPONENT_SHA"),
    ):
        path = caller / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        monkeypatch.setattr(builder, pin_name, sha(raw))
    entries = []
    for name in ("geometry/diana20_geometry_worker.py", "geometry/CARD_WORKER_PINS.json"):
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = b"# SYNTHETIC_NOT_EXECUTED\n" if name.endswith(".py") else b"{}\n"
        path.write_bytes(raw)
        entries.append(dict(path=name, sha256=sha(raw), bytes=len(raw)))
    monkeypatch.setattr(diana20_gambler_scale, "NativeScaleSelector",
                        lambda **kwargs: SimpleNamespace(sprites=inventory()))
    manifest = dict(schema="diana20-pixel-scale-source-bundle/v1",
                    base="installed-margin", version=builder.VERSION + "-installed-margin",
                    factor_identity_fix_enabled=False, files=entries)
    return SimpleNamespace(caller=caller, bundle=bundle, assets=tmp_path / "synthetic-assets",
                           output=tmp_path / "proposal", manifest=manifest)


def write_caller_manifest(inputs):
    (inputs.bundle / "CODE_BUNDLE.json").write_text(json.dumps(inputs.manifest), encoding="utf-8")


@pytest.mark.parametrize("base", ["installed-margin", "tie-candidate"])
def test_prepare_caller_supported_versions_stay_under_code_bundles(synthetic_caller_inputs, base):
    from scripts import build_diana20_scale_bundle as builder
    inputs = synthetic_caller_inputs
    inputs.manifest.update(base=base, version=builder.VERSION + "-" + base,
                           factor_identity_fix_enabled=base == "tie-candidate")
    write_caller_manifest(inputs)
    receipt = prepare_caller(inputs.caller, inputs.bundle, inputs.assets, inputs.output)
    target = "/opt/bridge-school/universal-video/code-bundles/" + inputs.manifest["version"]
    pointer = json.loads((inputs.output / "CURRENT_RECOGNIZER.proposed.json").read_bytes())
    component = json.loads((inputs.output / "ops/diana20-recognizer-component.json").read_bytes())
    assert receipt["target"] == pointer["code_root"] == target
    for key in ("vendor", "pins", "asset_root", "sprite"):
        assert component[key].startswith(target + "/")
    assert component["runtime"]["worker_path"].startswith(target + "/")
    assert receipt["applied"] is False and receipt["owner_lifecycle_dispatch_changed"] is False


@pytest.mark.parametrize("version", [
    "/tmp/release", "../release", "../../release", "nested/release", "nested\\release",
    "C:/release", "", ".", "..", None, True, [], {}, "unrecognized-release",
    "diana20-pixel-scale-source-v1-tie-candidate",
])
def test_prepare_caller_rejects_unsafe_or_mismatched_version(synthetic_caller_inputs, version):
    inputs = synthetic_caller_inputs
    inputs.manifest["version"] = version
    write_caller_manifest(inputs)
    with pytest.raises(ValueError, match="unsupported scale bundle version"):
        prepare_caller(inputs.caller, inputs.bundle, inputs.assets, inputs.output)
    assert not inputs.output.exists()


@pytest.mark.parametrize("base", [None, "", "unknown-base", True, [], {}])
def test_prepare_caller_rejects_unsupported_base(synthetic_caller_inputs, base):
    inputs = synthetic_caller_inputs
    inputs.manifest["base"] = base
    write_caller_manifest(inputs)
    with pytest.raises(ValueError, match="unsupported scale bundle version"):
        prepare_caller(inputs.caller, inputs.bundle, inputs.assets, inputs.output)
    assert not inputs.output.exists()
