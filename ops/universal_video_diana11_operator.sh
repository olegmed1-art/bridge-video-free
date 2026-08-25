#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Exact, two-job root helper for Diana 11.
# No arbitrary file id, path, folder, shell, profile, command, or payload is accepted.

readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly PYTHON="$BASE_DIR/.venv/bin/python"
readonly SPOOL="$BASE_DIR/spool"
readonly TRANSCRIPT_JOB_ID='diana11-transcript-20260825-01'
readonly TRANSCRIPT_JOB_HASH='a7253067339ca1ed366279d55584d9eaec5e4cd8d2e0e04c57b9c3b69392785b'
readonly BRIDGE_JOB_ID='diana11-bridge-20260825-01'
readonly BRIDGE_JOB_HASH='c80f34c4018c0861c5ba85d9ab0efac63e84027eca755783d15206d416f2d7f6'
readonly DRIVE_FILE_ID='1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C'
readonly DRIVE_RESULTS_FOLDER_ID='1I8cSuA-p0MpaZIbA33slks19KyvfJDMK'
readonly OAUTH_FILE="$BASE_DIR/secrets/google-drive-oauth.json"
readonly ROOT_STAGING='/opt/bridge-school/.universal-video-diana11-staging'
readonly PUBLISHED_DIR='/opt/bridge-school/.universal-video-diana11-published'
readonly RECEIPT_READER="$SOURCE_DIR/ops/universal_video_receipt_reader.py"

fail(){ echo "ERROR: $*" >&2; exit 1; }
need_root(){ [[ $(id -u) -eq 0 ]] || fail 'must run as root'; }
verify_runtime(){
  bash "$SOURCE_DIR/ops/oracle_universal_video_spool_guard.sh" \
    verify "$BASE_DIR" root universal-video universal-video >/dev/null \
    || fail 'unsafe Universal Video spool layout'
  systemctl is-active --quiet universal-video.service || fail 'universal-video.service inactive'
  [[ -d "$SPOOL/inbox" && -d "$SPOOL/running" && -d "$SPOOL/done" && -d "$SPOOL/failed" && -d "$SPOOL/results" ]] || fail 'spool missing'
  [[ -x "$PYTHON" && -f "$SOURCE_DIR/universal_video/result_conformance.py" && -f "$RECEIPT_READER" ]] || fail 'conformance runtime missing'
  id universal-video >/dev/null 2>&1 || fail 'universal-video user missing'
}

verify_school_runtime(){
  local service
  for service in assistant-lab.service assistant-lab-observer.service assistant-lab-control.service assistant-lab-control-bridge.service; do
    systemctl is-active --quiet "$service" || fail "$service inactive"
  done
  local ready
  ready="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
  READY_JSON="$ready" python3 - <<'PY'
import json,os
x=json.loads(os.environ['READY_JSON'])
assert x.get('status') == 'ready'
assert x.get('engine') == 'DDS3'
assert x.get('fallback_used') is False
assert x.get('position_solver') == 'ready'
PY
}

verify_root_control_dir(){
  local path="$1"
  [[ -d "$path" && ! -L "$path" ]] || fail "unsafe root control directory: $path"
  [[ "$(stat -c '%U:%G:%a' "$path")" == 'root:root:700' ]] || fail "unsafe root control ownership/mode: $path"
}

spec_for(){
  case "$1" in
    "$TRANSCRIPT_JOB_ID") printf '%s\n%s\n' transcript_only "$TRANSCRIPT_JOB_HASH" ;;
    "$BRIDGE_JOB_ID") printf '%s\n%s\n' bridge_lesson "$BRIDGE_JOB_HASH" ;;
    *) return 64 ;;
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
      --evidence-phase POST_HOC_OBSERVATION
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
    echo 'UV_STATE=MISSING'
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
      if ! summary="$(runuser -u universal-video -- /usr/bin/python3 "$RECEIPT_READER" \
        inspect-failed "$receipt" "$job_file" 2>/dev/null)"; then
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=UNSAFE_FAILED_RECEIPT'
        return 0
      fi
      echo 'UV_STATE=FAILED'
      printf '%s\n' "$summary"
      ;;
    running) echo 'UV_STATE=RUNNING' ;;
    inbox) echo 'UV_STATE=QUEUED' ;;
    done)
      mapfile -t spec < <(spec_for "$job_id")
      profile="${spec[0]}"; job_hash="${spec[1]}"
      if ! inner="$(runuser -u universal-video -- /usr/bin/python3 "$RECEIPT_READER" \
        inspect-done "$receipt" "$job_id" "$profile" "$job_hash" "$DRIVE_FILE_ID" 2>/dev/null)"; then
        echo 'UV_STATE=NONCONFORMANT'
        echo 'UV_ERROR_TYPE=DONE_RECEIPT_IDENTITY_MISMATCH'
        publication_state "$job_id"
        return 0
      fi
      echo "UV_RESULT_STATUS=$inner"
      echo "UV_RESULT_DIR=$SPOOL/results/$job_id"
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
      REPORT_JSON="$report" python3 - <<'PY'
