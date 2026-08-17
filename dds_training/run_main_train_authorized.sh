#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

: "${DDS_MAIN_EXPECTED_COMMIT:?DDS_MAIN_EXPECTED_COMMIT is required}"
: "${DDS_MAIN_APPROVAL_PHRASE:?DDS_MAIN_APPROVAL_PHRASE is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"
: "${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME is required}"
: "${GITHUB_REF_NAME:?GITHUB_REF_NAME is required}"
: "${GITHUB_ACTOR:?GITHUB_ACTOR is required}"
: "${GITHUB_TRIGGERING_ACTOR:?GITHUB_TRIGGERING_ACTOR is required}"

[[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]]
[[ "$GITHUB_REF_NAME" == "main" ]]
[[ "$GITHUB_ACTOR" == "olegmed1-art" ]]
[[ "$GITHUB_TRIGGERING_ACTOR" == "olegmed1-art" ]]
[[ "$GITHUB_SHA" == "$DDS_MAIN_EXPECTED_COMMIT" ]]
[[ "$DDS_MAIN_APPROVAL_PHRASE" == "ЭТАП-2-СТАРТ-ПОДТВЕРЖДАЮ" ]]

WORK="work/pilot"
PLAN="$WORK/shard_plan_main.json"
TASKS="$WORK/blind_tasks_crossfit_main.jsonl"
PREDICTIONS="$WORK/locked_predictions_main_train_adaptive.jsonl"
EVIDENCE_DIR="${DDS_MAIN_EVIDENCE_DIR:-/tmp/dds-main-shard-evidence}"
mkdir -p "$EVIDENCE_DIR"

source .venv/bin/activate

python - <<'PY'
import hashlib,json,sqlite3
from pathlib import Path
root=Path('work/pilot')
corpus=json.loads((root/'corpus_summary.json').read_text())
ready=json.loads((root/'stage2_readiness_main.json').read_text())
plan=json.loads((root/'shard_plan_main.json').read_text())
assert corpus['count']==30000 and corpus['seed']==20260815
assert ready['main_train']['ready'] is True
assert ready['holdout']['ready'] is False
assert ready['skill_claim']['ready'] is False
assert plan['shard_count']==20 and plan['family_safe'] is True and plan['restartable'] is True
assert hashlib.sha256((root/'locked_predictions_main_train_adaptive.jsonl').read_bytes()).hexdigest()=='ae946bec73f12bead7ebd6e54947bb6f2d2268798cf08b7b468495f943befe2b'
train=[s for s in plan['shards'] if s.get('splits')=={'train':2000}]
assert [s['index'] for s in train]==list(range(4,18)),[s['index'] for s in train]
db=sqlite3.connect(root/'training.sqlite3')
result_ids={r[0] for r in db.execute('select task_id from dds_results')}
tasks=[json.loads(x) for x in (root/'blind_tasks_crossfit_main.jsonl').read_text().splitlines() if x.strip()]
holdout={str(t['task_id']) for t in tasks if t.get('split') in {'validation','sealed_test'}}
assert not (result_ids & holdout),'fresh main holdout leakage before training'
assert 22497 <= len(result_ids) <= 50497,len(result_ids)
print({'restored_results':len(result_ids),'train_shards':len(train),'holdout_closed':True})
PY

expected=22497
for index in $(seq 4 17); do
  shard="main-shard-$(printf '%04d' "$index")"
  manifest="$WORK/main_shards/${shard}.jsonl"
  test -s "$manifest"
  expected=$((expected + 2000))

  complete="$(python - "$manifest" "$WORK/training.sqlite3" <<'PY'
import json,sqlite3,sys
tasks={json.loads(x)['task_id'] for x in open(sys.argv[1],encoding='utf-8') if x.strip()}
db=sqlite3.connect(sys.argv[2]); results={r[0] for r in db.execute('select task_id from dds_results')}
print('yes' if tasks <= results else 'no')
PY
)"

  if [[ "$complete" != "yes" ]]; then
    nonce="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    receipt="/tmp/${shard}-receipt.json"
    consume="/tmp/${shard}-consumed"
    rm -rf "$consume" && mkdir -p "$consume"
    python launch_authorization.py issue \
      --out "$receipt" \
      --scope main_train \
      --manifest "$manifest" \
      --nonce "$nonce" \
      --approval-phrase "$DDS_MAIN_APPROVAL_PHRASE" \
      --expected-commit "$DDS_MAIN_EXPECTED_COMMIT"
    python authorized_run_stage.py \
      --receipt "$receipt" \
      --nonce "$nonce" \
      --scope main_train \
      --manifest "$manifest" \
      --consume-dir "$consume" \
      --stage main \
      --work "$WORK" \
      --tasks-file "main_shards/${shard}.jsonl" \
      --predictions "$PREDICTIONS" \
      --run-id "main-${shard}-${GITHUB_RUN_ID}-a${GITHUB_RUN_ATTEMPT}" \
      --checkpoint-every 100 \
      --snapshot-every 500 \
      --milestone-every 2000 \
      --no-generate-followups \
      | tee "$WORK/${shard}-evaluate.log"
  fi

  python run_stage.py audit \
    --work "$WORK" \
    --run-id "main-${shard}-audit-${GITHUB_RUN_ID}-a${GITHUB_RUN_ATTEMPT}" \
    --fail-on-error > "$WORK/${shard}-audit.json"

  SHARD="$shard" EXPECTED="$expected" EVIDENCE_DIR="$EVIDENCE_DIR" python - <<'PY'
