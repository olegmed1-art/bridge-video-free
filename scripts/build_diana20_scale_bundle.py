"""Build a local, create-only scale overlay for two exact Diana20 source pins.

No installer, ACTIVE entry, lifecycle, PBN, images, or credentials are emitted.
Every base byte is authenticated before edits. New worker, vendor manifest and
matching IPC client are sealed together; older clients must reject this bundle.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
BASES = {
    "installed-margin": ("RELEASE.json", "af7b1e91f30e34943e3ae61e8d8e2c8ed8a6ee00929406862b735b538217f21c"),
    "tie-candidate": ("SOURCE_BUNDLE.json", "a85c419ec6ca58f47b6fdb266d15280d9f471c8e0aefa5b07e345ce14764dc83"),
}
OLD_VENDOR_PINS = "00f35fa9afba2c6f6fd3f90fbb4531dd4ad493f48dddcb4655919a10c082a061"
VERSION = "diana20-pixel-scale-source-v1"
CALLER_SHA = "fcd94731e914598cb85e35627a92cdded017da92f8002e4c3adfb65dd1d3fdc7"
COMPONENT_SHA = "26843f04d258834d497affd0a340cef1ef1f657c2d2852c0d26ffea2ea11e626"
PREPARED_SHA = "73e6c69c039828b013a2674b8b0921da91c6d924c78d18bb07cb5aaa99f98ab0"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_pinned(path, digest, size=None):
    path = Path(path)
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
           for p in (path, *path.parents)):
        raise ValueError("linked source path")
    if not 0 < path.stat().st_size <= 4 * 1024 * 1024:
        raise ValueError("source byte bound")
    raw = path.read_bytes()
    if sha(raw) != digest or size is not None and len(raw) != size:
        raise ValueError("source pin mismatch: " + path.name)
    return raw


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("pinned source seam is not unique: " + old[:70])
    return source.replace(old, new, 1)


def assignment(source, name, value):
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    if len(nodes) != 1:
        raise ValueError("assignment seam not unique: " + name)
    node = nodes[0]
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [name + " = " + repr(value) + "\n"]
    return "".join(lines)


def patch_layout(source):
    source = assignment(source, "VERSION", VERSION + "-layout")
    source = once(source, "PartialPixelGeometry, _discover_corners,", "PartialPixelGeometry, _discover_corners, _corner_size,")
    source = once(source, "from bridge_vision.gambler_classic_reference import GamblerClassicSprite",
                  "from bridge_vision.gambler_classic_reference import GamblerClassicSprite, VARIANT_CARD_SIZE\n"
                  "from bridge_vision.gambler_reference_authority import pinned_sprite_sha256")
    source = once(source, "def discovery_image(frame):", "def discovery_image(frame, card_height=147):")
    source = once(source, "masked[y - 1:y + 19, x0:x1] = frame[y - 1:y + 19, x0:x1]",
                  "cue_height = max(19, round(card_height * .12) + 1)\n"
                  "        masked[y - 1:y + cue_height, x0:x1] = frame[y - 1:y + cue_height, x0:x1]")
    source = once(source, "expected_layout_profile_sha256: str | None = None):",
                  "expected_layout_profile_sha256: str | None = None, card_scale=None):")
    source = once(source,
                  "        if (sprite.variant, sprite.card_width, sprite.card_height, sprite.sprite_sha256) != (5, 109, 147, SPRITE_SHA256) or sha(sprite.payload) != SPRITE_SHA256:\n"
                  "            raise ValueError(\"DIANA20_ORIGINAL_V5_SPRITE_REQUIRED\")",
                  "        if not isinstance(card_scale, dict):\n"
                  "            raise ValueError('DIANA20_SCALE_RECEIPT_REQUIRED')\n"
                  "        if sprite is not None and (\n"
                  "                VARIANT_CARD_SIZE.get(sprite.variant) != (sprite.card_width, sprite.card_height)\n"
                  "                or sprite.sprite_sha256 != pinned_sprite_sha256(sprite.variant)\n"
                  "                or sha(sprite.payload) != sprite.sprite_sha256\n"
                  "                or card_scale.get('selection_status') != 'SELECTED'\n"
                  "                or card_scale.get('selected_sprite_sha256') != sprite.sprite_sha256):\n"
                  "            raise ValueError('DIANA20_PINNED_NATIVE_SPRITE_REQUIRED')")
    source = once(source, '"frame_size": list(FRAME_SIZE), "sprite_sha256": sprite.sprite_sha256,',
                  '"frame_size": list(FRAME_SIZE), "sprite_sha256": sprite.sprite_sha256 if sprite else None,\n'
                  '            "card_scale": dict(card_scale),')
    source = once(source, '        corners = _discover_corners(discovery_image(image), sprite, self._geometry)',
                  "        if sprite is None:\n"
                  "            result['status'] = 'SCALE_' + card_scale['selection_status']\n"
                  "            result['unknown'].append(dict(reason=card_scale['selection_reason'],\n"
                  "                                          kind='CARD_SCALE', card=None, seat=None))\n"
                  "            return result\n"
                  "        discovery_geometry = PartialPixelGeometry(*FRAME_SIZE, sprite.card_width,\n"
                  "            sprite.card_height, TABLE_XYWH, (), auto_discover=True)\n"
                  "        corners = _discover_corners(discovery_image(image, sprite.card_height), sprite, discovery_geometry)")
    source = once(source, '        result["handrow_receipts"] = []',
                  '        result["handrow_receipts"] = []\n        cw, ch = _corner_size(sprite)')
    source = once(source, 'corner.x + 33, corner.y + 47', 'corner.x + cw, corner.y + ch')
    source = once(source, 'corner.y:corner.y+47, corner.x:corner.x+33', 'corner.y:corner.y+ch, corner.x:corner.x+cw')
    source = once(source, 'geometry = PartialPixelGeometry(*FRAME_SIZE, 109, 147, TABLE_XYWH, tuple(accepted))',
                  'geometry = PartialPixelGeometry(*FRAME_SIZE, sprite.card_width, sprite.card_height, TABLE_XYWH, tuple(accepted))')
    source = once(source, '        if self.rank_suit_fallback:\n',
                  '        if self.rank_suit_fallback and sprite.variant == 5:\n')
    source = once(source, '        observed, validated_receipts = _observe_verified_snapshot(',
                  '        result["card_scale"]["matcher_attempts"] = 1\n'
                  '        observed, validated_receipts = _observe_verified_snapshot(')
    return source


def patch_worker(source, frozen, base):
    source = assignment(source, "VERSION", VERSION + "-" + base)
    source = assignment(source, "FROZEN", frozen)
    source = once(source,
                  '            sprite_raw = read_bounded(args.sprite, 16 * 1024 * 1024)\n'
                  '            if sha(sprite_raw) != SPRITE_SHA256:\n'
                  '                raise IntegrityError("original frozen v5 sprite SHA mismatch")\n'
                  '            self.sprite = reference.validate_sprite_bytes(sprite_raw, expected_sha256=SPRITE_SHA256, expected_variant=5)',
                  "            self.scale = importlib.import_module('bridge_vision.diana20_gambler_scale')\n"
                  "            self.selector = self.scale.NativeScaleSelector(sprite_path=args.sprite, asset_root=getattr(args, 'asset_root', None))")
    source = once(source, '        self.banks = self.scoring.make_banks(self.sprite, base, factor, reference)',
                  '        self.base, self.factor = base, factor\n        self.bank_cache = {}')
    source = once(source, '            sprite_sha256=SPRITE_SHA256, margin_enabled=True, geometry_enabled=False,',
                  '            sprite_sha256=None, scale_selection_version=self.scale.VERSION,\n'
                  '            margin_enabled=True, geometry_enabled=False,')
    source = once(source, '        try:\n            with contextlib.redirect_stdout(sys.stderr):\n'
                  '                result = self.geometry.observe(request["frame_path"], self.sprite,',
                  '        import cv2\n        import numpy as np\n'
                  '        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)\n'
                  "        selection = self.selector.select(image)\n"
                  "        sprite = selection.sprite\n"
                  '        try:\n            with contextlib.redirect_stdout(sys.stderr):\n'
                  '                result = self.geometry.observe(request["frame_path"], sprite,')
    source = once(source, '                    expected_layout_profile_sha256=PROFILE_SHA256)',
                  '                    expected_layout_profile_sha256=PROFILE_SHA256, card_scale=selection.evidence)')
    source = once(source,
                  '        return self.layer.recognize(result, image, self.banks, self.scoring,\n'
                  '                                    self.candidate, self.reference, self.runtime)',
                  "        native_v5 = sprite is not None and sprite.variant == 5\n"
                  "        runtime = dict(self.runtime, sprite_sha256=sprite.sprite_sha256 if sprite else None,\n"
                  "            margin_applied=native_v5, card_scale=result['card_scale'])\n"
                  "        if not native_v5:\n"
                  "            return self.scale.compose_without_margin(result, self.candidate, self.layer, runtime)\n"
                  "        if sprite.sprite_sha256 not in self.bank_cache:\n"
                  "            self.bank_cache[sprite.sprite_sha256] = self.scoring.make_banks(\n"
                  "                sprite, self.base, self.factor, self.reference)\n"
                  "        return self.layer.recognize(result, image, self.bank_cache[sprite.sprite_sha256],\n"
                  "                                    self.scoring, self.candidate, self.reference, runtime)")
    source = once(source, '    args = parser.parse_args(argv)',
                  '    parser.add_argument("--asset-root", default=None)\n    args = parser.parse_args(argv)')
    return source


def embed_scale_validator(source, seam):
    from bridge_vision.gambler_classic_reference import VARIANT_CARD_SIZE
    from bridge_vision.gambler_reference_authority import pinned_sprite_sha256

    pins = {v: (pinned_sprite_sha256(v), w, h) for v, (w, h) in VARIANT_CARD_SIZE.items()}
    validator = (ROOT / 'tools/diana20_scale_contract.py').read_text(encoding='utf-8')
    return once(source, seam, 'NATIVE_SCALE_PINS = ' + repr(pins) + '\n\n' + validator + '\n\n' + seam)


def patch_ipc(source, worker_sha, pins_sha):
    source = assignment(source, "GEOMETRY_WORKER_SHA", worker_sha)
    source = assignment(source, "GEOMETRY_PINS_SHA", pins_sha)
    source = once(source, 'scratch, sprite, *, timeout_seconds=60):',
                  'scratch, sprite, *, timeout_seconds=60, asset_root=None):')
    source = once(source, '"--scratch", str(scratch), "--sprite", str(sprite)], timeout_seconds=timeout_seconds)',
                  '"--scratch", str(scratch), "--sprite", str(sprite)]\n'
                  '            + (["--asset-root", str(asset_root)] if asset_root is not None else []),\n'
                  '            timeout_seconds=timeout_seconds)')
    source = embed_scale_validator(source, 'class IPCError(RuntimeError):')
    source = once(source, "        update = result.get('recognizer_update', {})",
                  "        if not validate_native_scale_response(result, NATIVE_SCALE_PINS):\n"
                  "            self.channel._abort()\n"
                  "            raise IPCError('pixel scale attestation mismatch')\n"
                  "        update = result.get('recognizer_update', {})")
    return source


def build(base, base_root, output, *, vendor_root=None, vendor_pins=None):
    base_root, output = Path(base_root), Path(output)
    if output.exists():
        raise ValueError("output must be a new directory")
    manifest_name, expected = BASES[base]
    manifest = json.loads(read_pinned(base_root / manifest_name, expected))
    files = {}
    for item in manifest["files"]:
        name = item["path"]
        if name.startswith(("geometry/", "runtime/")) and name != "runtime/diana20_evidence_composition.py":
            files[name] = read_pinned(base_root / name, item["sha256"], item["bytes"])
    if base == "installed-margin":
        if vendor_root is None or vendor_pins is None:
            raise ValueError("installed baseline needs its saved vendor root and pins")
        raw_pins = read_pinned(vendor_pins, OLD_VENDOR_PINS)
        for item in json.loads(raw_pins)["files"]:
            files["geometry/vendor/" + item["path"]] = read_pinned(
                Path(vendor_root) / item["path"], item["sha256"], item["bytes"])
        files["geometry/CARD_WORKER_PINS.json"] = raw_pins
    original = dict(files)
    for source in ("bridge_vision/gambler_card_scale.py", "tools/diana20_gambler_scale.py"):
        files["geometry/vendor/bridge_vision/" + Path(source).name] = (ROOT / source).read_bytes()
    layout_name = "geometry/vendor/bridge_vision/bridgit_diana20_layout.py"
    files[layout_name] = patch_layout(files[layout_name].decode()).encode()
    # Keep the complete native reference loader/decoder byte-for-byte.
    # Adapter-specific binary I/O lives in the new scale helper.
    prefix = "geometry/vendor/"
    frozen = {name.removeprefix(prefix): sha(raw) for name, raw in sorted(files.items()) if name.startswith(prefix)}
    entries = [dict(path=name, sha256=digest, bytes=len(files[prefix + name])) for name, digest in frozen.items()]
    pins = (json.dumps(dict(schema="diana20-card-worker-pins-v1", files=entries), indent=2) + "\n").encode()
    files["geometry/CARD_WORKER_PINS.json"] = pins
    worker_name = "geometry/diana20_geometry_worker.py"
    files[worker_name] = patch_worker(files[worker_name].decode(), frozen, base).encode()
    ipc_name = "runtime/diana20_visual_ipc.py"
    files[ipc_name] = patch_ipc(files[ipc_name].decode(), sha(files[worker_name]), sha(pins)).encode()
    for name, raw in files.items():
        if name.endswith(".py"):
            compile(raw, name, "exec", dont_inherit=True)
    changed = [name for name, raw in sorted(files.items()) if original.get(name) != raw]
    result = dict(schema="diana20-pixel-scale-source-bundle/v1", version=VERSION + "-" + base,
                  base=base, base_manifest_sha256=expected, state="LOCAL_CANDIDATE",
                  worker_sha256=sha(files[worker_name]), vendor_pins_sha256=sha(pins),
                  geometry_enabled=False, factor_identity_fix_enabled=base == "tie-candidate",
                  installed=False, installation_allowed=False, automatic_promotion_allowed=False,
                  assets_included=False, lifecycle_or_active_entry_included=False,
                  source_frame_shape=[1920, 1010], scale_modes=["native_available_only", "legacy_single_sprite"],
                  margin_supported_variants=[5], old_client_new_pins="MUST_REJECT",
                  changed_files=changed,
                  files=[dict(path=name, bytes=len(raw), sha256=sha(raw)) for name, raw in sorted(files.items())])
    output.mkdir(parents=True)
    for name, raw in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest_bytes = (json.dumps(result, indent=2) + "\n").encode()
    (output / "SOURCE_BUNDLE.json").write_bytes(manifest_bytes)
    # ACTIVE_RECOGNIZER's existing generic worker route reads CODE_BUNDLE.json.
    # This copy is source-only until an explicitly authorized pointer install.
    (output / "CODE_BUNDLE.json").write_bytes(manifest_bytes)
    return result


def prepare_caller(caller_root, bundle_root, asset_root, output):
    """Seal a proposed prepared-frame caller contract; never apply it.

    Existing owner, binding, dispatch, false-authority and subprocess lifecycle
    gates are unchanged. The generic ACTIVE worker route needs no code change.
    Assets stay private and external to the source bundle.
    """
    from tools.diana20_gambler_scale import NativeScaleSelector

    caller_root, bundle_root, output = Path(caller_root), Path(bundle_root), Path(output)
    if output.exists():
        raise ValueError("caller output must be a new directory")
    source = read_pinned(caller_root / 'universal_video/diana20_recognizer.py', CALLER_SHA).decode()
    prepared = read_pinned(caller_root / 'universal_video/diana20_prepared_frames.py', PREPARED_SHA).decode()
    contract = json.loads(read_pinned(caller_root / 'ops/diana20-recognizer-component.json', COMPONENT_SHA))
    raw_manifest = (bundle_root / 'CODE_BUNDLE.json').read_bytes()
    manifest = json.loads(raw_manifest)
    if manifest.get('schema') != 'diana20-pixel-scale-source-bundle/v1':
        raise ValueError('scale bundle required')
    base = manifest.get('base')
    if (type(base) is not str or base not in BASES
            or manifest.get('version') != VERSION + '-' + base):
        raise ValueError('unsupported scale bundle version')
    for item in manifest['files']:
        read_pinned(bundle_root / item['path'], item['sha256'], item['bytes'])
    selector = NativeScaleSelector(sprite_path=None, asset_root=asset_root)
    if not selector.sprites:
        raise ValueError('caller needs at least one verified native sprite')
    install_root = PurePosixPath('/opt/bridge-school/universal-video/code-bundles') / manifest['version']
    pointer = dict(version=manifest['version'], code_root=str(install_root), manifest_sha256=sha(raw_manifest))
    pointer_raw = (json.dumps(pointer, indent=2) + '\n').encode()
    entries = {item['path']: item for item in manifest['files']}
    worker = 'geometry/diana20_geometry_worker.py'
    pins = 'geometry/CARD_WORKER_PINS.json'
    contract['vendor'] = str(install_root / 'geometry/vendor')
    contract['pins'] = str(install_root / pins)
    contract['pins_sha256'] = entries[pins]['sha256']
    contract['asset_root'] = str(install_root / 'assets')
    first = selector.sprites[0]
    contract['sprite'] = str(install_root / f'assets/all-v{first.variant}.png')
    contract['runtime'].update(worker_path=str(install_root / worker),
        worker_sha256=entries[worker]['sha256'], version=manifest['version'],
        factor_identity_fix_enabled=manifest['factor_identity_fix_enabled'],
        scale_selection_version='diana20-pixel-native-scale-v1')
    # Preserve the existing ACTIVE entry pin. Replace only the prospective
    # pointer and recognizer/asset pins; no owner or lifecycle contract changes.
    active = next(item for item in contract['files'] if item['path'] == contract['entry'])
    pointer_path = str(PurePosixPath(contract['entry']).parent / 'CURRENT_RECOGNIZER.json')
    files = [active, dict(path=pointer_path, sha256=sha(pointer_raw), max_bytes=65536, private=True)]
    for name in (worker, pins):
        files.append(dict(path=str(install_root / name), sha256=entries[name]['sha256'],
                          max_bytes=max(131072, entries[name]['bytes'])))
    for sprite in selector.sprites:
        files.append(dict(path=str(install_root / f'assets/all-v{sprite.variant}.png'),
                          sha256=sprite.sprite_sha256, max_bytes=8 * 1024 * 1024))
    contract['files'] = files
    contract['scale_scope'] = 'Measured available native sizes; original fixed layout; Margin only v5.'
    raw_contract = (json.dumps(contract, indent=2) + '\n').encode()
    source = assignment(source, 'COMPONENT_SHA256', sha(raw_contract))
    source = embed_scale_validator(source, 'class RecognizerError(RuntimeError):')
    source = once(source, '            "--sprite", c["sprite"]]',
                  '            "--sprite", c["sprite"], "--asset-root", c["asset_root"]]')
    source = once(source, '            or runtime.get("factor_identity_fix_enabled") is not False):',
                  '            or runtime.get("factor_identity_fix_enabled") is not contract["runtime"]["factor_identity_fix_enabled"]):')
    source = once(source, '    # Keep baseline cards, UNKNOWN history and every observation field intact.',
                  '    if not validate_native_scale_response(result, NATIVE_SCALE_PINS):\n'
                  '        _fail("RESPONSE_SCALE")\n'
                  '    # Keep baseline cards, UNKNOWN history and every observation field intact.')
    prepared = once(prepared, '            "--sprite", c["sprite"]]',
                    '            "--sprite", c["sprite"], "--asset-root", c["asset_root"]]')
    prepared = once(prepared, 'len(abi["argv"]) == 15', 'len(abi["argv"]) == 17')
    compile(source, 'diana20_recognizer.py', 'exec', dont_inherit=True)
    compile(prepared, 'diana20_prepared_frames.py', 'exec', dont_inherit=True)
    output_files = {'universal_video/diana20_recognizer.py': source.encode(),
                    'universal_video/diana20_prepared_frames.py': prepared.encode(),
                    'ops/diana20-recognizer-component.json': raw_contract,
                    'CURRENT_RECOGNIZER.proposed.json': pointer_raw}
    output.mkdir(parents=True)
    for name, raw in output_files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    receipt = dict(schema='diana20-scale-caller-proposal/v1', applied=False,
                   target=str(install_root), base_caller_sha256=CALLER_SHA,
                   base_prepared_frames_sha256=PREPARED_SHA,
                   base_component_sha256=COMPONENT_SHA, bundle_manifest_sha256=sha(raw_manifest),
                   asset_variants=[s.variant for s in selector.sprites],
                   owner_lifecycle_dispatch_changed=False,
                   files=[dict(path=name, bytes=len(raw), sha256=sha(raw)) for name, raw in sorted(output_files.items())])
    (output / 'CALLER_PROPOSAL.json').write_bytes((json.dumps(receipt, indent=2) + '\n').encode())
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", choices=BASES, required=True)
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--vendor-root", type=Path)
    parser.add_argument("--vendor-pins", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--caller-root", type=Path)
    parser.add_argument("--asset-root", type=Path)
    parser.add_argument("--caller-output", type=Path)
    args = parser.parse_args()
    if any((args.caller_root, args.asset_root, args.caller_output)) and not all((args.caller_root, args.asset_root, args.caller_output)):
        parser.error('caller-root, asset-root and caller-output must be supplied together')
    result = build(args.base, args.base_root, args.output,
                   vendor_root=args.vendor_root, vendor_pins=args.vendor_pins)
    print(json.dumps({key: result[key] for key in ("version", "worker_sha256", "vendor_pins_sha256", "changed_files")}))
    if args.caller_root:
        proposal = prepare_caller(args.caller_root, args.output, args.asset_root, args.caller_output)
        print(json.dumps(dict(caller_target=proposal['target'], applied=proposal['applied'], assets=proposal['asset_variants'])))


if __name__ == "__main__":
    main()