import json,os
x=json.loads(os.environ['REPORT_JSON'])
assert x.get('state') == 'PASS'
print('UV_STATE=TECHNICAL_CONFORMANT')
print('UV_CONFORMANCE_STATE=PASS')
print('UV_ATTESTATION_MODE='+str(x.get('evidence_phase') or ''))
print('UV_ARTIFACT_SET_SHA256='+str(x.get('artifact_set_sha256') or ''))
print('UV_MANIFEST_SHA256='+str(x.get('manifest_sha256') or ''))
print('UV_ARTIFACT_COUNT='+str(x.get('artifact_count') or ''))
print('UV_TOTAL_BYTES='+str(x.get('total_bytes') or ''))
print('UV_DOMAIN_ANALYSIS_STATUS='+str(x.get('domain_analysis_status') or ''))
print('UV_TECHNICAL_BUNDLE_READY=YES')
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
  local job_id="$1" profile="$2" purpose="$3" job_file="${1}.json" tmp
  verify_runtime
  local current
  current="$(state_for "$job_id" | sed -n 's/^UV_STATE=//p' | head -n1)"
  if [[ "$current" != MISSING ]]; then
    state_for "$job_id"
    echo 'UNIVERSAL_VIDEO_DIANA11_SUBMIT_IDEMPOTENT'
    return 0
  fi
  verify_root_control_dir "$ROOT_STAGING"
  [[ "$(stat -c '%d' "$ROOT_STAGING")" == "$(stat -c '%d' "$SPOOL/inbox")" ]] \
    || fail 'root staging and inbox must share a filesystem'
  tmp="$(mktemp -p "$ROOT_STAGING" "$job_file.XXXXXXXX.tmp")"
  trap 'rm -f "${tmp:-}"' EXIT
  JOB_ID="$job_id" PROFILE="$profile" PURPOSE="$purpose" TMP="$tmp" python3 - <<'PY'
import json,os
payload={
  'job_id':os.environ['JOB_ID'],
  'profile':os.environ['PROFILE'],
  'source':{'kind':'google_drive','file_id':'1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C','name':'Диана 11'},
  'project':'Школа спортивного бриджа',
  'metadata':{'purpose':os.environ['PURPOSE'],'human_requested':True},
  'options':{'chunk_seconds':600,'max_source_bytes':2147483648,'max_duration_seconds':43200},
}
with open(os.environ['TMP'],'w',encoding='utf-8') as f:
    json.dump(payload,f,ensure_ascii=False,indent=2)
    f.write('\n')
PY
  chown root:universal-video "$tmp"
  chmod 0640 "$tmp"
  if ! ln "$tmp" "$SPOOL/inbox/$job_file" 2>/dev/null; then
    rm -f "$tmp"
    fail 'enqueue collision'
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
  echo 'UNIVERSAL_VIDEO_DIANA11_SUBMIT_PASS'
}

