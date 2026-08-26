from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class AuctionModelError(ValueError):
    pass


PROVENANCE_CLASSES = {"CANON", "MODEL", "UNKNOWN"}
SEATS = ("N", "E", "S", "W")
SUITS = ("S", "H", "D", "C")
FIT_MIN_COMBINED_LENGTH = 8


@dataclass(frozen=True)
class HandView:
    seat: str
    cards: str
    hcp: int
    lengths: Mapping[str, int]


@dataclass(frozen=True)
class PublicSuitPromise:
    """A minimum suit length established by a public, evidenced call."""

    suit: str
    minimum_length: int
    source_call: str
    canon_rule_id: str
    evidence_ref: str

    def __post_init__(self) -> None:
        suit = str(self.suit or "").strip().upper()
        minimum = int(self.minimum_length)
        if suit not in SUITS:
            raise AuctionModelError("public suit promise requires S/H/D/C suit")
        if not 0 <= minimum <= 13:
            raise AuctionModelError("public suit promise minimum must be between 0 and 13")
        if not str(self.source_call or "").strip():
            raise AuctionModelError("public suit promise requires source_call")
        if not str(self.canon_rule_id or "").strip():
            raise AuctionModelError("public suit promise requires canon_rule_id")
        if not str(self.evidence_ref or "").strip():
            raise AuctionModelError("public suit promise requires evidence_ref")
        object.__setattr__(self, "suit", suit)
        object.__setattr__(self, "minimum_length", minimum)


def _parse_hand(seat: str, cards: str) -> HandView:
    parts = str(cards or "").strip().split(".")
    if len(parts) != 4:
        raise AuctionModelError("hand must use S.H.D.C notation")
    lengths = {s: (0 if p == "-" else len(p)) for s, p in zip(SUITS, parts)}
    if sum(lengths.values()) != 13:
        raise AuctionModelError("hand must contain 13 cards")
    hcp_map = {"A": 4, "K": 3, "Q": 2, "J": 1}
    hcp = sum(hcp_map.get(ch, 0) for p in parts for ch in p)
    return HandView(seat=seat, cards=cards, hcp=hcp, lengths=lengths)


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AuctionModelError(f"missing required field: {field}")
    return text


def guaranteed_fit_state(*, actor_length: int, partner_promise: PublicSuitPromise) -> dict[str, Any]:
    """Return fit state from the actor's hand and an evidenced public promise only."""
    actor_length = int(actor_length)
    if actor_length < 0 or actor_length > 13:
        raise AuctionModelError("actor length must be between 0 and 13")
    if not isinstance(partner_promise, PublicSuitPromise):
        raise AuctionModelError("partner_promise must be an evidenced PublicSuitPromise")
    combined = actor_length + partner_promise.minimum_length
    fit_established = combined >= FIT_MIN_COMBINED_LENGTH
    return {
        "actor_length": actor_length,
        "partner_promised_minimum": partner_promise.minimum_length,
        "partner_promise_suit": partner_promise.suit,
        "partner_promise_source_call": partner_promise.source_call,
        "partner_promise_canon_rule_id": partner_promise.canon_rule_id,
        "partner_promise_evidence_ref": partner_promise.evidence_ref,
        "guaranteed_combined_length": combined,
        "fit_established": fit_established,
        "fit_threshold": FIT_MIN_COMBINED_LENGTH,
        "uses_partner_hidden_actual_length": False,
        "support_as_known_fit_allowed": fit_established,
    }


