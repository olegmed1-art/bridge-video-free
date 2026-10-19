'use strict';
// New scenario only: actual UI -> separate Python CLI processes -> SQLite.
// The caller supplies a fresh private root with the author's accepted package.
const fs=require('node:fs'), path=require('node:path');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {randomUUID,createHash}=require('node:crypto');
const {mount}=require('../../../bridge_school_api/static/diana19/diana19.js');
const root=path.resolve(process.argv[2]), repo=path.resolve(__dirname,'../../..');
const python=process.env.BRIDGE_TEST_PYTHON || 'python';
const D='diana19-visible-exercise-001', S='diana19-visible-exercise-002';
const runtime=JSON.parse(fs.readFileSync(path.join(root,'private/diana19-visible-private.json'),'utf8'));
const hint=runtime.entries.find(item=>item.exercise_id===D).stages[1].question.question_ru;
const ids=['lesson-picker','lesson-title','lesson-question','visible-holdings','lesson-conditions',
  'lesson-options','answer-form','saved-response','saved-answer','lesson-explanation','explanation-text',
  'source-timecode','source-link','explanation-scope','diana-status','lesson-content','learner-answer','save-answer'];
class Element {
  constructor(){this.children=[];this.textContent='';this.value='';this.hidden=false;this.listeners={};}
  appendChild(child){this.children.push(child);return child;}
  replaceChildren(){this.children=[];}
  addEventListener(name,handler){this.listeners[name]=handler;}
  removeAttribute(name){delete this[name];}
}
function document(){const elements=Object.fromEntries(ids.map(id=>[id,new Element()]));
  return {getElementById:id=>elements[id],createElement:()=>new Element()};}
let calls=0, lostReply=true;
function call(message){
  calls++;
  const result=spawnSync(python,['-B','-m','site_integration.yan_spot_cycle.diana19_cli','--root',root],{
    cwd:repo,input:JSON.stringify(message),encoding:'utf8',timeout:20000,
    env:{...process.env,PYTHONPATH:repo,PYTHONDONTWRITEBYTECODE:'1'}});
  assert.equal(result.status,0,result.stderr);
  return JSON.parse(result.stdout);
}
const transport={catalog:async()=>call({operation:'catalog'}),
  view:async lesson_id=>call({operation:'view',lesson_id}),
  answer:async(lesson_id,value)=>{
    const reply=call({operation:'answer',lesson_id,value});
    if(lostReply){lostReply=false;throw new Error('Simulated dropped reply after commit');}
    return reply;
  }};
const hash=()=>createHash('sha256').update(fs.readFileSync(path.join(root,'private/diana19-visible-answers.sqlite3'))).digest('hex');
async function verify(){
  const doc=document(), el=id=>doc.getElementById(id), ui=mount(doc,transport,randomUUID);
  await ui.ready;
  assert.ok(el('lesson-picker').children.some(item=>item.value===D));
  assert.ok(el('lesson-picker').children.some(item=>item.value===S));
  const initial=call({operation:'view',lesson_id:D});
  const initialJSON=JSON.stringify(initial);
  assert.equal(initialJSON.includes(hint),false);
  assert.equal(initialJSON.includes('diamond_small_to_jack_then_overtake'),false);
  assert.equal(initial.explanation,null);
  el('lesson-picker').value=D; await ui.select();
  assert.notEqual(el('lesson-question').textContent,hint);
  const answer1='NEW automated first-stage answer. Not historical learner work.';
  el('learner-answer').value=answer1;
  await ui.submit({preventDefault(){}}); // Backend commits; the UI receives no confirmation.
  assert.notEqual(el('lesson-question').textContent,hint);
  assert.equal(el('lesson-explanation').hidden,true);
  const committed=hash();
  await ui.submit({preventDefault(){}}); // Same UUID, same content; no duplicate write.
  assert.equal(hash(),committed);
  assert.equal(el('lesson-question').textContent,hint);
  assert.ok(el('saved-answer').textContent.includes(answer1));
  assert.equal(el('answer-form').hidden,false);
  assert.equal(el('lesson-explanation').hidden,true);
  const reopened=document(), reopenUI=mount(reopened,transport,randomUUID);
  await reopenUI.ready;
  reopened.getElementById('lesson-picker').value=D; await reopenUI.select();
  assert.equal(reopened.getElementById('lesson-question').textContent,hint);
  assert.equal(hash(),committed);
  const answer2='NEW automated follow-up response after the first saved response.';
  reopened.getElementById('learner-answer').value=answer2;
  await reopenUI.submit({preventDefault(){}});
  assert.equal(reopened.getElementById('answer-form').hidden,true);
  assert.equal(reopened.getElementById('lesson-explanation').hidden,false);
  assert.ok(reopened.getElementById('saved-answer').textContent.includes(answer1));
  assert.ok(reopened.getElementById('saved-answer').textContent.includes(answer2));
  assert.ok(reopened.getElementById('explanation-text').children[0].textContent.length>300);
  assert.match(reopened.getElementById('source-timecode').textContent,/01:29:15/);
  reopened.getElementById('lesson-picker').value=S; await reopenUI.select();
  assert.equal(reopened.getElementById('lesson-explanation').hidden,true);
  reopened.getElementById('learner-answer').value='NEW automated independent spade answer.';
  await reopenUI.submit({preventDefault(){}});
  assert.equal(reopened.getElementById('lesson-explanation').hidden,false);
  assert.match(reopened.getElementById('source-timecode').textContent,/00:56:33/);
  const final=hash(), readback=call({operation:'view',lesson_id:D});
  assert.equal(readback.saved_answers.length,2);
  assert.equal(readback.stage.complete,true);
  assert.equal(hash(),final);
  const report={schema:'diana19-visible-new-stages-ui-proof-v1',verified_utc:new Date().toISOString(),
    exercise_ids:[D,S],private_source_pins:runtime.source_pins,independent_python_processes:calls,
    initial_payload_excludes_hint_and_answers:true,failed_or_unconfirmed_save_does_not_reveal_in_ui:true,
    durable_first_answer_before_hint:true,lost_reply_retry_idempotent:true,refresh_after_first_answer:true,
    full_explanation_after_followup:true,independent_spade_answer:true,reopen_readback_equal:true,
    actor:'AUTOMATED_VERIFICATION_NEW_RECORDS',transport:'REAL_PYTHON_CLI_WITH_TEST_DOM',
    journal_sha256:final,windows_http_verified:false,production_verified:false,
    automatic_assessment:false,canonical_promotion_allowed:false};
  fs.writeFileSync(path.join(root,'VISIBLE_UI_EVIDENCE.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify(report));
}
verify().catch(error=>{console.error(error);process.exitCode=1;});
