#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Exact, single-job root helper for the Diana 11 transcript-only acceptance run.
# No arbitrary file id, path, shell, profile, command, or payload is accepted.

readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SPOOL="$BASE_DIR/spool"
readonly JOB_ID='diana11-transcript-20260825-01'
readonly JOB_FILE="$JOB_ID.json"
readonly DRIVE_FILE_ID='1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C'

fail(){ echo "ERROR: $*" >&2; exit 1; }
need_root(){ [[ $(id -u) -eq 0 ]] || fail 'must run as root'; }
verify_runtime(){
  systemctl is-active --quiet universal-video.service || fail 'universal-video.service inactive'
  [[ -d "$SPOOL/inbox" && -d "$SPOOL/running" && -d "$SPOOL/done" && -d "$SPOOL/failed" ]] || fail 'spool missing'
  id universal-video >/dev/null 2>&1 || fail 'universal-video user missing'
}

state(){
  local d
  for d in done failed running inbox; do
    if [[ -f "$SPOOL/$d/$JOB_FILE" && ! -L "$SPOOL/$d/$JOB_FILE" ]]; then
      case "$d" in
        done) echo 'UV_STATE=COMPLETED' ;;
        failed) echo 'UV_STATE=FAILED' ;;
        running) echo 'UV_STATE=RUNNING' ;;
        inbox) echo 'UV_STATE=QUEUED' ;;
      esac
      if [[ "$d" == failed ]]; then
        python3 - "$SPOOL/$d/$JOB_FILE" <<'PY'
import json,sys
x=json.load(open(sys.argv[1], encoding='utf-8'))
print('UV_ERROR_TYPE='+str(x.get('error_type') or '')[:120])
print('UV_ERROR='+str(x.get('error') or '').replace('\n',' ')[:500])
PY
      elif [[ "$d" == done ]]; then
        python3 - "$SPOOL/$d/$JOB_FILE" <<'PY'
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

submit(){
  verify_runtime
  local current
  current="$(state | sed -n 's/^UV_STATE=//p' | head -n1)"
  if [[ "$current" != MISSING ]]; then
    state
    echo 'UNIVERSAL_VIDEO_DIANA11_SUBMIT_IDEMPOTENT'
    return 0
  fi
  local tmp="$SPOOL/inbox/.$JOB_FILE.$$.$RANDOM.tmp"
  python3 - "$tmp" <<PY
import json,sys
payload={
  'job_id':'$JOB_ID',
  'profile':'transcript_only',
  'source':{'kind':'google_drive','file_id':'$DRIVE_FILE_ID','name':'Диана 11'},
  'project':'Школа спортивного бриджа',
  'metadata':{'purpose':'test transcription','human_requested':True},
  'options':{'chunk_seconds':600,'max_source_bytes':2147483648,'max_duration_seconds':43200},
}
with open(sys.argv[1],'w',encoding='utf-8') as f:
    json.dump(payload,f,ensure_ascii=False,indent=2)
    f.write('\n')
PY
  chown universal-video:universal-video "$tmp"
  chmod 0640 "$tmp"
  mv -n "$tmp" "$SPOOL/inbox/$JOB_FILE" || { rm -f "$tmp"; fail 'enqueue collision'; }
  echo 'UV_STATE=QUEUED'
  echo 'UNIVERSAL_VIDEO_DIANA11_SUBMIT_PASS'
}

need_root
[[ $# -eq 1 ]] || fail 'usage: universal-video-diana11 submit|status'
case "$1" in
  submit) submit ;;
  status) verify_runtime; state; echo 'UNIVERSAL_VIDEO_DIANA11_STATUS_PASS' ;;
  *) fail 'unsupported operation' ;;
esac
