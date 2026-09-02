from bridge_school_api.dds3 import ObservedField, ScreenshotDealObservation, derive_board_metadata
from tests.test_dds3_deal_model import HANDS

def test_board16_metadata_cycle():
    assert derive_board_metadata(16)==('W','EW')

def test_board17_restarts_cycle_with_north_none():
    assert derive_board_metadata(17)==('N','None')

def test_screenshot_can_derive_missing_metadata():
    deal,p=ScreenshotDealObservation(HANDS,board_number=ObservedField(16,.99)).canonicalize()
    assert deal.dealer=='W' and deal.vulnerability=='EW'
    assert p['dealer']['derived_from_board'] is True
    assert p['recognition']['cards_complete'] is True

def test_observed_metadata_is_preserved_and_conflict_reported():
    deal,p=ScreenshotDealObservation(HANDS,board_number=ObservedField(16),dealer=ObservedField('N'),vulnerability=ObservedField('NS')).canonicalize()
    assert deal.dealer=='N' and deal.vulnerability=='NS'
    assert len(p['warnings'])==2
    assert p['recognition']['metadata_status']=='validated_with_warnings'
