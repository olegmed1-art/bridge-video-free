#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Exact one-job operator for UV-DIANA11-DURABLE-003.
# It accepts only five fixed commands and no file ID, path, folder, profile,
# payload, shell text, retry count, or publication target from the caller.

readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly PYTHON="$BASE_DIR/.venv/bin/python"
readonly SPOOL="$BASE_DIR/spool"
readonly RUNTIME_ENV="$BASE_DIR/universal-video.env"
readonly SERVICE='universal-video.service'
readonly BRIDGE_JOB_ID='diana11-shadow-20260826-001'
readonly BRIDGE_JOB_HASH='a43e11beb0765aa91551d4c4a69767f02c4dcb3b5e485cd5bb0f2996e734d73d'
readonly DRIVE_FILE_ID='1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C'
readonly SOURCE_SIZE_BYTES='740292560'
readonly DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'
readonly EXPECTED_RUNTIME_COMMIT='6a4e8248eedd00f849fcefd1bf41a51b26f5e7c6'
readonly EXPECTED_WHISPER_MODEL='small'
readonly EXPECTED_PROCESSING_FINGERPRINT='371661d2a1858e576e2f618ddf504da724edc30089a9af88f9dd3a140ca30951'
readonly OAUTH_FILE="$BASE_DIR/secrets/google-drive-oauth.json"
readonly ROOT_STAGING='/opt/bridge-school/.universal-video-diana11-003-staging'
readonly PUBLISHED_DIR='/opt/bridge-school/.universal-video-diana11-003-published'
readonly RECEIPT_READER="$SOURCE_DIR/ops/universal_video_receipt_reader.py"

fail(){
  local code="$1"
  case "$code" in
    MUST_RUN_AS_ROOT|RUNTIME_LAYOUT|SPOOL_UNSAFE|SERVICE_INACTIVE|RUNTIME_COMMIT|RUNTIME_DIRTY|RUNTIME_ENV|SCHOOL_RUNTIME|ROOT_CONTROL_DIR|UNSUPPORTED_JOB|CONFORMANCE|PUBLICATION_RECEIPT|FRESH_ID_CONFLICT|STAGING|ENQUEUE_COLLISION|OAUTH|NOT_CONFORMANT|PUBLICATION|READBACK|USAGE|UNSUPPORTED_OPERATION) ;;
    *) code='RUNTIME_LAYOUT' ;;
  esac
  printf 'UV003_OPERATOR_FAILURE=%s\n' "$code" >&2
  exit 1
}

need_root(){ [[ $(id -u) -eq 0 ]] || fail MUST_RUN_AS_ROOT; }

