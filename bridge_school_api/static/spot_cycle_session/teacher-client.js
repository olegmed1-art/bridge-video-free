'use strict';
(function(root){
  class ProtocolError extends Error { constructor(code,message){super(message);this.name='ProtocolError';this.code=code;} }
  const fail=(code,message)=>{throw new ProtocolError(code,message);};
  const copy=value=>JSON.parse(JSON.stringify(value));
  const stable=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
  const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
  const spotChoices={'spot.a7c2':['DA','H2'],'spot.f3b8':['CA','HA']};
  function text(value,label,max=6000){if(typeof value!=='string'||value.length>max)fail('INVALID_PUBLIC_DATA','Invalid '+label);return value;}
  function identity(value,label){const result=text(value,label,160);if(!result)fail('INVALID_PUBLIC_DATA','Missing '+label);return result;}
  function hash(value){if(typeof value!=='string'||!/^[a-f0-9]{64}$/i.test(value))fail('INVALID_PUBLIC_DATA','Invalid prompt hash');return value;}
  function boundary(value){
    if(!object(value))fail('INVALID_PUBLIC_DATA','Object required');
    for(const key of ['actual_mastery','criterion_met','learner_error'])if(value[key]!==null)fail('UNSAFE_BOUNDARY','Mastery/diagnosis boundary changed');
    for(const key of ['persisted','student_profile_written','curriculum_activated','mastery_event_created'])if(value[key]!==false)fail('UNSAFE_BOUNDARY','Persistence boundary changed');
    return {actual_mastery:null,criterion_met:null,learner_error:null,persisted:false,student_profile_written:false,curriculum_activated:false,mastery_event_created:false,explanation_review:'HUMAN_REVIEW_REQUIRED'};
  }
  function cards(value){
    if(!object(value)||Object.keys(value).some(key=>!['S','H','D','C'].includes(key)))fail('UNSAFE_CARDS','Only visible hand/dummy suits are permitted');
    const result={};let count=0;
    for(const [suit,ranks]of Object.entries(value)){
      if(!Array.isArray(ranks)||ranks.length>13||ranks.some(rank=>typeof rank!=='string'||!/^[AKQJT98765432]$/.test(rank))||new Set(ranks).size!==ranks.length)fail('UNSAFE_CARDS','Invalid visible cards');
      result[suit]=[...ranks];count+=ranks.length;
    }
    if(count>13)fail('UNSAFE_CARDS','Too many cards in visible hand');
    return result;
  }
  function presentation(value){
    if(!object(value))fail('INVALID_PUBLIC_DATA','Presentation required');
    for(const key of ['expected','answer_key','teacher_snapshot','defenders','W','E','west','east'])if(Object.hasOwn(value,key))fail('TEACHER_ONLY_DATA','Closed material is not a student presentation');
    const result={title:text(value.title,'title'),hand:cards(value.hand),dummy:cards(value.dummy),target_suit:identity(value.target_suit,'target suit'),questions:[]};
    if(!['S','H','D','C'].includes(result.target_suit))fail('INVALID_PUBLIC_DATA','Invalid target suit');
    for(const key of ['author_label','strain','entry_suit','opening_lead','lead','explanation_prompt','limitation','task_text','position_kind'])if(value[key]!==undefined)result[key]=text(value[key],key);
    if(value.position_kind==='COMPLETE_REMAINING_HANDS'){
      if(value.cards_per_hand!==5||value.trick_index!==9||value.strain!=='NT'||value.lead!=='HAND')fail('UNSUPPORTED_PREVIEW','Unexpected pinned preview position');
      if(Object.values(result.hand).flat().length!==5||Object.values(result.dummy).flat().length!==5)fail('UNSAFE_CARDS','Preview requires five remaining cards per hand');
      const given=value.given_information;
      if(!object(given)||stable(given.defenders_remaining_lengths)!=='[2,2]'||!Array.isArray(given.played_in_target_suit)||given.played_in_target_suit.some(card=>typeof card!=='string'||!new RegExp('^'+result.target_suit+'[AKQJT98765432]$').test(card)))fail('UNSUPPORTED_PREVIEW','Missing public preview givens');
      result.cards_per_hand=5;result.trick_index=9;
      result.given_information={defenders_remaining_lengths:[2,2],played_in_target_suit:[...given.played_in_target_suit]};
    }
    const visible=Object.entries(result.hand).flatMap(([s,r])=>r.map(rank=>s+rank)).concat(Object.entries(result.dummy).flatMap(([s,r])=>r.map(rank=>s+rank)));
    if(new Set(visible).size!==visible.length)fail('UNSAFE_CARDS','Duplicate visible cards');
    if(!Array.isArray(value.questions)||!value.questions.length||value.questions.length>32)fail('INVALID_PUBLIC_DATA','Invalid questions');
    const ids=new Set();
    result.questions=value.questions.map(q=>{
      if(!object(q)||typeof q.id!=='string'||!/^[a-z][a-z0-9_]{0,79}$/.test(q.id)||ids.has(q.id))fail('INVALID_PUBLIC_DATA','Invalid question id');
      ids.add(q.id);if(!['integer','choice','boolean','card_pair'].includes(q.type))fail('UNSUPPORTED_QUESTION','Unsupported question type');
      const out={id:q.id,type:q.type,prompt:text(q.prompt,'question')};
      if(q.type==='integer'){if(!Number.isInteger(q.minimum)||!Number.isInteger(q.maximum)||q.minimum>q.maximum)fail('INVALID_PUBLIC_DATA','Invalid range');out.minimum=q.minimum;out.maximum=q.maximum;}
      if(q.type==='choice'){if(!Array.isArray(q.options)||q.options.length<1||q.options.length>32)fail('INVALID_PUBLIC_DATA','Invalid options');out.options=q.options.map(o=>({id:identity(o.id,'option id'),label:text(o.label,'option label')}));}
      if(q.type==='card_pair'){if(!Array.isArray(q.pair_order)||q.pair_order.length!==2)fail('INVALID_PUBLIC_DATA','Invalid pair');out.pair_order=q.pair_order.map(v=>text(v,'pair order',40));}
      return out;
    });
    result.conditions=[];
    if(Array.isArray(value.opening_choices))result.opening_choices=value.opening_choices.map(c=>({winner:text(c.winner,'winner',20),hand:text(c.hand,'hand card',3),dummy:text(c.dummy,'dummy card',3)}));
    for(const key of ['future_given','given_split','supplied_play','after_supplied_play','given_conditions','supplied_probe','contrast']){
      const block=value[key];if(block===undefined)continue;if(!object(block))fail('INVALID_PUBLIC_DATA','Invalid given condition');
      if(block.defenders_target_cards!==undefined&&(!Array.isArray(block.defenders_target_cards)||block.defenders_target_cards.length))fail('TEACHER_ONLY_DATA','Closed defender cards rejected');
      const out={kind:key,text:text(block.text,'given condition')};
      if(block.hand!==undefined)out.hand=cards(block.hand);
      if(block.dummy!==undefined)out.dummy=cards(block.dummy);
      if(block.lead!==undefined)out.lead=text(block.lead,'lead',20);
      result.conditions.push(out);
    }
    return result;
  }
  function assignment(value){
    boundary(value);
    if(value.status==='AWAIT_TEACHER_REVIEW')return null;
    if(value.method_version!=='local-teacher-routing-v1')fail('UNSUPPORTED_METHOD','Expected LOCAL_TEACHER_ROUTING_V1');
    if(value.audience==='TEACHER_ONLY'||value.runs||value.journal)fail('TEACHER_ONLY_DATA','Teacher snapshot rejected');
    const prompt=value.prompt;
    if(!object(prompt)||prompt.response_schema!=='low-card-structured-response-v1')fail('INVALID_PUBLIC_DATA','Unsupported response schema');
    const preview=prompt.method_version==='spot-routing-handoff-v2';
    if(preview&&(value.session_mode!=='SPOT_CANDIDATE_PREVIEW_V1'||value.automatic_assessment_enabled!==false||value.approved_catalog_member!==false))fail('UNSAFE_PREVIEW','Preview review boundaries missing');
    const publicPresentation=presentation(prompt.presentation);
    if(preview&&(!publicPresentation.questions.some(q=>q.id==='guaranteed_spot_tricks_from_position')||publicPresentation.questions.some(q=>q.id==='low_tricks')))fail('UNSAFE_PREVIEW','Preview metric must not alias low_tricks');
    return {
      session_id:identity(value.session_id,'session id'),issuance_id:identity(value.issuance_id,'issuance id'),
      method_version:identity(value.method_version,'method version'),
      context_exposure:text(value.context_exposure,'context exposure',80),support_history:text(value.support_history,'support history',80),
      session_mode:value.session_mode||'LOCAL_FORMATIVE',candidate_preview:preview,automatic_assessment_enabled:preview?false:null,
      prompt:{exercise_id:identity(prompt.exercise_id,'exercise id'),prompt_sha256:hash(prompt.prompt_sha256),response_schema:prompt.response_schema,presentation:publicPresentation},
      ...boundary(value)
    };
  }
  function receipt(value,binding){
    boundary(value);
    if(value.status!=='RECORDED_PENDING_FEEDBACK'||value.issuance_id!==binding.issuance_id||!Number.isInteger(value.response_no)||value.response_no<1)fail('INVALID_RECEIPT','Unexpected receipt');
    for(const key of ['feedback','assessment_status','correct','next_assignment','expected'])if(Object.hasOwn(value,key))fail('NON_NEUTRAL_RECEIPT','Receipt disclosed assessment');
    return {status:value.status,issuance_id:value.issuance_id,response_no:value.response_no,response_sha256:hash(value.response_sha256),message:'Ответ сохранён. До обратной связи можно перепроверить и прислать новую версию.',...boundary(value)};
  }
  function decision(value,binding){
    boundary(value);
    if(!['NEXT_ACTIVITY','AWAIT_TEACHER_REVIEW','CLARIFY_RESPONSE','WAIT_FIRST_RESPONSE'].includes(value.status)||!['PARTIAL','ABSTAIN'].includes(value.assessment_status))fail('INVALID_FEEDBACK','Unexpected feedback');
    if(value.from_issuance_id!==undefined&&value.from_issuance_id!==binding.issuance_id)fail('STALE_RESPONSE','Feedback belongs to another issuance');
    const feedback=Array.isArray(value.feedback)?value.feedback.map(item=>({criterion_id:text(item.criterion_id,'criterion',120),code:text(item.code,'feedback code',120),text:text(item.text,'feedback')})):[];
    const out={status:value.status,assessment_status:value.assessment_status,from_issuance_id:binding.issuance_id,feedback,...boundary(value)};
    for(const key of ['message','evidence_kind','reason','skill_focus'])if(value[key]!==undefined)out[key]=text(value[key],key);
    out.next_assignment=value.next_assignment?assignment(value.next_assignment):null;
    if(out.next_assignment&&out.next_assignment.session_id!==binding.session_id)fail('STALE_RESPONSE','Next assignment belongs to another session');
    if(value.status==='NEXT_ACTIVITY'&&!out.next_assignment)fail('INVALID_FEEDBACK','Missing teacher-selected assignment');
    return out;
  }
  function hintResult(value){
    boundary(value);
    if(value.status==='HINT_SHOWN')return {status:value.status,text:text(value.hint,'hint')};
    if(value.status==='PREVIEW_HINT_UNAVAILABLE'&&value.hint===null&&value.assessment_status==='ABSTAIN'&&value.automatic_assessment_enabled===false)return {status:value.status,text:text(value.message,'hint unavailable')};
    fail('INVALID_HINT','Unexpected hint');
  }
  function createClient(transport,options={}){
    if(!transport||['current','record_response','request_hint','advance'].some(name=>typeof transport[name]!=='function'))fail('INVALID_TRANSPORT','Inject the four TeacherLesson public methods');
    const listeners=new Set(),commands=new Map(),seenHelpDeliveries=new Set();
    let epoch=0,inflight=null,uncertain=null;
    let state={schema:'bridge-ui-state-v1',phase:'DISCONNECTED',assignment:null,receipt:null,feedback:null,next_assignment:null,hint:null,history:[],busy:false,error:null,restored_receipt_unverified:false,recovery:null,recovery_notice:null,local_journal_persisted:false,can_send_new_command:true,history_authoritative:false,session_mode:'LOCAL_FORMATIVE'};
    const emit=()=>{for(const listener of listeners)listener(copy(state));};
    const expose=()=>copy(state);
    function failState(error){state.error={code:error.code||'TRANSPORT_ERROR',message:error.message||'Transport failed',can_retry:!!uncertain};state.busy=false;emit();}
    function activate(value){state.assignment=value;state.receipt=null;state.feedback=null;state.next_assignment=null;state.hint=null;state.phase=value?'ANSWERING':'REVIEW';state.restored_receipt_unverified=false;if(value&&!state.history.some(row=>row.issuance_id===value.issuance_id))state.history.push({issuance_id:value.issuance_id,exercise_id:value.prompt.exercise_id,title:value.prompt.presentation.title,support_history:value.support_history,context_exposure:value.context_exposure,response_count:0,hints:0,feedback:null});}
    const historyRow=()=>state.history.find(row=>row.issuance_id===state.assignment?.issuance_id);
    function historyRowFor(a){return {issuance_id:a.issuance_id,exercise_id:a.prompt.exercise_id,title:a.prompt.presentation.title,support_history:a.support_history,context_exposure:a.context_exposure,response_count:0,hints:0,feedback:null,hint_history:[],feedback_history:[],receipt:null,last_response_revision:0,last_feedback_revision:0};}
    function restorePublicHistory(current){
      const restored=transport.history?.();
      state.history_authoritative=!!restored;
      state.can_send_new_command=transport.status?.().can_send_new_command!==false;
      state.session_mode=transport.status?.().session_mode||current?.session_mode||'LOCAL_FORMATIVE';
      if(!restored)return;
      const rows=[];let last=null;
      for(const historical of restored.rows){
        const a=assignment(historical.assignment),row=historyRowFor(a);last=a;
        for(const item of historical.deliveries){
          const result=item.delivery.result,id=item.delivery.delivery_id;
          if(item.kind==='RESPONSE'){row.receipt=receipt(result,a);row.response_count=row.receipt.response_no;row.last_response_revision=item.ack_revision;}
          else if(item.kind==='HINT'){
            const hint=hintResult(result);row.hint_history.push({delivery_id:id,...hint});
            if(hint.status==='HINT_SHOWN'){row.hints++;row.support_history='GUIDED';seenHelpDeliveries.add(id);}
          }else{
            const feedback=decision(result,a);row.feedback={assessment_status:feedback.assessment_status,evidence_kind:feedback.evidence_kind||null,items:copy(feedback.feedback)};
            row.feedback_history.push({delivery_id:id,...feedback});
            row.last_feedback_revision=item.ack_revision;
          }
        }
        rows.push(row);
      }
      state.history=rows;
      if(!current&&last){state.assignment=last;state.phase='REVIEW';}
      const row=historyRow();
      if(row){
        if(row.receipt){state.receipt=copy(row.receipt);state.restored_receipt_unverified=false;if(state.phase==='ANSWERING')state.phase='RECEIPT';}
        if(row.hint_history.length)state.hint=copy(row.hint_history.at(-1));
        if(row.feedback_history.length&&row.last_feedback_revision>row.last_response_revision){state.feedback=copy(row.feedback_history.at(-1));state.next_assignment=state.feedback.next_assignment;state.phase=state.can_send_new_command?'FEEDBACK':'REVIEW';}
      }
    }
    function binding(event){
      if(!state.assignment)fail('NO_ASSIGNMENT','No active teacher assignment');
      const current=state.assignment;
      if(event.issuance_id!==undefined&&event.issuance_id!==current.issuance_id)fail('STALE_ISSUANCE','Stale issuance');
      if(event.prompt_sha256!==undefined&&event.prompt_sha256!==current.prompt.prompt_sha256)fail('STALE_PROMPT','Stale prompt');
      return copy(current);
    }
    async function connect(){
      if(inflight&&transport.kind==='teacher-session-store-v1')fail('BUSY','Wait for the current store command');
      const generation=++epoch;state.busy=true;state.error=null;emit();
      try{
        const acceptPending=async delivery=>{
          if(generation!==epoch)fail('STALE_RESPONSE','Superseded recovery');
          const result=delivery.result;boundary(result);
          let recovered;
          if(result.status==='RECORDED_PENDING_FEEDBACK')recovered=receipt(result,{issuance_id:result.issuance_id});
          else if(['HINT_SHOWN','PREVIEW_HINT_UNAVAILABLE'].includes(result.status))recovered={...hintResult(result),...boundary(result)};
          else recovered=decision(result,{session_id:delivery.session_id,issuance_id:result.from_issuance_id||null});
          state.recovery={delivery_id:identity(delivery.delivery_id,'delivery id'),result:recovered};emit();
        };
        const current=assignment(await (transport.kind==='teacher-session-store-v1'?transport.current(acceptPending):transport.current()));
        if(generation!==epoch)fail('STALE_RESPONSE','Superseded connection');
        activate(current);
        restorePublicHistory(current);
        if(state.recovery?.result.status==='RECORDED_PENDING_FEEDBACK'&&state.recovery.result.issuance_id===current?.issuance_id){state.receipt=copy(state.recovery.result);state.phase='RECEIPT';historyRow().response_count=state.receipt.response_no;}
        if(state.recovery?.result.status==='HINT_SHOWN'){
          state.hint={status:'HINT_SHOWN',text:state.recovery.result.text};
          const row=historyRow(),id=state.recovery.delivery_id;
          if(row&&!seenHelpDeliveries.has(id)){row.hints++;row.support_history='GUIDED';seenHelpDeliveries.add(id);}
        }
        state.busy=false;uncertain=null;state.local_journal_persisted=transport.kind==='teacher-session-store-v1';emit();return expose();
      }
      catch(error){if(generation===epoch)failState(error);throw error;}
    }
    function command(method,args,apply,event){
      const key=args.command_id,signature=stable({method,args});
      const existing=commands.get(key);
      if(existing){if(existing.signature!==signature)fail('COMMAND_CONFLICT','Command id reused with different content');return existing.promise;}
      if(inflight)fail('BUSY','Another command is pending');
      if(uncertain)fail('RETRY_REQUIRED','Resolve the uncertain command first');
      const record={method,args:copy(args),signature,eventSignature:stable(event),apply,generation:epoch,promise:null};
      commands.set(key,record);return send(record);
    }
    function send(record){
      const generation=record.generation;inflight=record;state.busy=true;state.error=null;emit();
      record.promise=Promise.resolve().then(()=>transport[record.method](copy(record.args))).then(async result=>{
        if(generation!==epoch||state.assignment?.issuance_id!==record.args.issuance_id)fail('STALE_RESPONSE','Late response ignored');
        if(!record.applied){record.apply(result);record.applied=true;emit();}
        if(transport.acceptResult)await transport.acceptResult();
        if(generation!==epoch)fail('STALE_RESPONSE','Late acknowledgment ignored');
        if(transport.kind==='teacher-session-store-v1'){
          const phase=state.phase,feedback=state.feedback,next=state.next_assignment;
          restorePublicHistory(state.assignment);
          state.phase=phase;state.feedback=feedback;state.next_assignment=next;
        }
        uncertain=null;state.busy=false;emit();return expose();
      }).catch(error=>{
        if(generation===epoch){
          // I/O errors (including ECONNRESET/TimeoutError) are ambiguous even with a code.
          const definitive=error instanceof ProtocolError||error.definitive===true||['TEACHER_REJECTED','STALE_REVISION','DELIVERY_ACK_REQUIRED','CONFLICTING_COMMAND_ID'].includes(error.code);
          uncertain=definitive?null:record;
          failState(error);
        }
        throw error;
      }).finally(()=>{if(inflight===record)inflight=null;});
      return record.promise;
    }
    function dispatch(event){
      if(!object(event))fail('INVALID_EVENT','Event required');
      if(event.command_id&&commands.has(event.command_id)){
        const previous=commands.get(event.command_id);
        if(previous.eventSignature!==stable(event))fail('COMMAND_CONFLICT','Command id reused with different content');
        // A replay may outlive its issuance; never apply its old result again.
        return previous.promise.then(expose);
      }
      if(event.type==='continue_recovery'){
        if(state.busy)fail('BUSY','Recovery is still pending');
        if(state.error)fail('RECOVERY_INCOMPLETE','Recovery must finish before continuing');
        state.recovery=null;emit();return Promise.resolve(expose());
      }
      if(state.recovery)fail('RECOVERY_REQUIRED','Review the recovered delivery first');
      if(event.type==='retry_delivery'){
        if(inflight) return inflight.promise;
        if(!uncertain)fail('NO_UNCERTAIN_COMMAND','Nothing to resend');
        return send(uncertain);
      }
      if(event.type==='next_task'||event.type==='retry'){
        const current=binding(event),next=state.next_assignment;
        if(state.busy||uncertain)fail('BUSY','Wait for the current command');
        if(!next)fail('NO_TEACHER_ASSIGNMENT','Teacher has not issued a next assignment');
        if(event.type==='retry'&&next.prompt.exercise_id!==current.prompt.exercise_id)fail('NO_TEACHER_RETRY','Teacher did not issue a same-context retry');
        epoch++;activate(copy(next));state.error=null;emit();return Promise.resolve(expose());
      }
      if(!state.can_send_new_command)fail('AWAIT_TEACHER_REVIEW','This session is closed for new commands');
      const current=binding(event);
      const editable=state.phase!=='FEEDBACK'||['CLARIFY_RESPONSE','WAIT_FIRST_RESPONSE'].includes(state.feedback?.status);
      const commandId=event.command_id||options.makeCommandId?.()||root.crypto?.randomUUID?.();
      if(typeof commandId!=='string'||!commandId.length||commandId.length>120)fail('INVALID_COMMAND_ID','Command id required');
      const args={command_id:commandId,issuance_id:current.issuance_id};
      if(event.type==='first_answer'||event.type==='revise_answer'){
        let response=event.response;
        if(current.candidate_preview&&Object.hasOwn(spotChoices,current.prompt.exercise_id)){
          if(!object(response)||Object.keys(response).sort().join(',')!==['first_play','count','explanation'].sort().join(',')||
             !spotChoices[current.prompt.exercise_id].includes(response.first_play)||
             !Number.isInteger(response.count)||![0,1].includes(response.count))fail('INVALID_SPOT_RESPONSE','Use the three authored response fields');
          response={schema:'spot-first-play-response-v2',exercise_id:current.prompt.exercise_id,
            prompt_sha256:current.prompt.prompt_sha256,first_play:response.first_play,
            guaranteed_spot_tricks_from_position:response.count,explanation:text(response.explanation,'explanation',4000)};
          if(!response.explanation.trim())fail('INVALID_SPOT_RESPONSE','Explanation required');
        }else if(typeof response==='string')response=text(response,'response',2000);
        else {
          if(!object(response)||!object(response.answers))fail('INVALID_RESPONSE','Answer payload required');
          response={schema:current.prompt.response_schema,exercise_id:current.prompt.exercise_id,prompt_sha256:current.prompt.prompt_sha256,answers:copy(response.answers),explanation:text(response.explanation,'explanation',2000)};
        }
        args.response=response;
        if(!commands.has(commandId)&&!editable)fail('FEEDBACK_ALREADY_REVEALED','This issuance is closed');
        if(event.type==='first_answer'&&state.receipt)fail('REVISION_REQUIRED','Use a revision event after the first receipt');
        if(event.type==='revise_answer'&&!state.receipt)fail('FIRST_RESPONSE_REQUIRED','Save the first answer before revising');
        return command('record_response',args,result=>{
          state.receipt=receipt(result,current);state.phase='RECEIPT';state.feedback=null;state.next_assignment=null;state.restored_receipt_unverified=false;
          const row=historyRow();row.response_count=state.receipt.response_no;row.feedback=null;
        },event);
      }
      if(event.type==='hint'){
        if(!commands.has(commandId)&&!editable)fail('CLOSED_ISSUANCE','Hint requested for a closed issuance');
        return command('request_hint',args,result=>{
          state.hint=hintResult(result);
          if(state.hint.status==='HINT_SHOWN'){historyRow().hints++;historyRow().support_history='GUIDED';seenHelpDeliveries.add(commandId);}
        },event);
      }
      if(event.type==='reveal_feedback'){
        if(!commands.has(commandId)&&(!state.receipt||!editable))fail('NO_PENDING_RESPONSE','Save a response before feedback');
        return command('advance',args,result=>{
          const value=decision(result,current);state.feedback=value;state.next_assignment=value.next_assignment;state.phase='FEEDBACK';state.restored_receipt_unverified=false;
          historyRow().feedback={assessment_status:value.assessment_status,evidence_kind:value.evidence_kind||null,items:copy(value.feedback)};
        },event);
      }
      fail('UNKNOWN_EVENT','Unsupported UI event');
    }
    function snapshot(){
      if(inflight||uncertain||state.busy)fail('UNSETTLED_SNAPSHOT','Cannot snapshot an uncertain command');
      if(!state.assignment)fail('NO_ASSIGNMENT','No assignment to snapshot');
      // Deliberately no raw answers, explanations, private journal or command payload.
      if(transport.kind==='teacher-session-store-v1')return {schema:'bridge-ui-store-snapshot-v1',session_id:state.assignment.session_id,issuance_id:state.assignment.issuance_id,prompt_sha256:state.assignment.prompt.prompt_sha256,receipt:copy(state.receipt)};
      return {schema:'bridge-ui-safe-snapshot-v1',assignment:copy(state.assignment),phase:state.phase,receipt:copy(state.receipt),feedback:copy(state.feedback),next_assignment:copy(state.next_assignment),hint:copy(state.hint)};
    }
    async function restore(value){
      if(transport.kind==='teacher-session-store-v1'){
        if(!object(value)||value.schema!=='bridge-ui-store-snapshot-v1'||Object.keys(value).some(k=>!['schema','session_id','issuance_id','prompt_sha256','receipt'].includes(k)))fail('UNSAFE_SNAPSHOT','Unsupported store snapshot');
        identity(value.session_id,'session id');identity(value.issuance_id,'issuance id');hash(value.prompt_sha256);
        const restoredReceipt=value.receipt===null?null:receipt(value.receipt,value);
        if(restoredReceipt&&stable(restoredReceipt)!==stable(value.receipt))fail('UNSAFE_SNAPSHOT','Unexpected receipt fields');
        // Pin the intended session before any pending delivery can be acknowledged.
        transport.pinSession(value.session_id);
        await connect();
        const currentSession=transport.status?.().session_id||state.assignment?.session_id;
        if(currentSession!==value.session_id)fail('SESSION_MISMATCH','Snapshot belongs to another session');
        if(!state.history_authoritative&&!state.recovery&&restoredReceipt&&value.issuance_id===state.assignment?.issuance_id&&value.prompt_sha256===state.assignment?.prompt.prompt_sha256){state.receipt=restoredReceipt;state.phase='RECEIPT';state.restored_receipt_unverified=true;}
        state.recovery_notice=state.history_authoritative?'Из журнала восстановлены задание, квитанции, подсказки и обратная связь. Неотправленный черновик не сохранён.':'Восстановлено текущее задание. Подтверждённая история недоступна этому подключению.';
        emit();return expose();
      }
      if(!object(value)||value.schema!=='bridge-ui-safe-snapshot-v1'||Object.keys(value).some(k=>!['schema','assignment','phase','receipt','feedback','next_assignment','hint'].includes(k)))fail('UNSAFE_SNAPSHOT','Unsupported snapshot fields');
      // Closed feedback needs an authoritative persistence projection that does not yet exist.
      if(!['ANSWERING','RECEIPT'].includes(value.phase)||value.feedback!==null||value.next_assignment!==null)fail('AUTHORITATIVE_RESTORE_REQUIRED','Feedback restore requires the teacher persistence interface');
      if(value.hint!==null)fail('AUTHORITATIVE_RESTORE_REQUIRED','Restoring help history requires the teacher persistence interface');
      const normalized=normalizedAssignment(value.assignment);
      if((value.phase==='RECEIPT')!==(value.receipt!==null))fail('UNSAFE_SNAPSHOT','Receipt and phase disagree');
      const restoredReceipt=value.receipt===null?null:receipt(value.receipt,normalized);
      if(restoredReceipt&&stable(restoredReceipt)!==stable(value.receipt))fail('UNSAFE_SNAPSHOT','Unexpected receipt fields');
      await connect();
      if(!state.assignment||stable(normalized)!==stable(state.assignment))fail('STALE_SNAPSHOT','Snapshot does not match the current public assignment');
      if(restoredReceipt){state.receipt=restoredReceipt;state.phase='RECEIPT';state.restored_receipt_unverified=true;}
      state.error=null;emit();return expose();
    }
    function normalizedAssignment(value){
      // Snapshots already contain the public projection; round-trip through the same whitelist.
      if(!object(value)||!object(value.prompt))fail('UNSAFE_SNAPSHOT','Missing public assignment');
      const p=value.prompt.presentation;
      if(!object(p)||Object.keys(p).some(k=>!['title','hand','dummy','target_suit','questions','author_label','strain','entry_suit','opening_lead','lead','explanation_prompt','limitation','opening_choices','conditions','task_text','position_kind','cards_per_hand','trick_index','given_information'].includes(k)))fail('UNSAFE_SNAPSHOT','Unexpected prompt fields');
      const raw=copy(value);delete raw.prompt.presentation.conditions;
      for(const condition of p.conditions||[]){if(!['future_given','given_split','supplied_play','after_supplied_play','given_conditions','supplied_probe','contrast'].includes(condition.kind))fail('UNSAFE_SNAPSHOT','Unexpected condition');raw.prompt.presentation[condition.kind]=copy(condition);}
      const clean=assignment(raw);
      if(stable(clean)!==stable(value))fail('UNSAFE_SNAPSHOT','Snapshot contains nonpublic fields');
      return clean;
    }
    return {connect,dispatch,getState:expose,snapshot,restore,subscribe(listener){listeners.add(listener);listener(expose());return()=>listeners.delete(listener);}};
  }
  const api={createClient,ProtocolError};
  if(typeof module!=='undefined')module.exports=api;
  else root.BridgeTeacherClient=api;
})(typeof globalThis!=='undefined'?globalThis:this);
