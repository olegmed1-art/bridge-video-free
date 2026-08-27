from pathlib import Path
import pytest
from bridge_vision.graphic_cards import GraphicCardBackend
from bridge_vision.native_cards import NativeCardDetectorError,NativeFourSeatCardDetector

def test_graphic_backend_composes_only_confident_rank_and_suit_then_native_assigns_seat():
    def runner(_):return {"table_region":{"x":0,"y":0,"w":1000,"h":600},"candidates":[{"rank":"A","rank_confidence":.98,"suit":"♠","suit_confidence":.97,"box":{"x":490,"y":20,"w":20,"h":30}},{"rank":"10","rank_confidence":.96,"suit":"h","suit_confidence":.95,"box":{"x":490,"y":550,"w":20,"h":30}}]}
    result=NativeFourSeatCardDetector(GraphicCardBackend(runner))(Path("frame.png"));assert result["hands"]=={"N":["AS"],"S":["TH"]};assert result["confidence"]==.95

def test_graphic_backend_rejects_missing_or_low_confidence_signal_without_guessing():
    backend=GraphicCardBackend(lambda _:{"table_region":{"x":0,"y":0,"w":100,"h":100},"candidates":[{"rank":"Q","rank_confidence":.99,"suit":None,"suit_confidence":.99,"box":{"x":1,"y":1,"w":5,"h":5}},{"rank":"K","rank_confidence":.89,"suit":"D","suit_confidence":.99,"box":{"x":1,"y":1,"w":5,"h":5}}]});payload=backend(Path("frame.png"));assert payload["cards"]==[];assert [i["reason"] for i in payload["graphic_evidence"]["rejected"]]==["INCOMPLETE_OR_INVALID_CARD","LOW_GRAPHIC_CONFIDENCE"]

def test_native_layer_rejects_same_card_claimed_by_two_seats():
    backend=GraphicCardBackend(lambda _:{"table_region":{"x":0,"y":0,"w":1000,"h":600},"candidates":[{"rank":"A","rank_confidence":.99,"suit":"S","suit_confidence":.99,"box":{"x":490,"y":10,"w":20,"h":20}},{"rank":"A","rank_confidence":.99,"suit":"S","suit_confidence":.99,"box":{"x":490,"y":570,"w":20,"h":20}}]})
    with pytest.raises(NativeCardDetectorError,match="assigned to both"):NativeFourSeatCardDetector(backend)(Path("frame.png"))