publish_bridge(){
  verify_runtime
  verify_school_runtime
  [[ -f "$OAUTH_FILE" && ! -L "$OAUTH_FILE" ]] || fail 'protected Drive OAuth file missing'
  local current conformance publication work published_dir receipt artifact_set_sha256
  current="$(state_for "$BRIDGE_JOB_ID" | sed -n 's/^UV_STATE=//p' | head -n1)"
  [[ "$current" == TECHNICAL_CONFORMANT ]] || fail "bridge result is not technical conformant: $current"
  work="$(mktemp -d -t diana11-publish.XXXXXX)"
  trap 'rm -rf "${work:-}"' EXIT INT TERM
  conformance="$work/conformance.json"
  publication="$work/publication.json"
  conformance_json "$BRIDGE_JOB_ID" > "$conformance"
  artifact_set_sha256="$(python3 - "$conformance" <<'PY'
import json,sys
x=json.load(open(sys.argv[1], encoding='utf-8'))
assert x.get('state') == 'PASS'
value=str(x.get('artifact_set_sha256') or '')
assert len(value) == 64 and all(ch in '0123456789abcdef' for ch in value)
print(value)
PY
)"
  runuser -u universal-video -- env PYTHONPATH="$SOURCE_DIR" PYTHONDONTWRITEBYTECODE=1 \
    GOOGLE_DRIVE_OAUTH_JSON_FILE="$OAUTH_FILE" \
    "$PYTHON" -m universal_video.drive_results publish \
      --folder-id "$DRIVE_RESULTS_FOLDER_ID" \
      --job-dir "$SPOOL/results/$BRIDGE_JOB_ID" \
      --expected-job-id "$BRIDGE_JOB_ID" \
      --expected-profile bridge_lesson \
      --expected-job-hash "$BRIDGE_JOB_HASH" \
      --expected-source-file-id "$DRIVE_FILE_ID" \
      --expected-artifact-set-sha256 "$artifact_set_sha256" > "$publication"
  verify_root_control_dir "$PUBLISHED_DIR"
  published_dir="$PUBLISHED_DIR"
  receipt="$published_dir/$BRIDGE_JOB_ID.json"
  python3 - "$conformance" "$publication" "$receipt" "$BRIDGE_JOB_ID" <<'PY'
import json,os,sys,tempfile
conformance=json.load(open(sys.argv[1], encoding='utf-8'))
publication=json.load(open(sys.argv[2], encoding='utf-8'))
assert conformance.get('state') == 'PASS'
assert conformance.get('evidence_phase') == 'POST_HOC_OBSERVATION'
assert publication.get('status') == 'PUBLISHED_VERIFIED'
assert publication.get('artifact_set_sha256') == conformance.get('artifact_set_sha256')
assert publication.get('manifest_sha256') == conformance.get('manifest_sha256')
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
  'attestation_mode':'POST_HOC_OBSERVATION',
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
  chown root:universal-video "$receipt"
  verify_school_runtime
  publication_state "$BRIDGE_JOB_ID" "$artifact_set_sha256"
  echo 'UV_DOMAIN_ANALYSIS_STATUS=DEFERRED'
  echo 'UV_BRIDGE_PRODUCTION_READY=NO'
  echo 'UV_PEDAGOGICAL_STATUS=NOT_EVALUATED'
  echo 'UNIVERSAL_VIDEO_DIANA11_PUBLISH_VERIFIED_PASS'
  rm -rf "$work"
  trap - EXIT INT TERM
}

need_root
[[ $# -eq 1 ]] || fail 'usage: universal-video-diana11 submit|status|submit-bridge|status-bridge|conform-bridge|publish-bridge'
case "$1" in
  submit) submit_for "$TRANSCRIPT_JOB_ID" transcript_only 'test transcription' ;;
  status) verify_runtime; state_for "$TRANSCRIPT_JOB_ID"; echo 'UNIVERSAL_VIDEO_DIANA11_STATUS_PASS' ;;
  submit-bridge) submit_for "$BRIDGE_JOB_ID" bridge_lesson 'bridge-specific post-transcript processing' ;;
  status-bridge) verify_runtime; state_for "$BRIDGE_JOB_ID"; echo 'UNIVERSAL_VIDEO_DIANA11_BRIDGE_STATUS_PASS' ;;
  conform-bridge)
    verify_runtime
    state_for "$BRIDGE_JOB_ID"
    [[ "$(state_for "$BRIDGE_JOB_ID" | sed -n 's/^UV_STATE=//p' | head -n1)" == TECHNICAL_CONFORMANT ]] \
      || fail 'bridge result did not pass technical conformance'
    echo 'UNIVERSAL_VIDEO_DIANA11_CONFORMANCE_PASS'
    ;;
  publish-bridge) publish_bridge ;;
  *) fail 'unsupported operation' ;;
esac
