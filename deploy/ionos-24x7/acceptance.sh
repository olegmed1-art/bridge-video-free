#!/usr/bin/env bash
set -euo pipefail

: "${COMPUTE_BASE_URL:?set COMPUTE_BASE_URL, e.g. https://compute.example.com}"
: "${DDS3_RUNTIME_TOKEN:?set DDS3_RUNTIME_TOKEN in the trusted admin shell}"

python3 - <<'PY'
import os, ssl, urllib.request, json
base=os.environ['COMPUTE_BASE_URL'].rstrip('/')
with urllib.request.urlopen(base + '/readyz', timeout=10, context=ssl.create_default_context()) as r:
    d=json.load(r)
assert d['status']=='ready', d
assert d['engine']=='DDS3', d
assert d['fallback_used'] is False, d
print('READYZ_PASS', json.dumps(d, sort_keys=True))
PY

GOLDEN='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'
REQ=$(python3 - <<PY
import json
print(json.dumps({
  'operation':'dd_table',
  'pbn':'''$GOLDEN''',
  'dealer':'N',
  'vulnerability':'None'
}))
PY
)

curl -fsS \
  -H "Authorization: Bearer ${DDS3_RUNTIME_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "$REQ" \
  "${COMPUTE_BASE_URL%/}/v1/compute" > /tmp/ionos-dds3-golden.json

python3 - <<'PY'
import json
p='/tmp/ionos-dds3-golden.json'
d=json.load(open(p,encoding='utf-8'))
assert d['engine']=='DDS3', d
assert d['fallback_used'] is False, d
assert d['par_score_ns']==-110, d
assert d['dd_table']=={'S':[5,8,5,8],'H':[6,6,6,6],'D':[5,7,5,7],'C':[7,5,7,5],'NT':[6,6,6,6]}, d
print('DDS3_GOLDEN_PASS')
PY

POSITION=$(python3 - <<PY
import json
print(json.dumps({
  'operation':'position_all_moves',
  'position':{
    'pbn':'''$GOLDEN''',
    'trump':'NT',
    'first':'N',
    'current_trick':[]
  }
}))
PY
)

for n in 1 2; do
  curl -fsS \
    -H "Authorization: Bearer ${DDS3_RUNTIME_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "$POSITION" \
    "${COMPUTE_BASE_URL%/}/v1/compute" > "/tmp/ionos-dds3-position-${n}.json"
done

python3 - <<'PY'
import json
a=json.load(open('/tmp/ionos-dds3-position-1.json',encoding='utf-8'))
b=json.load(open('/tmp/ionos-dds3-position-2.json',encoding='utf-8'))
for d in (a,b):
    assert d['engine']=='DDS3' and d['fallback_used'] is False, d
    assert d['operation']=='position_all_moves', d
assert a['moves']==b['moves'], (a,b)
assert a['solver_context']['request_seq'] + 1 == b['solver_context']['request_seq'], (a,b)
assert b['solver_context']['same_tt_instance'] is True, b
assert b['solver_context']['tt_present_before'] is True, b
assert a['nodes'] > b['nodes'] >= 0, (a['nodes'], b['nodes'])
print('DDS3_TT_REUSE_PASS', a['nodes'], '->', b['nodes'])
PY

rm -f /tmp/ionos-dds3-golden.json /tmp/ionos-dds3-position-1.json /tmp/ionos-dds3-position-2.json

echo 'IONOS_24X7_ACCEPTANCE_CORE_PASS'
