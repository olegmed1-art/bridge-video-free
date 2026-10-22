"""Mark a contradictory factor plateau without accepting another card.

Internal inputs must come from the SAME already selected registration. This
function does not search pixels, combine component maxima from different ROIs,
change thresholds, mutate history, or promote a machine candidate.
"""
from __future__ import annotations

import math
from numbers import Real

VERSION = 'same-roi-factor-tie-diagnostic-v1'
RANKS = 'AKQJT98765432'
SUITS = 'CDHS'


def _scores(values, size):
    values = tuple(values)
    if len(values) != size or any(isinstance(v, bool) or not isinstance(v, Real)
                                 or not math.isfinite(v) for v in values):
        raise ValueError('finite component score arrays required')
    return values


def mark_factor_identity_tie(detail, rank_scores, suit_scores):
    """Return the original object, or a diagnostic UNKNOWN with legacy history.

min(rank, suit) is a quality score: a weaker component can flatten several
identities. Only flag a zero-margin legacy choice contradicted by UNIQUE
component maxima at its own ROI and the SAME joint score. Other diagnostics
remain verbatim. A component candidate is never a card for acceptance.
"""
    ranks, suits = _scores(rank_scores, 13), _scores(suit_scores, 4)
    if detail.get('tie_policy_version') == VERSION:
        return detail
    card, margin = detail.get('best_card'), detail.get('identity_margin')
    if (not isinstance(card, str) or len(card) != 2 or card[0] not in RANKS
            or card[1] not in SUITS or isinstance(margin, bool)
            or not isinstance(margin, Real) or not math.isfinite(margin) or margin < 0):
        raise ValueError('valid legacy identity and margin required')
    if margin != 0:
        return detail
    rank_top, suit_top = max(ranks), max(suits)
    ri = [i for i, v in enumerate(ranks) if v == rank_top]
    si = [i for i, v in enumerate(suits) if v == suit_top]
    if len(ri) != 1 or len(si) != 1:
        return detail
    component = RANKS[ri[0]] + SUITS[si[0]]
    old_joint = min(ranks[RANKS.index(card[0])], suits[SUITS.index(card[1])])
    if component == card or old_joint != min(rank_top, suit_top):
        return detail
    return dict(detail, best_card=None, identity_status='TIE', status='UNKNOWN',
                component_candidate=component, card_for_acceptance=None,
                automatic_acceptance_allowed=False, tie_policy_version=VERSION,
                legacy_factor_diagnostic=detail)
