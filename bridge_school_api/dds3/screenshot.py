"""Screenshot adapter contract for Bridge School DDS3.

Vision/OCR is deliberately outside the mathematical DDS3 engine. A vision caller
extracts fields from the screenshot into ScreenshotDealObservation; this adapter
normalizes and validates board metadata before DDS3 is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import BridgeDeal

DEALERS = ("N", "E", "S", "W")
VULS = ("None", "NS", "EW", "Both")
# Standard duplicate board cycle, boards 1..16.
VUL_CYCLE = ("None", "NS", "EW", "Both", "NS", "EW", "Both", "None", "EW", "Both", "None", "NS", "Both", "None", "NS", "EW")


def derive_board_metadata(board_number: int) -> tuple[str, str]:
    if board_number < 1:
        raise ValueError("board_number must be >= 1")
    dealer = DEALERS[(board_number - 1) % 4]
    vulnerability = VUL_CYCLE[(board_number - 1) % 16]
    return dealer, vulnerability


@dataclass(frozen=True)
class ObservedField:
    value: Any
    confidence: float | None = None
    source: str = "screenshot"


@dataclass(frozen=True)
class ScreenshotDealObservation:
    hands: dict[str, dict[str, str]]
    board_number: ObservedField | None = None
    dealer: ObservedField | None = None
    vulnerability: ObservedField | None = None
    hand_confidence: dict[str, dict[str, float]] = field(default_factory=dict)
    extra_metadata: dict[str, ObservedField] = field(default_factory=dict)

    def canonicalize(self) -> tuple[BridgeDeal, dict[str, Any]]:
        warnings: list[str] = []
        board = int(self.board_number.value) if self.board_number is not None else None
        derived_dealer = derived_vul = None
        if board is not None:
            derived_dealer, derived_vul = derive_board_metadata(board)
        dealer = str(self.dealer.value).upper() if self.dealer is not None else derived_dealer
        vul = str(self.vulnerability.value) if self.vulnerability is not None else derived_vul
        if dealer not in DEALERS:
            raise ValueError("dealer missing or invalid")
        aliases = {"NONE":"None","LOVE":"None","-":"None","NS":"NS","N-S":"NS","EW":"EW","E-W":"EW","BOTH":"Both","ALL":"Both"}
        vul = aliases.get(str(vul).upper(), vul)
        if vul not in VULS:
            raise ValueError("vulnerability missing or invalid")
        if derived_dealer and self.dealer is not None and dealer != derived_dealer:
            warnings.append(f"dealer_conflict: observed={dealer} derived={derived_dealer}")
        if derived_vul and self.vulnerability is not None and vul != derived_vul:
            warnings.append(f"vulnerability_conflict: observed={vul} derived={derived_vul}")
        deal = BridgeDeal(self.hands, dealer=dealer, vulnerability=vul)
        deal.validate()
        provenance = {
            "input_kind":"screenshot_observation",
            "board_number":board,
            "dealer":{
                "value":dealer,
                "observed":self.dealer is not None,
                "derived_from_board":self.dealer is None and board is not None,
                "confidence":self.dealer.confidence if self.dealer is not None else None,
                "source":self.dealer.source if self.dealer is not None else None,
            },
            "vulnerability":{
                "value":vul,
                "observed":self.vulnerability is not None,
                "derived_from_board":self.vulnerability is None and board is not None,
                "confidence":self.vulnerability.confidence if self.vulnerability is not None else None,
                "source":self.vulnerability.source if self.vulnerability is not None else None,
            },
            "board_number_observation":{
                "confidence":self.board_number.confidence if self.board_number is not None else None,
                "source":self.board_number.source if self.board_number is not None else None,
            },
            "warnings":warnings,
            "recognition":{
                "cards_complete":True,
                "unique_cards":52,
                "metadata_status":"validated_with_warnings" if warnings else "validated",
                "hand_confidence":self.hand_confidence,
            },
            "extra_metadata":{k:{"value":v.value,"confidence":v.confidence,"source":v.source} for k,v in self.extra_metadata.items()},
        }
        return deal, provenance
