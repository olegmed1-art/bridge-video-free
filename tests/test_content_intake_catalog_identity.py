import hashlib
import json

import pytest

from ops.verify_content_intake_catalog import verify_catalog_snapshot


def fixture():
    export = b',id,status\n0,A,active\n1,,\n\f,id,status\n0,W,external\n'
    digest = hashlib.sha256(export).hexdigest()
    header_hash = hashlib.sha256(json.dumps(['', 'id', 'status'],
        separators=(',', ':')).encode()).hexdigest()
    lanes = ['LEGACY_L1_DRIVE', 'LEARNING_CONTENT', 'WORLD_EXTERNAL']
    binding = dict(drive_spreadsheet_id='test-catalog', revision='7', sha256=digest)
    data = {
        'catalog_snapshot_identity': {
            'immutable_export_revision': '7', 'immutable_export_sha256': digest,
            'drive_spreadsheet_id': 'test-catalog', 'dependent_lanes': lanes,
            'export_format': 'connector-csv-sections-utf8-v1',
            'sections': [{'sheet': name, 'header_sha256': header_hash} for name in ['School','World']],
        },
        'lanes': [{'lane_id': name, 'catalog_snapshot_binding': dict(binding)} for name in lanes],
        'drive_catalog_sheet_rows': {'School': 1, 'World': 1},
    }
    return data, export


def test_exact_bytes_and_nonblank_row_counts():
    data, export = fixture()
    assert verify_catalog_snapshot(data, export) == {'School': 1, 'World': 1}
    with pytest.raises(ValueError, match='BYTES_MISMATCH'):
        verify_catalog_snapshot(data, export.replace(b'active', b'retired'))


@pytest.mark.parametrize('lane_index', range(3))
@pytest.mark.parametrize('field', ['revision','sha256','drive_spreadsheet_id'])
def test_every_intake_lane_requires_exact_source_identity(lane_index, field):
    data, export = fixture()
    data['lanes'][lane_index]['catalog_snapshot_binding'].pop(field)
    with pytest.raises(ValueError, match='LANE_BINDING_MISMATCH'):
        verify_catalog_snapshot(data, export)


@pytest.mark.parametrize('field', ['immutable_export_revision','immutable_export_sha256'])
def test_missing_identity_fails_closed(field):
    data, export = fixture()
    data['catalog_snapshot_identity'].pop(field)
    with pytest.raises(ValueError, match='REQUIRED'):
        verify_catalog_snapshot(data, export)


def test_count_and_header_mismatch_fail_closed():
    data, export = fixture()
    data['drive_catalog_sheet_rows']['School'] = 2
    with pytest.raises(ValueError, match='ROW_COUNTS_MISMATCH'):
        verify_catalog_snapshot(data, export)
    data, export = fixture()
    data['catalog_snapshot_identity']['sections'][0]['header_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='SECTION_ORDER_MISMATCH'):
        verify_catalog_snapshot(data, export)