import hashlib,json,os,sqlite3
from pathlib import Path
shard_id=os.environ['SHARD']; expected=int(os.environ['EXPECTED']); root=Path('work/pilot')
plan=json.loads((root/'shard_plan_main.json').read_text()); shard=next(x for x in plan['shards'] if x['shard_id']==shard_id); shard_ids=set(map(str,shard['task_ids']))
db=sqlite3.connect(root/'training.sqlite3'); result_ids={r[0] for r in db.execute('select task_id from dds_results')}
assert len(result_ids)==expected,(len(result_ids),expected)
assert shard_ids<=result_ids and len(shard_ids)==2000
tasks=[json.loads(x) for x in (root/'blind_tasks_crossfit_main.jsonl').read_text().splitlines() if x.strip()]
holdout={str(t['task_id']) for t in tasks if t.get('split') in {'validation','sealed_test'}}
assert not (result_ids & holdout),'fresh main holdout leakage'
summary={'schema':'dds-main-shard-evidence-v1','shard_id':shard_id,'status':'completed','task_count':2000,'resume_key':shard['resume_key'],'task_ids_sha256':shard['task_ids_sha256'],'total_dds_results':expected,'holdout_closed':True,'workflow_run_id':os.environ['GITHUB_RUN_ID'],'workflow_run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'commit_sha':os.environ['GITHUB_SHA'],'prediction_sha256':hashlib.sha256((root/'locked_predictions_main_train_adaptive.jsonl').read_bytes()).hexdigest()}
out=Path(os.environ['EVIDENCE_DIR']); out.mkdir(parents=True,exist_ok=True); (out/f'{shard_id}-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
snap=out/f'{shard_id}-training.sqlite3'; snap.write_bytes((root/'training.sqlite3').read_bytes()); (out/f'{shard_id}-training.sqlite3.sha256').write_text(hashlib.sha256(snap.read_bytes()).hexdigest()+'  '+snap.name+'\n')
print(summary)
PY
done

python resolve_investigations_auto.py \
  --work "$WORK" \
  --tasks "$TASKS" \
  --run-id "main-train-investigations-${GITHUB_RUN_ID}-a${GITHUB_RUN_ATTEMPT}" \
  --out "$WORK/main_train_investigation_resolution.json"
python run_stage.py audit \
  --work "$WORK" \
  --run-id "main-train-final-audit-${GITHUB_RUN_ID}-a${GITHUB_RUN_ATTEMPT}" \
  --fail-on-error > "$WORK/main_train_final_audit.json"

python - <<'PY'
import json,sqlite3
from pathlib import Path
root=Path('work/pilot'); db=sqlite3.connect(root/'training.sqlite3')
result_ids={r[0] for r in db.execute('select task_id from dds_results')}; assert len(result_ids)==50497,len(result_ids)
tasks=[json.loads(x) for x in (root/'blind_tasks_crossfit_main.jsonl').read_text().splitlines() if x.strip()]
train={str(t['task_id']) for t in tasks if t.get('split')=='train'}; holdout={str(t['task_id']) for t in tasks if t.get('split') in {'validation','sealed_test'}}
assert train<=result_ids and len(train)==28000
assert not (result_ids & holdout)
opened=db.execute("select count(*) from investigation_events where event_type='opened'").fetchone()[0]; resolved=db.execute("select count(*) from investigation_events where event_type='resolved'").fetchone()[0]; assert opened==resolved,(opened,resolved)
summary={'schema':'dds-main-base-train-complete-v1','fresh_train_tasks':28000,'total_dds_results':50497,'holdout_closed':True,'investigations_opened':opened,'investigations_resolved':resolved,'next_gate':'OOF calibration + full-play/continuation TRAIN before validation'}
(root/'main_base_train_complete.json').write_text(json.dumps(summary,indent=2)+'\n'); print(json.dumps(summary,indent=2))
PY
