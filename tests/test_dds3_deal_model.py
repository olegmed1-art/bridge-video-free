import json
import stat

import pytest

from bridge_school_api.dds3 import BridgeDeal, DDS3Config, DealValidationError, compute, solve_deal

HANDS={
 "N":{"S":"K54","H":"7654","D":"875432","C":""},
 "E":{"S":"Q972","H":"AT82","D":"J","C":"KQT7"},
 "S":{"S":"8","H":"KQJ3","D":"AQT96","C":"943"},
 "W":{"S":"AJT63","H":"9","D":"K","C":"AJ8652"},
}

def fake(tmp_path):
 p=tmp_path/'dds'; payload={"hand_order":["N","E","S","W"],"strain_order":["S","H","D","C","NT"],"dd_table":{"S":[3,10,3,10],"H":[9,3,9,3],"D":[10,2,10,2],"C":[2,11,2,11],"NT":[5,7,5,7]},"par_score_ns":-100,"par_contracts":["5D*-NS-1"]}
 p.write_text('#!/bin/sh\necho '+"'"+json.dumps(payload)+"'\n"); p.chmod(p.stat().st_mode|stat.S_IXUSR); return DDS3Config(executable=str(p))

def test_complete_deal_to_pbn():
 d=BridgeDeal(HANDS); assert d.to_pbn()=="N:K54.7654.875432. Q972.AT82.J.KQT7 8.KQJ3.AQT96.943 AJT63.9.K.AJ8652"

def test_duplicate_card_rejected():
 bad={s:{u:r for u,r in h.items()} for s,h in HANDS.items()}; bad['N']['S']='A54'
 with pytest.raises(DealValidationError): BridgeDeal(bad).validate()

def test_autonomous_deal_path(tmp_path):
 r=solve_deal(BridgeDeal(HANDS),config=fake(tmp_path)); assert r['engine']=='DDS3' and r['input_validated'] is True and r['fallback_used'] is False

def test_embedding_compute_path(tmp_path):
 r=compute({'operation':'dd_table','deal':{'hands':HANDS}},config=fake(tmp_path)); assert r['dd_table']['S']==[3,10,3,10]
