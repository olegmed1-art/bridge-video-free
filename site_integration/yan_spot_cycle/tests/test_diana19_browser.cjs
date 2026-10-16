'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {browserTransport}=require('../../../bridge_school_api/static/diana19/diana19.js');

test('PHP endpoint transport preserves route, CSRF and same-origin credentials',async()=>{
  const calls=[];
  const config={catalog:'/index.php?route=practice%2Fdiana19%2Fcatalog',
    lesson:'/index.php?route=practice%2Fdiana19%2Flesson%2F',answer:'/index.php?route=practice%2Fdiana19%2Fanswer%2F'};
  const document={getElementById:()=>({textContent:JSON.stringify(config)}),querySelector:()=>({content:'test-csrf'})};
  const window={location:{href:'http://127.0.0.1:8090/index.php',origin:'http://127.0.0.1:8090'},fetch:async(url,options)=>{
    calls.push({url,options});return {ok:true,json:async()=>({status:'STAGING'})};}};
  const transport=browserTransport(document,window);
  await transport.catalog();await transport.view('diana19-q-001');
  await transport.answer('diana19-q-001',{answer:'new protocol input'});
  assert.equal(calls.length,3);
  assert.equal(new URL(calls[2].url).searchParams.get('route'),'practice/diana19/answer/diana19-q-001');
  assert.equal(calls[2].options.headers['X-CSRF-Token'],'test-csrf');
  assert.equal(calls[2].options.credentials,'same-origin');
  assert.equal(calls[2].options.cache,'no-store');
});

test('transport rejects foreign endpoint and arbitrary lesson path before fetch',async()=>{
  let calls=0;
  const config={catalog:'https://other.invalid/index.php?route=practice/diana19/catalog',
    lesson:'/index.php?route=practice%2Fdiana19%2Flesson%2F'};
  const document={getElementById:()=>({textContent:JSON.stringify(config)})};
  const window={location:{href:'http://127.0.0.1:8090/',origin:'http://127.0.0.1:8090'},fetch:async()=>{calls++;}};
  await assert.rejects(browserTransport(document,window).catalog());
  await assert.rejects(browserTransport(document,window).view('../../server'));
  assert.equal(calls,0);
});
