"""Build a create-only, code-only recognizer candidate. Never install or launch it."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

VERSION = 'diana20-margin-factor-tie-source-candidate-20261022-r1'
WORKER_VERSION = 'diana20-margin-factor-tie-source-worker-r1'
LAYER_VERSION = 'diana20-margin-factor-tie-source-r1-20261022'
BASE_MANIFEST_SHA = 'af7b1e91f30e34943e3ae61e8d8e2c8ed8a6ee00929406862b735b538217f21c'
BASE_PINS_SHA = '00f35fa9afba2c6f6fd3f90fbb4531dd4ad493f48dddcb4655919a10c082a061'
FIXED_FACTOR_SHA = '76bc2692a28a2f0ee668e2a84e71a4fed79bd65fe6a377fd576bb28f43a71938'
IDENTITY_HELPER_SHA = 'f404dcb53f8b53236f2e50eb9e2f5dc2ac5482dde7580edb88b0e75cbb748f2d'
OVERRIDE_TARGETS = frozenset((
    'geometry/vendor/bridge_vision/bridgit_rank_suit_candidate.py',
    'geometry/vendor/bridge_vision/factor_identity_diagnostic.py',
    'geometry/vendor/bridge_vision/factor_identity_compat.py',
    'geometry/release_scoring.py',
    'geometry/component_margin_candidate.py',
))


class BundleError(ValueError):
    pass


def need(ok, reason):
    if not ok:
        raise BundleError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def safe_relative(name):
    need(type(name) is str and name and '\\' not in name and ':' not in name and
         not PurePosixPath(name).is_absolute() and
         all(x not in ('', '.', '..') for x in name.split('/')), 'unsafe source path')
    return name


def read_source(path, expected_sha=None, expected_size=None):
    path = Path(path)
    need(not path.is_symlink() and not (hasattr(path, 'is_junction') and path.is_junction()),
         'source symlink or junction refused')
    raw = path.read_bytes()
    need(0 < len(raw) <= 4 * 1024 * 1024, 'source byte bound')
    if expected_sha is not None:
        need(sha(raw) == expected_sha, 'source SHA mismatch: ' + path.name)
    if expected_size is not None:
        need(len(raw) == expected_size, 'source size mismatch: ' + path.name)
    return raw


def strict_json(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            need(key not in out, 'duplicate JSON key')
            out[key] = value
        return out
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(BundleError('nonfinite JSON')))


def assignment(source, name):
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    need(len(nodes) == 1, 'source assignment not unique: ' + name)
    return nodes[0]


def value(source, name):
    return ast.literal_eval(assignment(source, name).value)


def replace_assignment(source, name, new_value):
    node = assignment(source, name)
    before = ast.get_source_segment(source, node)
    need(before is not None and source.count(before) == 1, 'ambiguous source replacement')
    return source.replace(before, name + ' = ' + repr(new_value))


def replace_once(source, before, after):
    need(source.count(before) == 1, 'source marker not unique')
    return source.replace(before, after)


def base_sources(release_root, vendor_root, pins_path):
    """Read exact raw hashes before parsing manifests or source ASTs."""
    release_root, vendor_root = Path(release_root), Path(vendor_root)
    manifest_raw = read_source(release_root / 'RELEASE.json', BASE_MANIFEST_SHA)
    manifest = strict_json(manifest_raw)
    need(manifest.get('version') == 'diana20-margin-installed-20261020-r1' and
         manifest.get('geometry_enabled') is False and
         manifest.get('factor_identity_fix_enabled') is False, 'unexpected base release')
    sources = {}
    for item in manifest['files']:
        name = safe_relative(item['path'])
        need(name not in sources, 'duplicate base source')
        sources[name] = read_source(release_root / name, item['sha256'], item['bytes'])
    need(len(sources) == 9, 'base release inventory changed')
    pins_raw = read_source(pins_path, BASE_PINS_SHA)
    pins = strict_json(pins_raw)
    need(set(pins) == {'schema', 'files'} and pins['schema'] == 'diana20-card-worker-pins-v1',
         'base vendor schema changed')
    vendor = {}
    for item in pins['files']:
        name = safe_relative(item['path'])
        need(name not in vendor, 'duplicate vendor source')
        vendor[name] = read_source(vendor_root / name, item['sha256'], item['bytes'])
    actual = set()
    for path in vendor_root.rglob('*'):
        need(not path.is_symlink() and not (hasattr(path, 'is_junction') and path.is_junction()),
             'base vendor symlink or junction refused')
        if path.is_file():
            actual.add(path.relative_to(vendor_root).as_posix())
    need(actual == set(vendor), 'base vendor has unpinned or missing files')
    need(len(vendor) == 15, 'base vendor inventory changed')
    return sources, vendor


def override_sources(path, expected_sha):
    raw = read_source(path, expected_sha)
    manifest = strict_json(raw)
    need(type(manifest) is dict and set(manifest) == {'schema', 'files'} and
         manifest['schema'] == 'recognizer-source-overrides/v1', 'override manifest schema')
    sources = {}
    for item in manifest['files']:
        need(type(item) is dict and set(item) == {'target', 'source', 'sha256', 'bytes'},
             'override source descriptor')
        target = safe_relative(item['target'])
        need(target in OVERRIDE_TARGETS and target not in sources, 'unexpected override target')
        source = Path(item['source'])
        need(source.is_absolute(), 'override source must be explicit absolute path')
        sources[target] = read_source(source, item['sha256'], item['bytes'])
        ast.parse(sources[target].decode('utf-8'))
    need(set(sources) == OVERRIDE_TARGETS, 'incomplete override inventory')
    need(sha(sources['geometry/vendor/bridge_vision/bridgit_rank_suit_candidate.py']) == FIXED_FACTOR_SHA,
         'fixed factor source differs from reviewed postimage')
    need(sha(sources['geometry/vendor/bridge_vision/factor_identity_diagnostic.py']) == IDENTITY_HELPER_SHA,
         'identity helper differs from reviewed source')
    return sources


def assemble_sources(base, vendor, overrides, include_ipc=True):
    files = {'geometry/vendor/' + name: raw for name, raw in vendor.items()}
    files.update(overrides)
    layer = base['geometry/release_layer.py'].decode('utf-8')
    layer = replace_assignment(layer, 'VERSION', LAYER_VERSION)
    layer = replace_once(layer, 'geometry_enabled=False, factor_identity_fix_enabled=False,',
                         'geometry_enabled=False, factor_identity_fix_enabled=True,')
    layer = replace_once(layer, 'Diana20: installed Margin recognizer',
                         'Diana20: source candidate Margin recognizer')
    layer = replace_once(layer, 'Original observations are retained verbatim. Geometry and the diagnostic identity fix are disabled. ',
                         'Original observations are retained verbatim. Geometry90 remains disabled. '
                         'The factor tie diagnostic correction is present in this source candidate; installation is not attested. ')
    layer = replace_once(layer, "        effective.append(dict(card=d['card'],",
        "        factor_view = u.get('factor_diagnostic')\n"
        "        if (d.get('status') != 'ADDITIONAL_MACHINE_CANDIDATE'\n"
        "                or type(d.get('card')) is not str or d['card'] not in candidate.CARDS\n"
        "                or (type(factor_view) is dict and factor_view.get('identity_status') == 'TIE')):\n"
        "            raise ValueError('ADDITION_NULL_OR_TIE_IDENTITY_INVALID')\n"
        "        effective.append(dict(card=d['card'],")
    files['geometry/release_layer.py'] = layer.encode('utf-8')
    files['runtime/diana20_release_layer.py'] = files['geometry/release_layer.py']
    helpers = {name: sha(files['geometry/' + name]) for name in
               ('component_margin_candidate.py', 'release_scoring.py', 'release_layer.py')}
    frozen = {name.removeprefix('geometry/vendor/'): sha(raw) for name, raw in files.items()
              if name.startswith('geometry/vendor/')}
    pin_entries = [{'path': name, 'sha256': frozen[name],
                    'bytes': len(files['geometry/vendor/' + name])} for name in sorted(frozen)]
    pin_raw = encode({'schema': 'diana20-card-worker-pins-v1', 'files': pin_entries})
    files['geometry/CARD_WORKER_PINS.json'] = pin_raw
    worker = base['geometry/diana20_geometry_worker.py'].decode('utf-8')
    worker = replace_once(worker, 'All card algorithms are original pinned vendor bytes; no recognition here.',
                         'Card algorithms are pinned candidate bytes; pixel scoring is delegated to vendor modules.')
    worker = replace_assignment(worker, 'VERSION', WORKER_VERSION)
    worker = replace_assignment(worker, 'FROZEN', dict(sorted(frozen.items())))
    worker = replace_assignment(worker, 'HELPERS', helpers)
    worker = replace_once(worker, '            factor_identity_fix_enabled=False)',
                          '            factor_identity_fix_enabled=True)')
    files['geometry/diana20_geometry_worker.py'] = worker.encode('utf-8')
    if include_ipc:
        ipc = base['runtime/diana20_visual_ipc.py'].decode('utf-8')
        ipc = replace_assignment(ipc, 'GEOMETRY_WORKER_SHA', sha(files['geometry/diana20_geometry_worker.py']))
        ipc = replace_assignment(ipc, 'GEOMETRY_PINS_SHA', sha(pin_raw))
        ipc = replace_once(ipc, "update.get('version') != 'diana20-installed-margin-r1-20261020'",
                           "update.get('version') != " + repr(LAYER_VERSION))
        ipc = replace_once(ipc,
            "or runtime.get('margin_enabled') is not True or runtime.get('geometry_enabled') is not False):",
            "or runtime.get('margin_enabled') is not True or runtime.get('geometry_enabled') is not False\n"
            "                or runtime.get('factor_identity_fix_enabled') is not True\n"
            "                or update.get('factor_identity_fix_enabled') is not True):")
        files['runtime/diana20_visual_ipc.py'] = ipc.encode('utf-8')
    for name, raw in files.items():
        if name.endswith('.py'):
            ast.parse(raw.decode('utf-8'))
    return files, helpers, frozen


def write_bundle(files, output, override_sha, include_ipc):
    output = Path(output)
    need(not output.exists(), 'output must be a new directory')
    zip_path = output.with_suffix('.zip')
    need(not zip_path.exists(), 'archive must not overwrite an existing file')
    for name, raw in files.items():
        safe_relative(name)
        need(name.startswith(('geometry/', 'runtime/')) and name.endswith(('.py', '.json')),
             'unexpected non-source bundle target')
        need(type(raw) is bytes and 0 < len(raw) <= 4 * 1024 * 1024, 'invalid source bytes')
    source_entries = [{'path': name, 'bytes': len(raw), 'sha256': sha(raw)}
                      for name, raw in sorted(files.items())]
    manifest = dict(schema='recognizer-factor-tie-source-bundle/v1', version=VERSION,
        release_status='HOLD', mode='margin', geometry_enabled=False,
        factor_identity_fix_enabled=True, installed=False, installation_allowed=False,
        activation_allowed=False, automatic_promotion_allowed=False, canonical_activation_allowed=False,
        human_verified=False, full_inference_validated=False, transport_or_owner_bridge_provided=False,
        base_saved_release_manifest_sha256=BASE_MANIFEST_SHA, base_vendor_pins_sha256=BASE_PINS_SHA,
        overrides_manifest_sha256=override_sha, contains_only='CODE_AND_CODE_PIN_METADATA',
        sprite_included=False, video_audio_images_included=False, installers_or_active_entry_included=False,
        matching_ipc_source_included=include_ipc, old_client_new_pins='MUST_REJECT',
        candidate_sha256=sha(files['geometry/component_margin_candidate.py']),
        worker_sha256=sha(files['geometry/diana20_geometry_worker.py']),
        vendor_pins_sha256=sha(files['geometry/CARD_WORKER_PINS.json']),
        files=source_entries)
    files = dict(files, **{'SOURCE_BUNDLE.json': encode(manifest)})
    output.mkdir(parents=False)
    for name, raw in sorted(files.items()):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
    with zipfile.ZipFile(zip_path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, raw in sorted(files.items()):
            item = zipfile.ZipInfo(name, (2026, 10, 22, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100600 << 16
            z.writestr(item, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(zip_path) as z:
        need(z.testzip() is None and set(z.namelist()) == set(files), 'archive inventory/CRC mismatch')
        for name, raw in files.items():
            need(z.read(name) == raw, 'archive byte mismatch')
    return dict(version=VERSION, output=str(output.resolve()), archive=str(zip_path.resolve()),
                archive_sha256=sha(zip_path.read_bytes()), archive_bytes=zip_path.stat().st_size,
                source_manifest_sha256=sha(files['SOURCE_BUNDLE.json']), file_count=len(files),
                vendor_count=sum(name.startswith('geometry/vendor/') for name in files),
                installation_performed=False, activation_performed=False)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-release', type=Path, required=True)
    p.add_argument('--base-vendor', type=Path, required=True)
    p.add_argument('--base-pins', type=Path, required=True)
    p.add_argument('--overrides', type=Path, required=True)
    p.add_argument('--overrides-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--omit-ipc', action='store_true')
    args = p.parse_args(argv)
    base, vendor = base_sources(args.base_release, args.base_vendor, args.base_pins)
    overrides = override_sources(args.overrides, args.overrides_sha256)
    files, _, _ = assemble_sources(base, vendor, overrides, not args.omit_ipc)
    print(json.dumps(write_bundle(files, args.output, args.overrides_sha256, not args.omit_ipc), sort_keys=True))


if __name__ == '__main__':
    main()