def build_stepwise_auction_model(
    *,
    dealer: str,
    hands: Mapping[str, str],
    steps: Sequence[Mapping[str, Any]],
    canon_revision: str,
) -> dict[str, Any]:
    """Build an auditable auction model without omniscient hand leakage.

    Each proposed call is evaluated only from:
      * the acting player's own hand; and
      * the public auction prefix already produced.

    A step may be labelled CANON only when it carries an explicit canon rule id and
    evidence reference. MODEL is allowed for a bridge-reasoning hypothesis. UNKNOWN
    is used when the school canon does not currently support a choice. No step may
    infer a partner/opponent's exact hidden cards from the full deal.
    """
    dealer = _required_text(dealer, "dealer").upper()
    if dealer not in SEATS:
        raise AuctionModelError("dealer must be N/E/S/W")
    if set(hands) != set(SEATS):
        raise AuctionModelError("hands must contain exactly N/E/S/W")
    parsed = {seat: _parse_hand(seat, hands[seat]) for seat in SEATS}
    canon_revision = _required_text(canon_revision, "canon_revision")

    expected = SEATS.index(dealer)
    public_calls: list[str] = []
    out_steps: list[dict[str, Any]] = []

    for idx, raw in enumerate(steps, start=1):
        seat = _required_text(raw.get("seat"), f"steps[{idx}].seat").upper()
        if seat != SEATS[expected % 4]:
            raise AuctionModelError(f"step {idx}: wrong actor order; expected {SEATS[expected % 4]}")
        call = _required_text(raw.get("call"), f"steps[{idx}].call")
        provenance = _required_text(raw.get("provenance"), f"steps[{idx}].provenance").upper()
        if provenance not in PROVENANCE_CLASSES:
            raise AuctionModelError(f"step {idx}: unsupported provenance {provenance!r}")

        rule_id = raw.get("canon_rule_id")
        evidence_ref = raw.get("evidence_ref")
        if provenance == "CANON":
            if not str(rule_id or "").strip() or not str(evidence_ref or "").strip():
                raise AuctionModelError("CANON step requires canon_rule_id and evidence_ref")
        elif rule_id is not None:
            raise AuctionModelError("non-CANON step cannot claim canon_rule_id")

        actor = parsed[seat]
        constraints = raw.get("constraints_checked") or []
        if not isinstance(constraints, Sequence) or isinstance(constraints, (str, bytes)):
            raise AuctionModelError("constraints_checked must be a sequence")

        decision_context = {
            "actor": seat,
            "actor_hcp": actor.hcp,
            "actor_lengths": dict(actor.lengths),
            "public_auction_before_call": list(public_calls),
            "hidden_hand_access_allowed": False,
        }
        out_steps.append({
            "step": idx,
            "seat": seat,
            "call": call,
            "provenance": provenance,
            "canon_rule_id": rule_id,
            "evidence_ref": evidence_ref,
            "constraints_checked": list(constraints),
            "reason": str(raw.get("reason") or "").strip() or None,
            "decision_context": decision_context,
        })
        public_calls.append(call)
        expected += 1

    return {
        "schema": "tournament-auction-stepwise-model-v1",
        "canon_revision": canon_revision,
        "dealer": dealer,
        "steps": out_steps,
        "public_auction": public_calls,
        "policy": {
            "build_direction": "FORWARD_ONE_CALL_AT_A_TIME",
            "use_final_contract_to_backsolve": False,
            "hidden_hand_access_allowed_for_call_selection": False,
            "fit_definition": "GUARANTEED_COMBINED_LENGTH_AT_LEAST_8",
            "fit_uses_public_promises_not_hidden_actual_length": True,
            "canon_gap_policy": "MARK_MODEL_OR_UNKNOWN_DO_NOT_INVENT_CANON",
            "automatic_student_error_attribution_allowed": False,
        },
    }


def board15_verified_prefix() -> dict[str, Any]:
    """Verified modelling prefix for tournament 30041 board 15."""
    hands = {
        "N": "Q3.Q9.A82.J98753",
        "E": "KJ6.AJ87632.43.6",
        "S": "AT94.5.9765.KQ42",
        "W": "8752.KT4.KQJT.AT",
    }
    out = build_stepwise_auction_model(
        dealer="S",
        hands=hands,
        canon_revision="bridge-school-canon-2026-08-26",
        steps=[
            {"seat": "S", "call": "P", "provenance": "MODEL", "reason": "9 HCP hand; pass retained as modelling hypothesis"},
            {"seat": "W", "call": "1D", "provenance": "MODEL", "reason": "13 HCP, 4-3-4-2; 1NT 15-17 is excluded; exact minor-opening priority requires canon binding"},
            {"seat": "N", "call": "P", "provenance": "MODEL", "reason": "pass retained as modelling hypothesis"},
            {
                "seat": "E",
                "call": "1H",
                "provenance": "CANON",
                "canon_rule_id": "RESP_NEW_SUIT_LEVEL1_4PLUS",
                "evidence_ref": "teacher-confirmed-in-chat-2026-08-25",
                "constraints_checked": ["heart_length>=4", "call_available_at_level_1"],
                "reason": "East has seven hearts; 1H promises 4+ cards",
            },
        ],
    )
    # At West's next turn only the public minimum 4+ hearts from 1H may be used.
    # West has three hearts, so the guaranteed combined length is 7: no fit yet.
    out["next_actor_fit_check"] = {
        "seat": "W",
        "suit": "H",
        **guaranteed_fit_state(
            actor_length=3,
            partner_promise=PublicSuitPromise(
                suit="H",
                minimum_length=4,
                source_call="1H",
                canon_rule_id="RESP_NEW_SUIT_LEVEL1_4PLUS",
                evidence_ref="teacher-confirmed-in-chat-2026-08-25",
            ),
        ),
        "teacher_rule_evidence": "teacher-confirmed-in-chat-2026-08-26",
    }
    return out
