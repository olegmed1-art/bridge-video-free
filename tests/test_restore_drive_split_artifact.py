#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import tempfile

from tools.restore_drive_split_artifact import RestoreError, reconstruct, sha256_file, validate_locator


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    blobs = [b'alpha-' * 101, b'beta-' * 97, b'gamma-' * 89]
    parts = []
    specs = []
    for i, blob in enumerate(blobs, start=1):
        p = root / f'part{i:03d}'
        p.write_bytes(blob)
        parts.append(p)
        specs.append({'index': i, 'drive_file_id': f'id-{i}', 'size': len(blob), 'sha256': sha(blob)})
    logical = b''.join(blobs)
    locator = {
        'schema': 'bridge-school-drive-artifact-v1',
        'lifecycle_class': 'P2',
        'logical_file': {'name': 'artifact.zip', 'size': len(logical), 'sha256': sha(logical)},
        'storage': {'provider': 'google_drive', 'layout': 'split-concatenate', 'parts': specs},
    }
    assert validate_locator(locator) == specs
    out = root / 'artifact.zip'
    reconstruct(parts, out, len(logical), sha(logical))
    assert out.read_bytes() == logical
    assert sha256_file(out) == sha(logical)

    single = {
        'schema': 'bridge-school-drive-artifact-v1',
        'lifecycle_class': 'P2',
        'logical_file': {'name': 'one.zip', 'size': len(logical), 'sha256': sha(logical)},
        'storage': {
            'provider': 'google_drive',
            'layout': 'single-file',
            'drive_file_id': 'single-id',
            'size': len(logical),
            'sha256': sha(logical),
        },
    }
    assert validate_locator(single) == [
        {'index': 1, 'drive_file_id': 'single-id', 'size': len(logical), 'sha256': sha(logical)}
    ]

    broken = json.loads(json.dumps(locator))
    broken['storage']['parts'][1]['size'] += 1
    try:
        validate_locator(broken)
    except RestoreError:
        pass
    else:
        raise AssertionError('broken locator must fail closed')

    broken_single = json.loads(json.dumps(single))
    broken_single['storage']['sha256'] = '0' * 64
    try:
        validate_locator(broken_single)
    except RestoreError:
        pass
    else:
        raise AssertionError('mismatched single-file identity must fail closed')

print('RESTORE_DRIVE_SPLIT_ARTIFACT_TEST: PASS')
