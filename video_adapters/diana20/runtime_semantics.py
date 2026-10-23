"""Automatic current-pixel UI evidence from the existing Diana20 calibration.

Reuses the visual-auction mark predicate, with its unchanged thresholds. This
is a separately named similarity candidate, NOT the old exact-pixel gate and
NOT a reviewed ownership authority. Board epochs and settled intervals cannot
be reconstructed from sparse snapshots. No card recognition or geometry run.
"""
from copy import deepcopy
from hashlib import sha256
import json

from bridge_vision.auction_observer import _same_mark
from bridge_vision.original_deal_solver import RecoveryInputError

VERSION = 'bridge-diana20-current-ui-evidence-v1'
PROFILE_SHA = '7ecad092df4491ac41c11f7c2e18cf56689dd3cb923ca107dcab9c860a1b4f7e'
UI_PINS_SHA = '42b8ee37085b86db72972ba48186a2499ea9acdd52f48ffa5fbffd5b280dbbc0'
NUMBER_BOX = (1258, 62, 1298, 106)  # existing bridgit_diana20_layout constant
POSITIONS = ('top', 'right', 'bottom', 'left')
SEATS = ('N', 'E', 'S', 'W')


def _need(condition, reason):
    if not condition:
        raise RecoveryInputError('SEMANTIC_' + reason)


def _sha(raw):
    return sha256(raw).hexdigest()


def _crop(image, box):
    x0, y0, x1, y1 = box
    return image[y0:y1, x0:x1]


