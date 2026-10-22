"""Offline code/import verifier. It never constructs a worker or opens media."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import socket
import sys


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def need(ok, reason):
    if not ok:
        raise AssertionError(reason)


def no_network(*args, **kwargs):
    raise AssertionError('network is forbidden in source verification')


def module_from_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verify(root, expected_manifest_sha):
    root = Path(root).resolve(strict=True)
    need(not any(n == 'bridge_vision' or n.startswith('bridge_vision.') or
                 n == 'bridge_contracts' or n.startswith('bridge_contracts.') for n in sys.modules),
         'run verifier in a fresh isolated Python process')
    raw = (root / 'SOURCE_BUNDLE.json').read_bytes()
    need(sha(raw) == expected_manifest_sha, 'external manifest pin mismatch')
    manifest = json.loads(raw)
    need(manifest['release_status'] == 'HOLD' and manifest['installed'] is False and
         manifest['installation_allowed'] is False and manifest['activation_allowed'] is False and
         manifest['geometry_enabled'] is False and manifest['factor_identity_fix_enabled'] is True,
         'candidate release gates changed')
    inventory = {e['path']: e for e in manifest['files']}
    need(len(inventory) == len(manifest['files']), 'duplicate source path')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    need(actual == set(inventory) | {'SOURCE_BUNDLE.json'}, 'bundle has unpinned or missing files')
    for name, entry in inventory.items():
        data = (root / name).read_bytes()
        need(len(data) == entry['bytes'] and sha(data) == entry['sha256'], 'source file pin mismatch')
        need(name.endswith('.py') or name.endswith('.json'), 'non-code/metadata artifact in bundle')
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8'))
    old_socket, old_connect = socket.socket, socket.create_connection
    socket.socket = socket.create_connection = no_network
    try:
        worker = module_from_file('_factor_tie_source_worker', root / 'geometry/diana20_geometry_worker.py')
        vendor_root = root / 'geometry/vendor'
        pin_path = root / 'geometry/CARD_WORKER_PINS.json'
        verified_root, verified = worker.verify_vendor(vendor_root, pin_path, manifest['vendor_pins_sha256'])
        finder = worker.PinnedImports(verified_root, verified)
        finder.check_loaded()
        sys.meta_path.insert(0, finder)
        try:
            # Import only code. Do not instantiate GeometryWorker, observers,
            # sprites, banks, frames, layout profiles or scoring functions.
            imported = []
            for name in sorted(finder.names):
                importlib.import_module(name)
                imported.append(name)
            finder.check_loaded()
            factor = sys.modules['bridge_vision.bridgit_rank_suit_candidate']
            base = sys.modules['bridge_vision.bridgit_partial_pixel_observer']
            need('with_receipts' in inspect.signature(base.GamblerPartialPixelObserver.__init__).parameters,
                 'frozen observer receipt API absent')
            need('with_receipts' in inspect.signature(factor.GamblerRankSuitCandidate.__init__).parameters,
                 'factor receipt API absent')
            for name, expected in worker.HELPERS.items():
                need(sha((root / 'geometry' / name).read_bytes()) == expected, 'helper pin mismatch')
            helpers = {name: worker.load_helper(name) for name in worker.HELPERS}
            layer = helpers['release_layer.py']
            need(layer.VERSION == 'diana20-margin-factor-tie-source-r1-20261022', 'wrong candidate layer')
            need(manifest['worker_sha256'] == sha((root / 'geometry/diana20_geometry_worker.py').read_bytes()),
                 'worker manifest pin mismatch')
            result = dict(status='PASS_SOURCE_AND_IMPORT_CLOSURE_ONLY', pinned_files=len(inventory),
                source_manifest_sha256=expected_manifest_sha, version=manifest['version'],
                worker_sha256=manifest['worker_sha256'], vendor_pins_sha256=manifest['vendor_pins_sha256'],
                vendor_files=len(verified), imported_vendor_modules=imported,
                helper_modules=sorted(helpers), observer_with_receipts_api=True,
                worker_constructed=False, observe_called=False, frames_or_sprite_read=False,
                network_calls=0, runtime_or_installation=False,
                live_release_mutation_performed=False, geometry_enabled=False, release_status='HOLD')
        finally:
            sys.meta_path.remove(finder)
    finally:
        socket.socket, socket.create_connection = old_socket, old_connect
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--receipt', type=Path)
    a = p.parse_args(argv)
    result = verify(a.bundle, a.manifest_sha256)
    raw = (json.dumps(result, sort_keys=True, indent=2) + '\n').encode('utf-8')
    if a.receipt:
        with a.receipt.open('xb') as stream:
            stream.write(raw)
    print(raw.decode('utf-8'))


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    main()
