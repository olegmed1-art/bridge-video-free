#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Exact, two-job root helper for Diana 11.
# No arbitrary file id, path, shell, profile, command, or payload is accepted.

readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SPOOL="$BASE_DIR/spool"
readonly TRANSCRIPT_JOB_ID='diana11-transcript-20260825-01'
readonly BRIDGE_JOB_ID='diana11-bridge-20260825-01'
readonly DRIVE_FILE_ID='1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C'

fail(){ echo "ERROR: $*" >&2; exit 1; }
need_root(){ [[ $(id -u) -eq 0 ]] || fail 'must run as root'; }
verify_runtime(){
  systemctl is-active --quiet universal-video.service || fail 'universal-video.service inactive'
  [[ -d "$SPOOL/inbox" && -d "$SPOOL/running" && -d "$SPOOL/done" && -d "$SPOOL/failed" ]] || fail 'spool missing'
  id universal-video >/dev/null 2>&1 || fail 'universal-video user missing'
}

state_for(){
  local job_id="$1" job_file="${1}.json" d
  for d in done failed running inbox; do
    if [[ -f "$SPOOL/$d/$job_file" && ! -L "$SPOOL/$d/$job_file" ]]; then
      case "$d" in
        done) echo 'UV_STATE=COMPLETED' ;;
        failed) echo 'UV_STATE=FAILED' ;;
        running) echo 'UV_STATE=RUNNING' ;;
        inbox) echo 'UV_STATE=QUEUED' ;;
      esac
      if [[ "$d" == failed ]]; then
        python3 - "$SPOOL/$d/$job_file" <<'PY'
import json,sys
x=json.load(open(sys.argv[1], encoding='utf-8'))
print('UV_ERROR_TYPE='+str(x.get('error_type') or '')[:120])
print('UV_ERROR='+str(x.get('error') or '').replace('\n',' ')[:500])
PY
      elif [[ "$d" == done ]]; then
        python3 - "$SPOOL/$d/$job_file" <<'PY'
import json,sys
x=json.load(open(sys.argv[1], encoding='utf-8'))
print('UV_RESULT_STATUS='+str(x.get('status') or ''))
print('UV_RESULT_DIR='+str(x.get('result_dir') or x.get('output_dir') or ''))
PY
      fi
      return 0
    fi
  done
  echo 'UV_STATE=MISSING'
}

submit_for(){
  local job_id="$1" profile="$2" purpose="$3" job_file="${1}.json"
  verify_runtime
  local current
  current="$(state_for "$job_id" | sed -n 's/^UV_STATE=//p' | head -n1)"
  if [[ "$current" != MISSING ]]; then
    state_for "$job_id"
    echo 'UNIVERSAL_VIDEO_DIANA11_SUBMIT_IDEMPOTENT'
    return 0
  fi
  local tmp="$SPOOL/inbox/.$job_file.$$.$RANDOM.tmp"
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
  chown universal-video:universal-video "$tmp"
  chmod 0640 "$tmp"
  mv -n "$tmp" "$SPOOL/inbox/$job_file" || { rm -f "$tmp"; fail 'enqueue collision'; }
  echo 'UV_STATE=QUEUED'
  echo 'UNIVERSAL_VIDEO_DIANA11_SUBMIT_PASS'
}

need_root
[[ $# -eq 1 ]] || fail 'usage: universal-video-diana11 submit|status|submit-bridge|status-bridge'
case "$1" in
  submit) submit_for "$TRANSCRIPT_JOB_ID" transcript_only 'test transcription' ;;
  status) verify_runtime; state_for "$TRANSCRIPT_JOB_ID"; echo 'UNIVERSAL_VIDEO_DIANA11_STATUS_PASS' ;;
  submit-bridge) submit_for "$BRIDGE_JOB_ID" bridge_lesson 'bridge-specific post-transcript processing' ;;
  status-bridge) verify_runtime; state_for "$BRIDGE_JOB_ID"; echo 'UNIVERSAL_VIDEO_DIANA11_BRIDGE_STATUS_PASS' ;;
  *) fail 'unsupported operation' ;;
esac
