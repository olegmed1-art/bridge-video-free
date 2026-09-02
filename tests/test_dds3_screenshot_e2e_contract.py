import json
import stat

from bridge_school_api.dds3 import DDS3Config, compute

HANDS={
 "N":{"S":"K54","H":"7654","D":"875432","C":""},
 "E":{"S":"Q972","H":"AT82","D":"J","C":"KQT7"},
 "S":{"S":"8","H":"KQJ3","D":"AQT96","C":"943"},
 "W":{"S":"AJT63","H":"9","D":"K","C":"AJ8652"},
}
EXPECTED={"S":[3,10,3,10],"H":[9,3,9,3],"D":[10,2,10,2],"C":[2,11,2,11],"NT":[5,7,5,7]}

def _fake(tmp_path):
 p=tmp_path/'dds'; payload={"hand_order":["N","E","S","W"],"strain_order":["S","H","D","C","NT"],"dd_table":EXPECTED,"par_score_ns":-100,"par_contracts":["5D*-NS-1"]}
 p.write_text('#!/bin/sh\necho '+"'"+json.dumps(payload)+"'\n"); p.chmod(p.stat().st_mode|stat.S_IXUSR); return DDS3Config(executable=str(p))

def test_board16_screenshot_contract_to_dds3(tmp_path):
 request={"operation":"dd_table","screenshot_observation":{"board_number":{"value":16,"confidence":.99},"hands":HANDS,"extra_metadata":{"source_ui":{"value":"bridge_diagram","confidence":.95}}}}
 r=compute(request,config=_fake(tmp_path))
 assert r['board']['board_number']==16
 assert r['board']['dealer']['value']=='W'
 assert r['board']['vulnerability']['value']=='EW'
 assert r['board']['recognition']['cards_complete'] is True
 assert r['dd_table']==EXPECTED
 assert r['engine']=='DDS3' and r['fallback_used'] is False
