"""Generate a fixed SSH program for the reviewed single-pilot controller.

No host action occurs here. Inputs are canonical accepted bytes, never Python
or shell fragments. Cleanup deliberately does not require a still-live window.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from ops import light_native_pilot_release as release

require = release.require


def program(package_raw, source, accepted_package, action, payload, accepted_payload):
    require(action in ('baseline','launch','prepare-retained','launch-retained','observe','restore'), 'PILOT_CONTROL_ACTION')
    require(release.source.identifier(source,40)
            and release.source.identifier(accepted_package,64)
            and hashlib.sha256(package_raw).hexdigest()==accepted_package,
            'PILOT_CONTROL_PACKAGE')
    require(type(payload) is bytes and 0<len(payload)<=262144
            and release.source.identifier(accepted_payload,64)
            and hashlib.sha256(payload).hexdigest()==accepted_payload,
            'PILOT_CONTROL_PAYLOAD')
    value=json.loads(payload)
    require(type(value) is dict and release.encoded(value)==payload,'PILOT_CONTROL_CANONICAL')
    if action=='baseline':
        require(set(value)=={'agreement','accepted_agreement_sha256','scope'},'PILOT_CONTROL_BASELINE')
    elif action=='prepare-retained':
        require(set(value)=={'request','accepted_permit_sha256'}
                and type(value['request']) is dict
                and 'permit_b64' not in value['request']
                and value['request'].get('source')==source
                and value['request'].get('package_sha256')==accepted_package
                and value['request'].get('permit_sha256')==value['accepted_permit_sha256']
                and release.source.identifier(value['accepted_permit_sha256'],64),
                'PILOT_CONTROL_RETAINED_REQUEST')
    elif action=='launch':
        require(value.get('source')==source and value.get('package_sha256')==accepted_package,
                'PILOT_CONTROL_REQUEST')
    else:
        require(set(value)=={'request_sha256'}
                and release.source.identifier(value['request_sha256'],64),'PILOT_CONTROL_REQUEST')
    # The isolated bootstrap checks all bytes before importing any supplied
    # helper. No SSH/session lifetime is inherited by the transient supervisor.
    return '''import base64,hashlib,json,os,pathlib,sys,tempfile
try:
 package_raw=base64.b64decode(%r,validate=True)
 assert hashlib.sha256(package_raw).hexdigest()==%r
 package=json.loads(package_raw)
 assert package['source']==%r and package['version']==1
 assert set(package['helpers'])==set(%r)
 payload=base64.b64decode(%r,validate=True)
 assert hashlib.sha256(payload).hexdigest()==%r
 value=json.loads(payload)
 assert os.geteuid()==0
 with tempfile.TemporaryDirectory(prefix='light-pilot-control-',dir='/var/tmp') as temp:
  root=pathlib.Path(temp)
  (root/'ops').mkdir(mode=0o700)
  for name,data in package['helpers'].items():
   fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as stream: stream.write(data)
  sys.path.insert(0,str(root))
  from ops import light_native_service_controller as controller
  action=%r
  if action=='baseline':
   result=controller.prepare_baseline(%r,%r,package_raw,value['agreement'],value['accepted_agreement_sha256'],value['scope'])
  elif action=='prepare-retained':
   permit=controller.read(controller.plan.ROOT/'intake'/'permit.json',262144)
   assert controller.digest(permit)==value['accepted_permit_sha256']
   request=dict(value['request'],permit_b64=base64.b64encode(permit).decode())
   request_raw=json.dumps(request,sort_keys=True,separators=(',',':')).encode()
   request_sha=controller.digest(request_raw)
   controller.prepare(request_raw,request_sha,package_raw)
   result={'audit':'LIGHT_NATIVE_REQUEST_PREPARED','request_sha256':request_sha,'pilot_submitted':False}
  elif action=='launch-retained':
   result=controller.launch(value['request_sha256'])
  elif action=='launch':
   controller.prepare(payload,%r,package_raw)
   result=controller.launch(%r)
  elif action=='restore':
   result=controller.restore(value['request_sha256'])
  else:
   request,prior,protected,protected_digest,directory=controller.ledger(value['request_sha256'])
   receipt=controller.restored_receipt(request,prior,protected,protected_digest,directory)
   assert receipt is not None
   result={'audit':'LIGHT_NATIVE_HOLD_RESTORE_READBACK','request_sha256':value['request_sha256'],'terminal_verified':False}
  print(json.dumps(result,sort_keys=True))
except BaseException:
 print('{"audit":"LIGHT_NATIVE_CONTROL_REFUSED"}')
 sys.exit(2)
''' % (base64.b64encode(package_raw).decode(),accepted_package,source,release.HELPERS,
       base64.b64encode(payload).decode(),accepted_payload,action,source,accepted_package,
       accepted_payload,accepted_payload)


def main():
    source=os.environ['PILOT_SOURCE']
    package=release.package(Path.cwd(),source)
    payload=base64.b64decode(os.environ['PILOT_PAYLOAD_BASE64'],validate=True)
    print(program(package,source,os.environ['PILOT_ACCEPTED_PACKAGE'],
                  os.environ['PILOT_ACTION'],payload,os.environ['PILOT_ACCEPTED_PAYLOAD']))


if __name__=='__main__':
    main()
