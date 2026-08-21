import json, stat
import pytest
from bridge_school_api.dds3 import DDS3Config, ImageEnvelope, ImageIngressError, ObservedField, ScreenshotDealObservation, solve_image_envelope
HANDS={"N":{"S":"K54","H":"7654","D":"875432","C":""},"E":{"S":"Q972","H":"AT82","D":"J","C":"KQT7"},"S":{"S":"8","H":"KQJ3","D":"AQT96","C":"943"},"W":{"S":"AJT63","H":"9","D":"K","C":"AJ8652"}}
def fake(tmp_path):
 p=tmp_path/'dds'; d={"hand_order":["N","E","S","W"],"strain_order":["S","H","D","C","NT"],"dd_table":{"S":[3,10,3,10],"H":[9,3,9,3],"D":[10,2,10,2],"C":[2,11,2,11],"NT":[5,7,5,7]},"par_score_ns":-100,"par_contracts":["5D*-NS-1"]}; p.write_text('#!/bin/sh\necho '+"'"+json.dumps(d)+"'\n");p.chmod(p.stat().st_mode|stat.S_IXUSR);return DDS3Config(executable=str(p))
def test_actual_jpeg_bytes_are_bound_to_result(tmp_path):
 image=b'\xff\xd8\xff'+b'board16-pixels'; obs=ScreenshotDealObservation(HANDS,board_number=ObservedField(16,.99)); r=solve_image_envelope(ImageEnvelope(image,obs,filename='board16.jpg'),config=fake(tmp_path)); assert r['image']['bytes']==len(image); assert len(r['image']['sha256'])==64; assert r['board']['dealer']['value']=='W'; assert r['board']['vulnerability']['value']=='EW'; assert r['pipeline'].endswith('DDS3'); assert r['fallback_used'] is False
def test_non_image_payload_is_rejected_before_dds(tmp_path):
 obs=ScreenshotDealObservation(HANDS,board_number=ObservedField(16));
 with pytest.raises(ImageIngressError): solve_image_envelope(ImageEnvelope(b'not an image',obs),config=fake(tmp_path))
