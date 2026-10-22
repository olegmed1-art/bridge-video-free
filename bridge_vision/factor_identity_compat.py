"""Versioned diagnostic parity views; never an acceptance or authority adapter."""
from __future__ import annotations

import json
import math
from typing import Any, Literal, TypedDict

from bridge_vision.factor_identity_diagnostic import (
    VERSION, RANKS, SUITS, mark_factor_identity_tie,
)

CARDS = frozenset(r+s for r in RANKS for s in SUITS)
TIE_FIELDS = frozenset(('identity_status', 'status', 'component_candidate',
    'card_for_acceptance', 'automatic_acceptance_allowed', 'tie_policy_version',
    'legacy_factor_diagnostic'))
NUMERIC_FIELDS = ('joint_score', 'identity_margin', 'rank_margin', 'suit_margin',
                  'whole_corner_score', 'white_retention')
TOLERANCE = 1e-6  # The existing diagnostic parity tolerance, never an identity margin.


class LegacyFactorDiagnostic(TypedDict):
    best_card: str
    identity_margin: float


class TieFactorDiagnostic(TypedDict):
    best_card: None
    identity_status: Literal['TIE']
    status: Literal['UNKNOWN']
    component_candidate: str
    card_for_acceptance: None
    automatic_acceptance_allowed: Literal[False]
    tie_policy_version: Literal['same-roi-factor-tie-diagnostic-v1']
    legacy_factor_diagnostic: dict[str, Any]


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_tie_diagnostic(value) -> Literal['TIE']:
    """Validate the current envelope. Same-ROI score proof is checked separately."""
    try:
        legacy = value['legacy_factor_diagnostic']
        valid = (type(value) is dict and type(legacy) is dict
            and not (TIE_FIELDS & set(legacy))
            and legacy.get('best_card') in CARDS
            and type(legacy.get('identity_margin')) in (int, float)
            and legacy['identity_margin'] == 0
            and set(value) == set(legacy) | TIE_FIELDS
            and value['best_card'] is None and value['identity_status'] == 'TIE'
            and value['status'] == 'UNKNOWN' and value['card_for_acceptance'] is None
            and value['automatic_acceptance_allowed'] is False
            and value['tie_policy_version'] == VERSION
            and value['component_candidate'] in CARDS
            and value['component_candidate'] != legacy['best_card']
            and all(_json(value[k]) == _json(v) for k,v in legacy.items() if k != 'best_card'))
        _json(value)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('INVALID_FACTOR_TIE_PROTOCOL')
    return 'TIE'


def factor_parity_views(stored, rebuilt, rank_scores, suit_scores):
    """Keep legacy comparisons, with an independently recomputed current view.

    A failed/future envelope is NEVER unwrapped. The caller retains its old
    checks and its global parity-failure veto. Untouched legacy diagnostics do
    not acquire an optional field or a new parity check.
    """
    failed = dict(stored_legacy=stored, current_recomputed=None, check=False)
    if type(stored) is not dict or type(rebuilt) is not dict:
        return failed
    try:
        current_input = bool(TIE_FIELDS & set(stored))
        if current_input:
            validate_tie_diagnostic(stored)
            legacy = stored['legacy_factor_diagnostic']
        else:
            legacy = stored
        expected = mark_factor_identity_tie(legacy, rank_scores, suit_scores)
        if expected is legacy:
            return failed if current_input else dict(stored_legacy=stored,
                                                     current_recomputed=None, check=None)
        validate_tie_diagnostic(expected)
        if current_input and _json(expected) != _json(stored):
            return failed
        # Preserve both the original exact metadata checks and numeric tolerance.
        if any(_json(legacy[k]) != _json(rebuilt[k])
               for k in ('best_card', 'gates', 'roi_xyxy', 'roi_bgr_sha256')):
            return failed
        for k in NUMERIC_FIELDS:
            if (type(legacy[k]) not in (int, float) or type(rebuilt[k]) not in (int, float)
                    or not math.isfinite(legacy[k]) or not math.isfinite(rebuilt[k])
                    or abs(legacy[k] - rebuilt[k]) > TOLERANCE):
                return failed
        full_rebuilt = dict(legacy, **rebuilt)
        current = mark_factor_identity_tie(full_rebuilt, rank_scores, suit_scores)
        validate_tie_diagnostic(current)
        if current['component_candidate'] != expected['component_candidate']:
            return failed
        return dict(stored_legacy=legacy, current_recomputed=current, check=True)
    except (KeyError, TypeError, ValueError):
        return failed