class Diana20RuntimeSemantics:
    """Frozen UI-method calibration, never per-card/per-video answer labels.

    Two existing PDF reference rasters are source-pinned calibration assets.
    Their labels are AI assertions. A matching current raster proves a template
    match only, conditional on that label interpretation. The references have
    no source PTS and therefore supply neither a video identity nor an epoch.
    """
    def __init__(self, profile_bytes, ui_pins_bytes, reference_payloads):
        import cv2
        import numpy as np
        _need(type(profile_bytes) is bytes and len(profile_bytes) <= 65536
              and _sha(profile_bytes) == PROFILE_SHA, 'PROFILE_PIN')
        _need(type(ui_pins_bytes) is bytes and len(ui_pins_bytes) <= 65536
              and _sha(ui_pins_bytes) == UI_PINS_SHA, 'UI_PIN')
        self.profile = json.loads(profile_bytes)
        self.pins = json.loads(ui_pins_bytes)
        _need(isinstance(reference_payloads, (list, tuple)) and len(reference_payloads) == 2,
              'REFERENCE_COUNT')
        self._references = []
        for raw, group in zip(reference_payloads, self.pins['reference_groups']):
            _need(type(raw) is bytes and 24 <= len(raw) <= 16 * 1024 * 1024
                  and _sha(raw) == group['source_sha256'], 'REFERENCE_PIN')
            _need(raw[:8] == b'\x89PNG\r\n\x1a\n' and raw[12:16] == b'IHDR'
                  and [int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')]
                  == self.profile['frame_size'], 'REFERENCE_DIMENSIONS')
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            _need(image is not None and _sha(image.tobytes()) == group['source_pixel_sha256_bgr'],
                  'REFERENCE_PIXEL_PIN')
            _need(all(_sha(_crop(image, roi['bounds']).tobytes()) == roi['pixel_sha256_bgr']
                      for roi in group['regions']), 'REFERENCE_ROI_PIN')
            self._references.append(image)

    def measure(self, image, *, source_video_sha256):
        import cv2
        import numpy as np
        _need(isinstance(image, np.ndarray) and image.dtype == np.uint8 and image.ndim == 3
              and image.shape[2] == 3 and image.shape[:2] == (1010, 1920), 'FRAME_SHAPE')
        if source_video_sha256 != self.profile['source_video_sha256']:
            return {'version': VERSION, 'status': 'UNKNOWN_SOURCE_PROFILE_MISMATCH',
                    'conditional_compass_mapping': None, 'hard_evidence': False}
        checks = []
        for group, reference in zip(self.pins['reference_groups'], self._references):
            regions = []
            for roi in group['regions']:
                actual, expected = _crop(image, roi['bounds']), _crop(reference, roi['bounds'])
                # The unchanged existing predicate includes grayscale variance,
                # mean absolute error <=2 and normalized correlation >=.99.
                matched = bool(_same_mark(actual, expected))
                a = cv2.cvtColor(actual, cv2.COLOR_BGR2GRAY).astype(float)
                b = cv2.cvtColor(expected, cv2.COLOR_BGR2GRAY).astype(float)
                regions.append({**deepcopy(roi), 'current_pixel_sha256_bgr': _sha(actual.tobytes()),
                                'exact_pixels_match': _sha(actual.tobytes()) == roi['pixel_sha256_bgr'],
                                'existing_auction_mark_match': matched,
                                'grayscale_mean_absolute_error': float(np.abs(a - b).mean())})
            checks.append({'reference_raw_sha256': group['source_sha256'], 'regions': regions,
                           'all_regions_match': all(r['existing_auction_mark_match'] for r in regions)})
        matched = [g for g in checks if g['all_regions_match']]
        mappings = []
        for group in matched:
            # Existing compass profile defines the sectors. Require the three
            # distinct disjoint N/E/W calibration glyphs from ONE group.
            cx, cy = self.profile['compass_reference_center']
            visible = {}
            for roi in group['regions']:
                if roi['label'] is None:
                    continue
                x0, y0, x1, y1 = roi['bounds']
                dx, dy = (x0+x1)/2-cx, (y0+y1)/2-cy
                position = ('right' if dx > 0 else 'left') if abs(dx) > abs(dy) else ('bottom' if dy > 0 else 'top')
                visible[position] = roi['label']
            rotations = [dict(zip(POSITIONS, SEATS[n:]+SEATS[:n])) for n in range(4)]
            candidates = [r for r in rotations if all(r[p] == label for p, label in visible.items())]
            if len(visible) == 3 and len(set(visible.values())) == 3 and len(candidates) == 1:
                mappings.append({'mapping': candidates[0], 'observed_glyph_labels': visible,
                                 'inferred_positions': sorted(set(POSITIONS) - set(visible)),
                                 'reference_raw_sha256': group['reference_raw_sha256']})
        mapping = mappings[0] if mappings and all(m['mapping'] == mappings[0]['mapping'] for m in mappings) else None
        x, y, w, h = self.profile['table_xywh']
        board = _crop(image, NUMBER_BOX)
        return {'version': VERSION, 'status': 'CONDITIONAL_CURRENT_PIXEL_MATCH' if mapping else 'UNKNOWN_CURRENT_UI_MISMATCH',
                'method': 'EXISTING_AUCTION_SAME_MARK_NOT_LEGACY_EXACT_UI_GATE',
                'profile_sha256': PROFILE_SHA, 'ui_pins_sha256': UI_PINS_SHA,
                'calibration_semantics': 'AI_LABEL_ASSERTIONS_NOT_HUMAN_VERIFIED',
                'calibration_source_pts': 'UNKNOWN_PDF_REFERENCE_NOT_RUNTIME_VIDEO_BINDING',
                'reference_group_checks': checks, 'conditional_compass_mapping': mapping,
                'table_pixel_sha256_bgr': _sha(image[y:y+h, x:x+w].tobytes()),
                'board_tile': {'region_xyxy': list(NUMBER_BOX), 'pixel_sha256_bgr': _sha(board.tobytes()),
                               'number': None, 'board_epoch': None, 'status': 'PIXEL_FINGERPRINT_ONLY'},
                'settled': {'status': 'UNKNOWN', 'reason': 'NO_CONTIGUOUS_DECODE_WINDOW'},
                'hard_evidence': False, 'canonical_promotion_allowed': False, 'independent_vote_count': 0}


def annotate_observed_pairs(records):
    """Pixel equality/difference is reported, never turned into a board epoch."""
    previous = None
    for record in sorted(records, key=lambda r: (r['source_video_sha256'], r['timestamp_ms'], r['sequence'])):
        current = record.get('semantic_measurements')
        if current is None:
            previous = None
            continue
        current.pop('previous_observed_frame', None)
        prior = previous.get('semantic_measurements') if previous else None
        if prior and previous['source_video_sha256'] == record['source_video_sha256']:
            same_table = prior.get('table_pixel_sha256_bgr') == current.get('table_pixel_sha256_bgr')
            has_tiles = 'board_tile' in prior and 'board_tile' in current
            current['previous_observed_frame'] = {
                'input_row_sha256': previous['input_row_sha256'],
                'interval_ms': record['timestamp_ms'] - previous['timestamp_ms'],
                'table_pixels_equal': same_table if 'table_pixel_sha256_bgr' in current and 'table_pixel_sha256_bgr' in prior else None,
                'board_tile_pixels_equal': prior['board_tile']['pixel_sha256_bgr'] == current['board_tile']['pixel_sha256_bgr'] if has_tiles else None,
                'same_board_proven': False, 'settled_proven': False,
                'coverage': 'SNAPSHOT_PAIR_ONLY_INTERVAL_UNOBSERVED'}
        previous = record
