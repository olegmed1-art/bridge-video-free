'use strict';
// Real UI controller -> real Python CLI service -> permanent SQLite journal.
// No socket claim: actual HTTP is verified separately on the Linux executor.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {randomUUID,createHash}=require('node:crypto');
const {mount}=require('../../../bridge_school_api/static/diana19/diana19.js');
const root=path.resolve(process.argv[2]);
const python=process.env.BRIDGE_TEST_PYTHON || 'python';
const repo=path.resolve(__dirname,'../../..');
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
let calls=0;
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
  answer:async(lesson_id,value)=>call({operation:'answer',lesson_id,value})};
async function verify(){
  const doc=document(), ui=mount(doc,transport,randomUUID), el=id=>doc.getElementById(id);
  await ui.ready;
  assert.equal(el('lesson-picker').children.length,3);
  const sequence=[];
  for(const option of el('lesson-picker').children){
    el('lesson-picker').value=option.value;
    await ui.select();
    assert.ok(el('lesson-question').textContent.length>20);
    assert.equal(el('lesson-explanation').hidden,true);
    assert.equal(el('source-link').href,undefined);
    assert.equal(el('explanation-text').children.length,0);
    const text='NEW automated UI verification response for '+option.value+'. Not historical learner work.';
    el('learner-answer').value=text;
    await ui.submit({preventDefault(){}});
    assert.equal(el('saved-answer').textContent,text);
    assert.equal(el('answer-form').hidden,true);
    assert.equal(el('lesson-explanation').hidden,false);
    assert.ok(el('explanation-text').children.length>=1);
    assert.match(el('source-timecode').textContent,/\d\d:\d\d:\d\d\.\d{3}/);
    assert.match(el('source-link').href,/^https:\/\/drive\.google\.com\/file\/d\//);
    assert.match(el('explanation-scope').textContent,/ASR/);
    sequence.push({lesson_id:option.value,question:el('lesson-question').textContent,
      saved_answer:text,explanation_sha256:createHash('sha256').update(el('explanation-text').children.map(p=>p.textContent).join('\n')).digest('hex'),
      timecode:el('source-timecode').textContent,source_url:el('source-link').href,
      path:['select','question','new_answer','persisted','explanation_with_source_and_timecode']});
  }
  const hash=()=>createHash('sha256').update(fs.readFileSync(path.join(root,'private/diana19-answers.sqlite3'))).digest('hex');
  const before=hash();
  const reopened=document(), reopenedUI=mount(reopened,transport,randomUUID);
  await reopenedUI.ready;
  for(const item of sequence){
    reopened.getElementById('lesson-picker').value=item.lesson_id;
    await reopenedUI.select();
    assert.equal(reopened.getElementById('saved-answer').textContent,item.saved_answer);
    assert.equal(reopened.getElementById('source-timecode').textContent,item.timecode);
  }
  assert.equal(hash(),before);
  const report={schema:'diana19-real-local-ui-flow-v1',verified_utc:new Date().toISOString(),
    source_archive_sha256:'58ce6d33b91ce293fedbcea3837ee0d42b7d4f092d6c2795a90b9653ffda9f4a',
    verification_transport:'REAL_PYTHON_CLI_NO_WINDOWS_HTTP_CLAIM',ui:'ACTUAL_CONTROLLER_WITH_TEST_DOM',
    actor:'AUTOMATED_VERIFICATION_NEW_RECORDS',status:'STAGING',cases:sequence,
    independent_python_processes:calls,journal_sha256:before,reopen_readback_equal:true,
    automatic_assessment:false,canonical_promotion_allowed:false,windows_http_verified:false};
  fs.writeFileSync(path.join(root,'UI_FLOW_EVIDENCE.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify({lessons:sequence.length,readback:true,independent_python_processes:calls,
    journal_sha256:before,evidence:path.join(root,'UI_FLOW_EVIDENCE.json'),windows_http_verified:false}));
}
verify().catch(error=>{console.error(error);process.exitCode=1;});