verify_runtime_pin(){
  [[ -f "$RUNTIME_ENV" && ! -L "$RUNTIME_ENV" ]] || fail RUNTIME_ENV
  [[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$EXPECTED_RUNTIME_COMMIT" ]] || fail RUNTIME_COMMIT
  [[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all)" ]] || fail RUNTIME_DIRTY
  RUNTIME_ENV="$RUNTIME_ENV" EXPECTED_RUNTIME_COMMIT="$EXPECTED_RUNTIME_COMMIT" EXPECTED_WHISPER_MODEL="$EXPECTED_WHISPER_MODEL" EXPECTED_PROCESSING_FINGERPRINT="$EXPECTED_PROCESSING_FINGERPRINT" python3 - <<'PY' >/dev/null || exit 1
import hashlib,json,os
from pathlib import Path
values={}
for raw in Path(os.environ['RUNTIME_ENV']).read_text(encoding='utf-8').splitlines():
    line=raw.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key,value=line.split('=',1)
    if key in {'UNIVERSAL_VIDEO_SOURCE_COMMIT','UNIVERSAL_VIDEO_WHISPER_MODEL','WHISPER_MODEL'}:
        values[key]=value.strip()
revision=values.get('UNIVERSAL_VIDEO_SOURCE_COMMIT','')
model=(values.get('UNIVERSAL_VIDEO_WHISPER_MODEL','') or values.get('WHISPER_MODEL','') or 'small').strip()
assert revision == os.environ['EXPECTED_RUNTIME_COMMIT']
assert model == os.environ['EXPECTED_WHISPER_MODEL']
payload={'contract':'universal-video-v1','source_revision':revision,'whisper_model':model}
raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
assert hashlib.sha256(raw).hexdigest() == os.environ['EXPECTED_PROCESSING_FINGERPRINT']
PY
  [[ $? -eq 0 ]] || fail RUNTIME_ENV
}

verify_runtime(){
  [[ -d "$SOURCE_DIR/.git" && ! -L "$SOURCE_DIR" ]] || fail RUNTIME_LAYOUT
  bash "$SOURCE_DIR/ops/oracle_universal_video_spool_guard.sh" \
    verify "$BASE_DIR" root universal-video universal-video >/dev/null \
    || fail SPOOL_UNSAFE
  systemctl is-active --quiet "$SERVICE" || fail SERVICE_INACTIVE
  [[ -d "$SPOOL/inbox" && -d "$SPOOL/running" && -d "$SPOOL/done" && -d "$SPOOL/failed" && -d "$SPOOL/results" ]] || fail RUNTIME_LAYOUT
  [[ -x "$PYTHON" && -f "$SOURCE_DIR/universal_video/result_conformance.py" && -f "$SOURCE_DIR/universal_video/drive_results.py" && -f "$RECEIPT_READER" ]] || fail RUNTIME_LAYOUT
  id universal-video >/dev/null 2>&1 || fail RUNTIME_LAYOUT
  verify_runtime_pin
}

verify_school_runtime(){
  local service ready
  for service in assistant-lab.service assistant-lab-observer.service assistant-lab-control.service assistant-lab-control-bridge.service; do
    systemctl is-active --quiet "$service" || fail SCHOOL_RUNTIME
  done
  ready="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)" || fail SCHOOL_RUNTIME
  READY_JSON="$ready" python3 - <<'PY' >/dev/null || exit 1
import json,os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready'
assert x.get('engine') == 'DDS3'
assert x.get('fallback_used') is False
assert x.get('position_solver') == 'ready'
PY
  [[ $? -eq 0 ]] || fail SCHOOL_RUNTIME
}

verify_root_control_dir(){
  local path="$1"
  [[ -d "$path" && ! -L "$path" ]] || fail ROOT_CONTROL_DIR
  [[ "$(stat -c '%U:%G:%a' "$path")" == 'root:root:700' ]] || fail ROOT_CONTROL_DIR
}

spec_for(){
  case "$1" in
    "$BRIDGE_JOB_ID") printf '%s\n%s\n' bridge_lesson "$BRIDGE_JOB_HASH" ;;
    *) fail UNSUPPORTED_JOB ;;
  esac
}

conformance_json(){
  local job_id="$1" profile job_hash result_dir
  local -a spec
  mapfile -t spec < <(spec_for "$job_id")
  profile="${spec[0]}"; job_hash="${spec[1]}"; result_dir="$SPOOL/results/$job_id"
  runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 \
    "$PYTHON" -m universal_video.result_conformance \
      --job-dir "$result_dir" \
      --expected-job-id "$job_id" \
      --expected-profile "$profile" \
      --expected-job-hash "$job_hash" \
      --expected-source-file-id "$DRIVE_FILE_ID" \
      --evidence-phase GENERATION_FINALIZATION
}

publication_state(){
  local job_id="$1" current_artifact_set_sha256="${2:-}" receipt="$PUBLISHED_DIR/$1.json"
  verify_root_control_dir "$PUBLISHED_DIR"
  if [[ ! -e "$receipt" && ! -L "$receipt" ]]; then
    echo 'UV_PUBLICATION_STATE=NOT_PUBLISHED'
    return
  fi
  if [[ -L "$receipt" || ! -f "$receipt" ]]; then
    echo 'UV_PUBLICATION_STATE=FAILED'
    return
  fi
  python3 - "$receipt" "$job_id" "$current_artifact_set_sha256" <<'PY' 2>/dev/null || { echo 'UV_PUBLICATION_STATE=FAILED'; return; }
import json,sys
x=json.load(open(sys.argv[1], encoding='utf-8'))
p=x.get('publication') if isinstance(x.get('publication'),dict) else {}
c=x.get('conformance') if isinstance(x.get('conformance'),dict) else {}
assert x.get('job_id') == sys.argv[2]
assert p.get('status') == 'PUBLISHED_VERIFIED'
assert c.get('state') == 'PASS'
assert p.get('artifact_set_sha256') == c.get('artifact_set_sha256')
current=sys.argv[3]
if not current:
    print('UV_PUBLICATION_STATE=NOT_CURRENTLY_VERIFIABLE')
elif p.get('artifact_set_sha256') != current:
    print('UV_PUBLICATION_STATE=STALE_LOCAL_MISMATCH')
else:
    print('UV_PUBLICATION_STATE=PUBLISHED_VERIFIED')
print('UV_PUBLICATION_BUNDLE_SHA256='+str(p.get('artifact_set_sha256') or ''))
print('UV_PUBLICATION_FOLDER_ID='+str(p.get('child_folder_id') or ''))
PY
}

state_for(){
  local job_id="$1" job_file="${1}.json" d receipt inner report summary profile job_hash current_artifact_set_sha256
  local -a states=()
  local -a spec
  for d in done failed running inbox; do
    [[ -e "$SPOOL/$d/$job_file" || -L "$SPOOL/$d/$job_file" ]] && states+=("$d")
  done
  if (( ${#states[@]} == 0 )); then
    if [[ -e "$SPOOL/results/$job_id" || -L "$SPOOL/results/$job_id" ]]; then
      echo 'UV_STATE=CONFLICT'
      echo 'UV_ERROR_TYPE=ORPHAN_RESULT_DIRECTORY'
    else
      echo 'UV_STATE=MISSING'
    fi
    return 0
  fi
  if (( ${#states[@]} != 1 )); then
    echo 'UV_STATE=CONFLICT'
    echo 'UV_ERROR_TYPE=MULTIPLE_SPOOL_STATES'
    return 0
  fi
  d="${states[0]}"; receipt="$SPOOL/$d/$job_file"
  if [[ -L "$receipt" || ! -f "$receipt" ]]; then
    echo 'UV_STATE=NONCONFORMANT'
    echo 'UV_ERROR_TYPE=UNSAFE_SPOOL_RECEIPT'
    return 0
  fi
  case "$d" in
    failed)
      if ! summary="$(runuser -u universal-video -- /usr/bin/python3 "$RECEIPT_READER" inspect-failed "$receipt" "$job_file" 2>/dev/null)"; then
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=UNSAFE_FAILED_RECEIPT'
        return 0
      fi
      echo 'UV_STATE=FAILED'
      printf '%s\n' "$summary" | grep -E '^UV_ERROR_TYPE=[A-Za-z0-9_.-]{1,120}$' | head -n1 || echo 'UV_ERROR_TYPE=WORKER_FAILED'
      ;;
    running) echo 'UV_STATE=RUNNING' ;;
    inbox) echo 'UV_STATE=QUEUED' ;;
    done)
      mapfile -t spec < <(spec_for "$job_id")
      profile="${spec[0]}"; job_hash="${spec[1]}"
      if ! inner="$(runuser -u universal-video -- /usr/bin/python3 "$RECEIPT_READER" inspect-done "$receipt" "$job_id" "$profile" "$job_hash" "$DRIVE_FILE_ID" 2>/dev/null)"; then
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=DONE_RECEIPT_IDENTITY_MISMATCH'
        publication_state "$job_id"
        return 0
      fi
      echo "UV_RESULT_STATUS=$inner"
      if [[ "$inner" == REVIEW ]]; then
        echo 'UV_STATE=REVIEW'
        echo 'UV_TECHNICAL_BUNDLE_READY=NO'
        echo 'UV_BRIDGE_PRODUCTION_READY=NO'
        echo 'UV_PEDAGOGICAL_STATUS=NOT_EVALUATED'
        publication_state "$job_id"
        return 0
      fi
      if [[ "$inner" != COMPLETED ]]; then
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=UNEXPECTED_DONE_STATUS'
        publication_state "$job_id"
        return 0
      fi
      if ! report="$(conformance_json "$job_id" 2>/dev/null)"; then
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=RESULT_CONFORMANCE_FAILED'
        publication_state "$job_id"
        return 0
      fi
      REPORT_JSON="$report" EXPECTED_RUNTIME_COMMIT="$EXPECTED_RUNTIME_COMMIT" EXPECTED_WHISPER_MODEL="$EXPECTED_WHISPER_MODEL" EXPECTED_PROCESSING_FINGERPRINT="$EXPECTED_PROCESSING_FINGERPRINT" python3 - <<'PY' || exit 1
import json,os
x=json.loads(os.environ['REPORT_JSON'])
assert x.get('state') == 'PASS'
assert x.get('processing_revision') == os.environ['EXPECTED_RUNTIME_COMMIT']
assert x.get('processing_model') == os.environ['EXPECTED_WHISPER_MODEL']
print('UV_STATE=TECHNICAL_CONFORMANT')
print('UV_CONFORMANCE_STATE=PASS')
print('UV_ATTESTATION_MODE='+str(x.get('evidence_phase') or ''))
print('UV_ARTIFACT_SET_SHA256='+str(x.get('artifact_set_sha256') or ''))
print('UV_MANIFEST_SHA256='+str(x.get('manifest_sha256') or ''))
print('UV_ARTIFACT_COUNT='+str(x.get('artifact_count') or ''))
print('UV_TOTAL_BYTES='+str(x.get('total_bytes') or ''))
print('UV_DOMAIN_ANALYSIS_STATUS='+str(x.get('domain_analysis_status') or ''))
print('UV_TECHNICAL_BUNDLE_READY=YES')
print('UV_TRANSCRIPT_QC=PASS')
print('UV_PROCESSING_REVISION='+os.environ['EXPECTED_RUNTIME_COMMIT'])
print('UV_PROCESSING_WHISPER_MODEL='+os.environ['EXPECTED_WHISPER_MODEL'])
print('UV_PROCESSING_FINGERPRINT='+os.environ['EXPECTED_PROCESSING_FINGERPRINT'])
print('UV_BRIDGE_PRODUCTION_READY=NO')
print('UV_PEDAGOGICAL_STATUS=NOT_EVALUATED')
PY
      current_artifact_set_sha256="$(REPORT_JSON="$report" python3 - <<'PY'
import json,os
print(str(json.loads(os.environ['REPORT_JSON']).get('artifact_set_sha256') or ''))
PY
)"
      publication_state "$job_id" "$current_artifact_set_sha256"
      ;;
  esac
}

submit_for(){
  local job_id="$1" profile="$2" purpose="$3" job_file="${1}.json" tmp current
  verify_runtime
  verify_school_runtime
  current="$(state_for "$job_id" | sed -n 's/^UV_STATE=//p' | head -n1)"
  if [[ "$current" != MISSING ]]; then
    state_for "$job_id"
    echo 'UNIVERSAL_VIDEO_DIANA11_003_SUBMIT_IDEMPOTENT'
    return 0
  fi
  verify_root_control_dir "$PUBLISHED_DIR"
  if [[ -e "$PUBLISHED_DIR/$job_id.json" || -L "$PUBLISHED_DIR/$job_id.json" ]]; then
    echo 'UV_STATE=CONFLICT'
    echo 'UV_ERROR_TYPE=FRESH_ID_PUBLICATION_CONFLICT'
    return 0
  fi
  verify_root_control_dir "$ROOT_STAGING"
  [[ "$(stat -c '%d' "$ROOT_STAGING")" == "$(stat -c '%d' "$SPOOL/inbox")" ]] || fail STAGING
  tmp="$(mktemp -p "$ROOT_STAGING" "$job_file.XXXXXXXX.tmp")"
  trap 'rm -f "${tmp:-}"' EXIT
  JOB_ID="$job_id" PROFILE="$profile" PURPOSE="$purpose" TMP="$tmp" EXPECTED_JOB_HASH="$BRIDGE_JOB_HASH" python3 - <<'PY' || exit 1
import hashlib,json,os
payload={
  'job_id':os.environ['JOB_ID'],
  'profile':os.environ['PROFILE'],
  'source':{'kind':'google_drive','file_id':'1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C','name':'Диана 11'},
  'project':'Школа спортивного бриджа',
  'metadata':{'purpose':os.environ['PURPOSE'],'human_requested':True},
  'options':{'chunk_seconds':600,'max_source_bytes':2147483648,'max_duration_seconds':43200.0},
}
canonical={'contract':'universal-video-v1',**payload}
raw=json.dumps(canonical,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
assert hashlib.sha256(raw).hexdigest() == os.environ['EXPECTED_JOB_HASH']
with open(os.environ['TMP'],'w',encoding='utf-8') as handle:
    json.dump(payload,handle,ensure_ascii=False,indent=2)
    handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
PY
  [[ $? -eq 0 ]] || fail FRESH_ID_CONFLICT
  chown root:universal-video "$tmp"
  chmod 0640 "$tmp"
  if ! ln "$tmp" "$SPOOL/inbox/$job_file" 2>/dev/null; then
    rm -f "$tmp"
    fail ENQUEUE_COLLISION
  fi
  rm -f "$tmp"
  tmp=''
  trap - EXIT
  python3 - "$SPOOL/inbox" <<'PY'
import os,sys
fd=os.open(sys.argv[1], os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
try: os.fsync(fd)
finally: os.close(fd)
PY
  echo 'UV_STATE=QUEUED'
  echo 'UV_SUBMIT_COUNT=1'
  echo 'UV_AUTOMATIC_RETRIES=0'
  echo 'UNIVERSAL_VIDEO_DIANA11_003_SUBMIT_PASS'
}

publish_bridge(){
  verify_runtime
  verify_school_runtime
  [[ -f "$OAUTH_FILE" && ! -L "$OAUTH_FILE" ]] || fail OAUTH
  local current conformance publication work receipt artifact_set_sha256
  current="$(state_for "$BRIDGE_JOB_ID" | sed -n 's/^UV_STATE=//p' | head -n1)"
  [[ "$current" == TECHNICAL_CONFORMANT ]] || fail NOT_CONFORMANT
  work="$(mktemp -d -t diana11-003-publish.XXXXXX)"
  trap 'rm -rf "${work:-}"' EXIT INT TERM
  conformance="$work/conformance.json"
  publication="$work/publication.json"
  conformance_json "$BRIDGE_JOB_ID" > "$conformance" || fail CONFORMANCE
  artifact_set_sha256="$(python3 - "$conformance" "$EXPECTED_RUNTIME_COMMIT" "$EXPECTED_WHISPER_MODEL" <<'PY'
import json,sys
x=json.load(open(sys.argv[1], encoding='utf-8'))
assert x.get('state') == 'PASS'
assert x.get('processing_revision') == sys.argv[2]
assert x.get('processing_model') == sys.argv[3]
value=str(x.get('artifact_set_sha256') or '')
assert len(value) == 64 and all(ch in '0123456789abcdef' for ch in value)
print(value)
PY
)" || fail CONFORMANCE
  runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 GOOGLE_DRIVE_OAUTH_JSON_FILE="$OAUTH_FILE" \
    "$PYTHON" -m universal_video.drive_results publish \
      --folder-id "$DRIVE_RESULTS_FOLDER_ID" \
      --job-dir "$SPOOL/results/$BRIDGE_JOB_ID" \
      --expected-job-id "$BRIDGE_JOB_ID" \
      --expected-profile bridge_lesson \
      --expected-job-hash "$BRIDGE_JOB_HASH" \
      --expected-source-file-id "$DRIVE_FILE_ID" \
      --expected-artifact-set-sha256 "$artifact_set_sha256" > "$publication" || fail PUBLICATION
  verify_root_control_dir "$PUBLISHED_DIR"
  receipt="$PUBLISHED_DIR/$BRIDGE_JOB_ID.json"
  python3 - "$conformance" "$publication" "$receipt" "$BRIDGE_JOB_ID" "$EXPECTED_RUNTIME_COMMIT" "$EXPECTED_WHISPER_MODEL" <<'PY' || exit 1
import json,os,sys,tempfile
conformance=json.load(open(sys.argv[1], encoding='utf-8'))
publication=json.load(open(sys.argv[2], encoding='utf-8'))
assert conformance.get('state') == 'PASS'
assert conformance.get('evidence_phase') == 'GENERATION_FINALIZATION'
assert conformance.get('processing_revision') == sys.argv[5]
assert conformance.get('processing_model') == sys.argv[6]
assert publication.get('status') == 'PUBLISHED_VERIFIED'
assert publication.get('artifact_set_sha256') == conformance.get('artifact_set_sha256')
assert publication.get('manifest_sha256') == conformance.get('manifest_sha256')
assert publication.get('raw_media_included') is False
assert conformance.get('domain_analysis_status') == 'DEFERRED'
assert conformance.get('pedagogical_status') == 'NOT_EVALUATED'
assert publication.get('domain_analysis_status') == conformance.get('domain_analysis_status')
assert publication.get('pedagogical_status') == conformance.get('pedagogical_status')
payload={
  'schema':'universal-video-delivery-receipt-v1',
  'job_id':sys.argv[4],
  'execution_terminal':'COMPLETED',
  'technical_artifact':'VERIFIED',
  'publication_state':'REMOTE_VERIFIED',
  'domain_analysis':conformance['domain_analysis_status'],
  'methodology':conformance['pedagogical_status'],
  'production_promotion':'BLOCKED',
  'attestation_mode':'GENERATION_FINALIZATION',
  'conformance':conformance,
  'publication':publication,
}
directory=os.path.dirname(sys.argv[3])
fd,temp=tempfile.mkstemp(prefix='.delivery.',suffix='.tmp',dir=directory,text=True)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as handle:
        json.dump(payload,handle,ensure_ascii=False,indent=2)
        handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
    os.chmod(temp,0o640)
    os.replace(temp,sys.argv[3])
    dfd=os.open(directory,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    try: os.fsync(dfd)
    finally: os.close(dfd)
finally:
    if os.path.exists(temp): os.unlink(temp)
PY
  [[ $? -eq 0 ]] || fail PUBLICATION_RECEIPT
  chown root:universal-video "$receipt"
  verify_school_runtime
  publication_state "$BRIDGE_JOB_ID" "$artifact_set_sha256"
  echo 'UV_RAW_MEDIA_PUBLISHED=NO'
  echo 'UV_DOMAIN_ANALYSIS_STATUS=DEFERRED'
  echo 'UV_BRIDGE_PRODUCTION_READY=NO'
  echo 'UV_PEDAGOGICAL_STATUS=NOT_EVALUATED'
  echo 'UNIVERSAL_VIDEO_DIANA11_003_PUBLISH_VERIFIED_PASS'
  rm -rf "$work"
  trap - EXIT INT TERM
}

readback_bridge(){
  verify_runtime
  verify_school_runtime
  [[ -f "$OAUTH_FILE" && ! -L "$OAUTH_FILE" ]] || fail OAUTH
  verify_root_control_dir "$PUBLISHED_DIR"
  local receipt="$PUBLISHED_DIR/$BRIDGE_JOB_ID.json" work conformance readback
  [[ -f "$receipt" && ! -L "$receipt" ]] || fail PUBLICATION_RECEIPT
  [[ "$(stat -c '%U:%G:%a' "$receipt")" == 'root:universal-video:640' ]] || fail PUBLICATION_RECEIPT
  [[ "$(state_for "$BRIDGE_JOB_ID" | sed -n 's/^UV_STATE=//p' | head -n1)" == TECHNICAL_CONFORMANT ]] || fail NOT_CONFORMANT
  work="$(mktemp -d -t diana11-003-readback.XXXXXX)"
  trap 'rm -rf "${work:-}"' EXIT INT TERM
  conformance="$work/conformance.json"
  readback="$work/readback.json"
  conformance_json "$BRIDGE_JOB_ID" > "$conformance" || fail CONFORMANCE
  runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 GOOGLE_DRIVE_OAUTH_JSON_FILE="$OAUTH_FILE" \
    "$PYTHON" - "$receipt" "$conformance" "$DRIVE_RESULTS_FOLDER_ID" <<'PY' > "$readback" || exit 1
import json,sys
import requests
from universal_video.drive_adapter import DRIVE, access_token
receipt=json.load(open(sys.argv[1], encoding='utf-8'))
local=json.load(open(sys.argv[2], encoding='utf-8'))
parent_id=sys.argv[3]
publication=receipt.get('publication') if isinstance(receipt.get('publication'),dict) else {}
recorded=receipt.get('conformance') if isinstance(receipt.get('conformance'),dict) else {}
assert receipt.get('schema') == 'universal-video-delivery-receipt-v1'
assert receipt.get('job_id') == 'diana11-shadow-20260826-001'
assert receipt.get('publication_state') == 'REMOTE_VERIFIED'
assert publication.get('status') == 'PUBLISHED_VERIFIED'
assert recorded.get('state') == 'PASS' and local.get('state') == 'PASS'
assert recorded.get('artifact_set_sha256') == local.get('artifact_set_sha256') == publication.get('artifact_set_sha256')
assert recorded.get('manifest_sha256') == local.get('manifest_sha256') == publication.get('manifest_sha256')
child_id=str(publication.get('child_folder_id') or '')
assert child_id
expected_rows=publication.get('remote_artifacts')
assert isinstance(expected_rows,list) and expected_rows
expected={str(row['relative_name']).replace('/','__'):row for row in expected_rows}
assert len(expected) == len(expected_rows)
token=access_token()
headers={'Authorization':f'Bearer {token}'}
def get_file(file_id,fields):
    response=requests.get(f'{DRIVE}/files/{file_id}',headers=headers,params={'fields':fields,'supportsAllDrives':True},timeout=30)
    response.raise_for_status(); return response.json()
def verify_acl(data):
    permissions=data.get('permissions')
    assert isinstance(permissions,list) and permissions
    assert not any(isinstance(item,dict) and str(item.get('type') or '') in {'anyone','domain'} for item in permissions)
parent=get_file(parent_id,'id,mimeType,trashed,permissions(id,type,role)')
assert parent.get('mimeType') == 'application/vnd.google-apps.folder' and not parent.get('trashed')
verify_acl(parent)
child=get_file(child_id,'id,mimeType,trashed,parents,permissions(id,type,role)')
assert child.get('mimeType') == 'application/vnd.google-apps.folder' and not child.get('trashed')
assert parent_id in (child.get('parents') or [])
verify_acl(child)
query=f"'{parent_id.replace(chr(39), chr(92)+chr(39))}' in parents and trashed = false"
# Inventory is intentionally read from the fixed child, not the destination root.
query=f"'{child_id.replace(chr(39), chr(92)+chr(39))}' in parents and trashed = false"
response=requests.get(f'{DRIVE}/files',headers=headers,params={'q':query,'fields':'nextPageToken,files(id,name,size,md5Checksum,appProperties,permissions(id,type,role),trashed)','pageSize':1000,'supportsAllDrives':True,'includeItemsFromAllDrives':True},timeout=30)
response.raise_for_status(); payload=response.json()
assert not payload.get('nextPageToken')
files=payload.get('files') or []
by_name={str(item.get('name') or ''):item for item in files}
assert len(by_name) == len(files)
assert set(by_name) == set(expected)
total=0
for name,row in expected.items():
    remote=by_name[name]
    assert str(remote.get('id') or '') == str(row.get('file_id') or '')
    assert int(remote.get('size')) == int(row.get('size_bytes'))
    assert str(remote.get('md5Checksum') or '').lower() == str(row.get('md5') or '').lower()
    properties=remote.get('appProperties') if isinstance(remote.get('appProperties'),dict) else {}
    assert str(properties.get('sha256') or '').lower() == str(row.get('sha256') or '').lower()
    assert not remote.get('trashed')
    verify_acl(remote)
    total += int(row.get('size_bytes'))
print(json.dumps({'status':'REMOTE_VERIFIED','artifact_count':len(expected),'total_bytes':total,'artifact_set_sha256':publication['artifact_set_sha256'],'child_folder_id':child_id,'broad_acl':False},sort_keys=True))
PY
  [[ $? -eq 0 ]] || fail READBACK
  python3 - "$readback" <<'PY' || exit 1
import json,sys
x=json.load(open(sys.argv[1], encoding='utf-8'))
assert x.get('status') == 'REMOTE_VERIFIED'
assert x.get('broad_acl') is False
print('UV_READBACK_STATE=REMOTE_VERIFIED')
print('UV_READBACK_ARTIFACT_COUNT='+str(x.get('artifact_count') or ''))
print('UV_READBACK_TOTAL_BYTES='+str(x.get('total_bytes') or ''))
print('UV_READBACK_BUNDLE_SHA256='+str(x.get('artifact_set_sha256') or ''))
print('UV_READBACK_FOLDER_ID='+str(x.get('child_folder_id') or ''))
print('UV_READBACK_BROAD_ACL=NO')
PY
  [[ $? -eq 0 ]] || fail READBACK
  echo 'UNIVERSAL_VIDEO_DIANA11_003_READBACK_PASS'
  rm -rf "$work"
  trap - EXIT INT TERM
}

need_root
[[ $# -eq 1 ]] || fail USAGE
case "$1" in
  submit-bridge) submit_for "$BRIDGE_JOB_ID" bridge_lesson 'UV-DIANA11-DURABLE-003 fresh provenance shadow' ;;
  status-bridge) verify_runtime; state_for "$BRIDGE_JOB_ID"; echo 'UNIVERSAL_VIDEO_DIANA11_003_STATUS_PASS' ;;
  conform-bridge)
    verify_runtime
    state_for "$BRIDGE_JOB_ID"
    [[ "$(state_for "$BRIDGE_JOB_ID" | sed -n 's/^UV_STATE=//p' | head -n1)" == TECHNICAL_CONFORMANT ]] || fail NOT_CONFORMANT
    echo 'UNIVERSAL_VIDEO_DIANA11_003_CONFORMANCE_PASS'
    ;;
  publish-bridge) publish_bridge ;;
  readback-bridge) readback_bridge ;;
  *) fail UNSUPPORTED_OPERATION ;;
esac
