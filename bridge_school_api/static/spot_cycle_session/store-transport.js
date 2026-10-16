'use strict';
(function(root){
  const {ProtocolError}=typeof module!=='undefined'?require('./teacher-client.js'):root.BridgeTeacherClient;
  const schemas=['local-teacher-session-journal-v1','local-teacher-session-journal-v2'];
  const clone=value=>JSON.parse(JSON.stringify(value));
  const fail=(code,message)=>{throw new ProtocolError(code,message);};
  function canonical(value){
    if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
    if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>canonical(key)+':'+canonical(value[key])).join(',')+'}';
    const encoded=JSON.stringify(value);
    if(encoded===undefined||typeof value==='number'&&!Number.isFinite(value))fail('INVALID_DELIVERY','Non-JSON delivery');
    return encoded.replace(/[\u007f-\uffff]/g,char=>'\\u'+char.charCodeAt(0).toString(16).padStart(4,'0'));
  }
  async function digest(value){
    if(!root.crypto?.subtle)fail('CRYPTO_UNAVAILABLE','Web Crypto is required for delivery integrity');
    return Array.from(new Uint8Array(await root.crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value)))),byte=>byte.toString(16).padStart(2,'0')).join('');
  }
  function createStoreTransport(store){
    if(!store||['resume','execute','acknowledge'].some(method=>typeof store[method]!=='function'))fail('INVALID_TRANSPORT','Inject the three TeacherSessionStore operations');
    let sessionId=null,expectedSession=null,revision=null,pending=null,connected=false,activeSchema=null,sessionMode=null,canSend=true,acknowledgedHistory=null;
    function base(value){
      if(!value||!schemas.includes(value.schema)||typeof value.session_id!=='string'||!value.session_id||!Number.isInteger(value.revision)||value.revision<1)fail('INVALID_DELIVERY','Invalid store envelope');
      if(activeSchema&&value.schema!==activeSchema)fail('SCHEMA_CHANGED','Store schema changed within a session');
      if((sessionId&&value.session_id!==sessionId)||(expectedSession&&value.session_id!==expectedSession))fail('SESSION_MISMATCH','Store response belongs to another session');
      sessionId=value.session_id;activeSchema=value.schema;
    }
    function persisted(value){if(value.local_journal_persisted!==true||value.domain_writes_performed!==false)fail('UNSAFE_BOUNDARY','Store persistence boundary changed');}
    async function delivery(value){
      base(value);persisted(value);
      if(typeof value.delivery_id!=='string'||!value.delivery_id||value.delivery_id.length>120||typeof value.result_sha256!=='string'||!/^[a-f0-9]{64}$/.test(value.result_sha256)||!value.result)fail('INVALID_DELIVERY','Invalid delivery identity');
      if(await digest(value.result)!==value.result_sha256)fail('DELIVERY_HASH_MISMATCH','Delivery content differs from its receipt');
      return clone(value);
    }
    async function resume(){
      const value=await store.resume();base(value);persisted(value);
      if(value.schema==='local-teacher-session-journal-v2'&&!['LOCAL_FORMATIVE','SPOT_CANDIDATE_PREVIEW_V1'].includes(value.session_mode))fail('UNSUPPORTED_SESSION_MODE','Unknown teacher session mode');
      if(sessionMode&&value.session_mode&&sessionMode!==value.session_mode)fail('SESSION_MODE_CHANGED','Session mode cannot change on reload');
      sessionMode=value.session_mode||'LOCAL_FORMATIVE';
      if(value.support_delivery_semantics!=='COMMITTED_MEANS_POSSIBLY_EXPOSED'||typeof value.can_send_new_command!=='boolean')fail('INVALID_RESUME','Invalid recovery boundary');
      if(revision!==null&&value.revision<revision)fail('STALE_RESPONSE','Store revision moved backwards');
      if(value.pending_delivery){
        if(value.can_send_new_command||value.current_assignment!==null)fail('INVALID_RESUME','Pending delivery must hide assignment');
        pending=await delivery(value.pending_delivery);
        if(pending.revision!==value.revision)fail('INVALID_RESUME','Pending delivery revision differs');
      }else{
        if(!value.current_assignment)fail('INVALID_RESUME','Missing current assignment');
        const terminal=value.current_assignment.status==='AWAIT_TEACHER_REVIEW'&&!value.current_assignment.issuance_id;
        if(value.can_send_new_command===terminal)fail('INVALID_RESUME','Input flag contradicts assignment');
        pending=null;
      }
      canSend=value.can_send_new_command;
      acknowledgedHistory=null;
      if(value.acknowledged_history){
        const history=value.acknowledged_history;
        if(history.schema!=='teacher-ui-acknowledged-history-v1'||history.session_id!==sessionId||history.revision!==value.revision||!Array.isArray(history.rows)||history.rows.length>32)fail('INVALID_HISTORY','Invalid history projection');
        const ids=new Set(),issuances=new Set();let lastAck=1;
        for(const row of history.rows){
          if(!row.assignment||row.assignment.session_id!==sessionId||!row.assignment.issuance_id||issuances.has(row.assignment.issuance_id)||!Array.isArray(row.deliveries))fail('INVALID_HISTORY','Invalid history row');
          issuances.add(row.assignment.issuance_id);
          for(const item of row.deliveries){
            const d=await delivery(item.delivery);
            if(!['RESPONSE','HINT','ADVANCE'].includes(item.kind)||ids.has(d.delivery_id)||ids.size>=128||!Number.isInteger(item.ack_revision)||item.ack_revision!==d.revision+1||item.ack_revision<=lastAck||item.ack_revision>value.revision||d.delivery_id===pending?.delivery_id)fail('INVALID_HISTORY','Invalid history delivery');
            const bound=d.result.issuance_id||d.result.from_issuance_id;
            if(bound&&bound!==row.assignment.issuance_id)fail('INVALID_HISTORY','History issuance mismatch');
            ids.add(d.delivery_id);lastAck=item.ack_revision;
          }
        }
        acknowledgedHistory=clone(history);
      }
      revision=value.revision;return value;
    }
    async function acceptResult(){
      if(!pending)fail('NO_PENDING_DELIVERY','Nothing to acknowledge');
      const accepted=pending;
      const ack=await store.acknowledge({delivery_id:accepted.delivery_id,result_sha256:accepted.result_sha256});
      base(ack);
      if(ack.kind!=='DELIVERY_ACK'||ack.delivery_id!==accepted.delivery_id||ack.result_sha256!==accepted.result_sha256||ack.revision<=accepted.revision)fail('INVALID_ACK','ACK does not match delivery');
      // An ACK retry may return its old revision. Resume is the authority now.
      revision=Math.max(revision||0,ack.revision);
      const state=await resume();
      if(state.pending_delivery)fail('DELIVERY_ACK_REQUIRED','Another delivery needs explicit recovery');
      connected=true;return state.current_assignment;
    }
    async function current(acceptPending){
      connected=false;const state=await resume();
      if(state.pending_delivery){
        if(typeof acceptPending!=='function')fail('PENDING_DELIVERY','Restore the pending receipt before continuing');
        await acceptPending({session_id:sessionId,delivery_id:pending.delivery_id,result:clone(pending.result)});
        return acceptResult();
      }
      connected=true;return state.current_assignment;
    }
    async function execute(kind,args){
      if(!connected)fail('RESUME_REQUIRED','Resume this session first');
      const command={command_id:args.command_id,kind,issuance_id:args.issuance_id,payload:kind==='RESPONSE'?clone(args.response):null};
      const result=await delivery(await store.execute({command,expected_revision:revision}));
      if(result.delivery_id!==args.command_id)fail('STALE_RESPONSE','Delivery is for a different command');
      pending=result;return clone(result.result);
    }
    return {kind:'teacher-session-store-v1',current,acceptResult,pinSession(id){if(sessionId&&sessionId!==id)fail('SESSION_MISMATCH','Snapshot belongs to another session');expectedSession=id;},
      record_response:args=>execute('RESPONSE',args),request_hint:args=>execute('HINT',args),advance:args=>execute('ADVANCE',args),
      history:()=>acknowledgedHistory?clone(acknowledgedHistory):null,
      status:()=>({session_id:sessionId,session_mode:sessionMode,revision,can_send_new_command:canSend,local_journal_persisted:connected||!!pending,domain_writes_performed:false,pending_delivery_id:pending?.delivery_id||null})};
  }
  const api={createStoreTransport};
  if(typeof module!=='undefined')module.exports=api;else root.BridgeStoreTransport=api;
})(typeof globalThis!=='undefined'?globalThis:this);
