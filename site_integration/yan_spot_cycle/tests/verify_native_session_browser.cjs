'use strict';
// Actual controller and browser transport over real HTTP; minimal test DOM.
// Cookies are kept in process memory and excluded from the evidence artifact.
const fs=require('node:fs'), assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto');
const {mount,browserTransport}=require('../../../bridge_school_api/static/diana19/diana19.js');
const origin=process.argv[2], reportPath=process.argv[3];
const D='diana19-visible-exercise-001', S='diana19-visible-exercise-002';
const url=route=>origin+'/index.php?'+new URLSearchParams({route:'practice/diana19'+route});
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
function browser(){
  let cookie='', csrf='', config=null, dropReply=false;
  const sentIds=[];
  async function fetch(url,options={}){
    assert.equal(new URL(url).origin,origin);
    const headers={...options.headers,...(cookie?{Cookie:cookie}:{})};
    const response=await globalThis.fetch(url,{...options,headers,redirect:'error'});
    const set=response.headers.get('set-cookie');if(set)cookie=set.split(';')[0];
    if(options.method==='POST' && options.body)sentIds.push(JSON.parse(options.body).request_id);
    if(dropReply && options.method==='POST' && response.ok){
      dropReply=false;await response.text();throw new Error('Simulated lost HTTP reply after commit');
    }
    return response;
  }
  async function document(){
    const response=await fetch(url(''));assert.equal(response.status,200);
    const html=await response.text();
    assert.ok(html.includes('native-session-contract-fixture'));
    assert.ok(!html.includes('FIXTURE_GUIDED_ONLY') && !html.includes('FIXTURE_EXPLANATION_ONLY'));
    csrf=html.match(/name="bridge-school-csrf-token" content="([^"]+)"/)[1];
    config=JSON.parse(html.match(/id="diana19-host">([^<]+)<\/script>/)[1]);
    const els=Object.fromEntries(ids.map(id=>[id,new Element()]));
    els['diana19-host']={textContent:JSON.stringify(config)};
    return {getElementById:id=>els[id],createElement:()=>new Element(),
      querySelector:()=>({content:csrf})};
  }
  const window={location:{href:origin+'/index.php',origin},fetch};
  return {fetch,document,window,sentIds,drop:()=>{dropReply=true;},
    headers:()=>({'Origin':origin,'X-CSRF-Token':csrf,'Content-Type':'application/json'})};
}
async function json(client,suffix,body,extra){
  const response=await client.fetch(url(suffix),body?{method:'POST',headers:extra||client.headers(),body:JSON.stringify(body)}:{});
  const text=await response.text();return {status:response.status,value:JSON.parse(text),text};
}
async function verify(){
  const alice=browser(), bob=browser();
  let doc=await alice.document(), ui=mount(doc,browserTransport(doc,alice.window),randomUUID);
  await ui.ready;
  assert.equal(doc.getElementById('lesson-picker').children.length,5);
  const first=await json(alice,'/lesson/'+D);
  assert.equal(first.status,200);assert.equal(first.value.explanation,null);
  assert.ok(!first.text.includes('FIXTURE_GUIDED_ONLY') && !first.text.includes('FIXTURE_EXPLANATION_ONLY'));
  const message={request_id:randomUUID(),question_sha256:first.value.question_sha256,
    stage_id:'initial',answer:'NEW automated security check input.'};
  assert.equal((await json(alice,'/answer/'+D,message,{'Content-Type':'application/json'})).status,403);
  assert.equal((await json(alice,'/answer/'+D,{...message,stage_id:'followup'})).status,409);
  assert.equal((await json(alice,'/answer/'+D,{...message,owner_id:'other'})).status,409);
  const forged=await alice.fetch(url('/catalog'),{headers:{'X-Learner-Id':'other'}});
  assert.equal(forged.status,403);
  assert.equal((await alice.fetch(url('/server/GRADING_KEY.json'))).status,404);
  assert.equal((await alice.fetch(url('/assets/private.json'))).status,404);
  doc.getElementById('lesson-picker').value=D;await ui.select();
  doc.getElementById('learner-answer').value='NEW Alice independent response.';
  alice.drop();await ui.submit({preventDefault(){}});
  assert.ok(!doc.getElementById('lesson-question').textContent.includes('FIXTURE_GUIDED_ONLY'));
  assert.equal(doc.getElementById('lesson-explanation').hidden,true);
  await ui.submit({preventDefault(){}});
  assert.equal(alice.sentIds.at(-1),alice.sentIds.at(-2));
  assert.equal(doc.getElementById('lesson-question').textContent,'FIXTURE_GUIDED_ONLY_AFTER_FIRST_COMMIT');
  assert.ok(doc.getElementById('saved-answer').textContent.includes('NEW Alice independent response.'));
  assert.equal(doc.getElementById('lesson-explanation').hidden,true);
  doc=await alice.document();ui=mount(doc,browserTransport(doc,alice.window),randomUUID);await ui.ready;
  doc.getElementById('lesson-picker').value=D;await ui.select();
  assert.equal(doc.getElementById('lesson-question').textContent,'FIXTURE_GUIDED_ONLY_AFTER_FIRST_COMMIT');
  doc.getElementById('learner-answer').value='NEW Alice guided response.';
  await ui.submit({preventDefault(){}});
  assert.equal(doc.getElementById('lesson-explanation').hidden,false);
  assert.equal(doc.getElementById('explanation-text').children[0].textContent,'FIXTURE_EXPLANATION_ONLY_AFTER_FINAL_COMMIT');
  doc.getElementById('lesson-picker').value=S;await ui.select();
  doc.getElementById('learner-answer').value='NEW Alice second exercise response.';
  await ui.submit({preventDefault(){}});
  assert.equal(doc.getElementById('lesson-explanation').hidden,false);
  const aliceD=(await json(alice,'/lesson/'+D)).value;
  const aliceS=(await json(alice,'/lesson/'+S)).value;
  await bob.document();
  const bobFirst=(await json(bob,'/lesson/'+D)).value;
  assert.equal(bobFirst.saved,null);assert.equal(bobFirst.explanation,null);
  const bobNext=(await json(bob,'/answer/'+D,{request_id:randomUUID(),
    question_sha256:bobFirst.question_sha256,stage_id:'initial',answer:'NEW Bob independent response.'})).value;
  assert.equal(bobNext.saved.answer,'NEW Bob independent response.');
  assert.equal(bobNext.saved_answers.length,1);
  assert.deepEqual((await json(alice,'/lesson/'+D)).value,aliceD);
  console.log('NATIVE_HTTP_BEFORE_RESTART_READY');
  await new Promise(resolve=>process.stdin.once('data',resolve));
  assert.deepEqual((await json(alice,'/lesson/'+D)).value,aliceD);
  assert.deepEqual((await json(alice,'/lesson/'+S)).value,aliceS);
  assert.deepEqual((await json(bob,'/lesson/'+D)).value,bobNext);
  assert.equal((await json(bob,'/lesson/'+S)).value.saved,null);
  const report={schema:'native-php-anonymous-staged-http-browser-proof-v1',status:'PASS',
    ui:'ACTUAL_CONTROLLER_AND_BROWSER_TRANSPORT_WITH_TEST_DOM',transport:'REAL_HTTP_PHP_NATIVE_SESSION_UVICORN_SQLITE',
    native_php_sessions:2,owner_journals_isolated:true,initial_payload_excludes_hint_and_key:true,
    first_commit_before_guided_prompt:true,lost_http_reply_retry_same_request_id:true,
    refreshed_ui_recovers_stage:true,explanation_after_followup_commit:true,
    wrong_csrf_and_owner_headers_rejected:true,direct_future_stage_rejected:true,
    backend_restarted:true,persisted_answers_reloaded:true,new_automated_answer_records:4,
    actual_yan_cms_verified:false,reviewed_teacher_materials_used:false,production_identity_enabled:false,
    automatic_assessment:false,canonical_promotion_allowed:false};
  fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify(report));
}
verify().catch(error=>{console.error(error);process.exitCode=1;});
