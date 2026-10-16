'use strict';
(function(root){
  const clientAPI=typeof module!=='undefined'?require('./teacher-client.js'):root.BridgeTeacherClient;
  const spotAPI=typeof module!=='undefined'?require('./spot-learner.js'):root.BridgeSpotLearner;
  const reviewAPI=typeof module!=='undefined'?require('./teacher-review.js'):root.BridgeTeacherReview;
  const suits={S:'♠',H:'♥',D:'♦',C:'♣'};
  const suitNames={S:'пики',H:'червы',D:'бубны',C:'трефы'};
  const locations={HAND:'рука',DUMMY:'стол',FROM:'ходим',TO:'принимаем'};
  const cardLabel=card=>suits[card[0]]+(card.slice(1)==='T'?'10':card.slice(1));
  function createUI(document,transport,options={}){
    const client=clientAPI.createClient(transport,options);
    const get=id=>document.getElementById(id);
    const el=(tag,content,className)=>{const node=document.createElement(tag);if(content!==undefined)node.textContent=content;if(className)node.className=className;return node;};
    let issuance=null,pages=[],page=0,summaryOpen=false,returnFocus=null,editing=false,localError=null,requiresPurpose=false,spotCard=null,reviewKey=null,reviewGeneration=0;
    let lastState=client.getState();
    const controls=new Map();
    function hand(container,value){
      container.replaceChildren();
      for(const suit of ['S','H','D','C']){
        if(!Object.hasOwn(value,suit))continue;
        const row=el('div',undefined,'partial-suit');
        row.setAttribute('aria-label',suitNames[suit]);
        const cards=el('div',undefined,'cards');
        if(!value[suit].length)cards.append(el('span',suits[suit]+' —'));
        for(const rank of value[suit]){
          const card=el('span',undefined,'playing-card'+(['H','D'].includes(suit)?' red-card':''));
          card.setAttribute('aria-label',suitNames[suit]+' '+(rank==='T'?'10':rank));
          card.append(el('span',rank==='T'?'10':rank),el('b',suits[suit]));cards.append(card);
        }
        row.append(cards);container.append(row);
      }
    }
    function compactHand(value){return Object.entries(value).map(([s,r])=>suits[s]+(r.map(v=>v==='T'?'10':v).join(' ')||'—')).join(' · ');}
    function select(id,label,values){
      const wrap=el('div',undefined,'transport-field'),caption=el('label',label),input=el('select');
      caption.htmlFor=id;input.id=id;
      for(const [value,text] of [['','Выбери ответ'],...values]){const option=el('option',text);option.value=value;input.append(option);}
      wrap.append(caption,input);return {wrap,input};
    }
    function field(q,p){
      const id='answer-'+q.id;
      if(q.type==='card_pair'){
        const group=el('fieldset',undefined,'transport-pair');group.append(el('legend',q.prompt));
        const parts=q.pair_order.map((place,index)=>{
          const hands=place==='HAND'?[p.hand]:place==='DUMMY'?[p.dummy]:[p.hand,p.dummy];
          const values=hands.flatMap(h=>Object.entries(h).filter(([s])=>!p.entry_suit||s===p.entry_suit).flatMap(([s,r])=>r.map(rank=>[s+rank,cardLabel(s+rank)])));
          const part=select(id+'-'+index,locations[place]||place,values);group.append(part.wrap);return part.input;
        });
        controls.set(q.id,{question:q,inputs:parts});return group;
      }
      if(q.type==='integer'){
        const wrap=el('div',undefined,'transport-field'),caption=el('label',q.prompt),input=el('input');
        caption.htmlFor=id;input.id=id;input.type='number';input.min=q.minimum;input.max=q.maximum;
        input.setAttribute('min',q.minimum);input.setAttribute('max',q.maximum);input.setAttribute('step','1');input.setAttribute('inputmode','numeric');
        wrap.append(caption,input);controls.set(q.id,{question:q,inputs:[input]});return wrap;
      }
      const values=q.type==='boolean'?[['true','Да'],['false','Нет']]:q.options.map(o=>[o.id,o.label]);
      const part=select(id,q.prompt,values);controls.set(q.id,{question:q,inputs:[part.input]});return part.wrap;
    }
    function buildAssignment(a){
      issuance=a.issuance_id;page=0;editing=false;summaryOpen=false;pages=[];controls.clear();
      requiresPurpose=a.candidate_preview&&a.prompt.exercise_id.startsWith('spot.');
      spotCard=requiresPurpose?spotAPI.cardFor(options.spotPresentation,a):null;
      reviewKey=null;reviewGeneration++;get('saved-spot-answer').hidden=true;
      get('retry-saved-answer').hidden=true;
      const p=a.prompt.presentation;
      get('exercise-title').textContent=p.title;get('question-heading').textContent='Твой ответ';
      get('author-label').textContent=p.author_label||'Авторское учебное задание';
      get('contract-label').textContent=p.strain==='NT'?'Без козыря':(p.strain||'Учебная позиция');
      get('target-suit').textContent='Масть: '+suits[p.target_suit];
      get('opening-lead').textContent=p.trick_index===9?'До хода в девятой взятке · ход: '+(locations[p.lead]||p.lead):p.opening_lead?'Первый ход: '+cardLabel(p.opening_lead):(p.lead?'Ход: '+(locations[p.lead]||p.lead):'Условия хода указаны ниже');
      get('task-limitation').textContent=a.candidate_preview?'По пять оставшихся карт с каждой стороны. План и число гарантированных фосочных взяток 9–13 проверяет преподаватель.':p.limitation||'';
      hand(get('dummy-cards'),p.dummy);hand(get('hand-cards'),p.hand);
      const conditions=get('conditions');conditions.replaceChildren();
      if(p.task_text)conditions.append(el('li',p.task_text));
      if(p.given_information){
        conditions.append(el('li','По условию у защитников осталось 2–2 карты целевой масти.'));
        conditions.append(el('li','Уже сыграны в целевой масти: '+p.given_information.played_in_target_suit.map(cardLabel).join(', ')+'.'));
      }
      if(p.opening_choices)for(const choice of p.opening_choices)conditions.append(el('li','Вариант первой взятки: '+cardLabel(choice.hand)+' из руки, '+cardLabel(choice.dummy)+' со стола; берёт '+(locations[choice.winner]||choice.winner)+'.'));
      for(const c of p.conditions){
        const li=el('li',c.text);
        if(c.hand)li.append(el('p','Рука: '+compactHand(c.hand)));
        if(c.dummy)li.append(el('p','Стол: '+compactHand(c.dummy)));
        if(c.lead)li.append(el('p','Ход: '+(locations[c.lead]||c.lead)));
        conditions.append(li);
      }
      const fields=get('question-fields');fields.replaceChildren();
      const authored=get('authored-spot-presentation');
      authored.hidden=!spotCard;authored.textContent=spotCard?.body||'';
      if(spotCard){
        const section=el('div',undefined,'question-page');
        const first=select('spot-first-play','Первый ход',spotCard.options.map(o=>[o.value,o.label]));
        const count=select('spot-count','Гарантированные взятки',[['0','0'],['1','1']]);
        const caption=el('label','Объясните свой выбор');caption.htmlFor='answer-explanation';
        const explanation=el('textarea');explanation.id='answer-explanation';
        explanation.setAttribute('maxlength','4000');explanation.setAttribute('rows','5');
        const purposeError=el('p','Напишите объяснение перед сохранением.','field-help');
        purposeError.id='purpose-error';purposeError.hidden=true;
        section.append(first.wrap,count.wrap,caption,explanation,purposeError);
        pages.push(section);fields.append(section);return;
      }
      // Pagination is presentation only; the teacher supplies every question and option.
      for(let offset=0;offset<p.questions.length;offset+=4){
        const section=el('div',undefined,'question-page');
        for(const q of p.questions.slice(offset,offset+4))section.append(field(q,p));
        pages.push(section);fields.append(section);
      }
      const explainPage=el('div',undefined,'question-page');
      const caption=el('label',requiresPurpose?'\u0413\u0434\u0435 \u043e\u0441\u0442\u0430\u043d\u0435\u0442\u0441\u044f \u0445\u043e\u0434 \u0438 \u043a\u0430\u043a \u043f\u043e\u043f\u0430\u0441\u0442\u044c \u043a \u0444\u043e\u0441\u043a\u0435?':p.explanation_prompt||'Explain your answer');caption.htmlFor='answer-explanation';
      const input=el('textarea');input.id='answer-explanation';input.setAttribute('maxlength','2000');input.setAttribute('rows','5');
      const purposeError=el('p','\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u043e\u0442\u0432\u0435\u0442\u044c \u0441\u0432\u043e\u0438\u043c\u0438 \u0441\u043b\u043e\u0432\u0430\u043c\u0438 \u0438\u043b\u0438 \u0432\u044b\u0431\u0435\u0440\u0438 \u00ab\u041d\u0435 \u0437\u043d\u0430\u044e\u00bb.','field-help');purposeError.id='purpose-error';purposeError.hidden=true;
      explainPage.append(caption,input,purposeError,el('p','Объяснение прочитает преподаватель. Можно сохранить неполный ответ.','field-help'));
      pages.push(explainPage);fields.append(explainPage);
    }
    function collect(){
      if(spotCard)return {first_play:get('spot-first-play').value,
        count:get('spot-count').value===''?null:Number(get('spot-count').value),
        explanation:get('answer-explanation').value};
      const answers={};
      for(const [id,{question:q,inputs}] of controls){
        if(q.type==='card_pair')answers[id]=inputs.map(input=>input.value||null);
        else {const value=inputs[0].value;answers[id]=value===''?null:q.type==='integer'?Number(value):q.type==='boolean'?value==='true':value;}
      }
      return {answers,explanation:get('answer-explanation').value};
    }
    function renderSummary(s){
      const items=get('completion-items');items.replaceChildren();
      get('completion-count').textContent=(s.history_authoritative?'Заданий в восстановленной истории сеанса: ':'Заданий в этой открытой странице: ')+s.history.length+'.';
      for(const row of s.history){
        const item=el('article',undefined,'completion-item');item.append(el('h3',row.title));
        item.append(el('p','Подтверждённых здесь версий ответа: '+row.response_count+'. Подсказок: '+row.hints+'.'));
        for(const hint of row.hint_history||[])item.append(el('p',(hint.status==='HINT_SHOWN'?'Подсказка: ':'Подсказка не предоставлена: ')+hint.text));
        if(row.feedback_history?.length){for(const feedback of row.feedback_history){if(feedback.message)item.append(el('p',feedback.message));for(const part of feedback.feedback)item.append(el('p',part.text));}}
        else if(row.feedback)for(const part of row.feedback.items)item.append(el('p',part.text));
        else item.append(el('p','Обратная связь ещё не получена.'));
        items.append(item);
      }
      if(s.restored_receipt_unverified)items.append(el('p','Квитанция восстановлена из копии экрана. История преподавателя пока не подтверждена.'));
    }
    function render(s){
      lastState=s;
      if(s.assignment&&s.assignment.issuance_id!==issuance)buildAssignment(s.assignment);
      const available=!!s.assignment;
      const revisable=s.can_send_new_command&&(s.phase!=='FEEDBACK'||['CLARIFY_RESPONSE','WAIT_FIRST_RESPONSE'].includes(s.feedback?.status));
      const showForm=available&&revisable&&(!s.receipt||editing||s.phase==='FEEDBACK');
      get('connection-status').textContent=s.busy?'Ожидаем ответ преподавателя…':available?'Задание получено от преподавателя.':s.phase==='REVIEW'?'Занятие ожидает преподавателя.':'Задание пока не подключено.';
      get('connection-status').setAttribute('aria-busy',String(s.busy));
      if(!s.busy&&!s.can_send_new_command&&!s.recovery)get('connection-status').textContent='Ответы сохранены. Ожидаем ручную проверку преподавателя.';
      get('preview-label').hidden=s.session_mode!=='SPOT_CANDIDATE_PREVIEW_V1';
      get('storage-note').textContent=s.local_journal_persisted?'Ответы сохраняются в локальном журнале преподавателя. Черновик до отправки остаётся только на этой странице.':'Этот сеанс хранится в памяти. Не обновляй страницу до окончания работы.';
      get('answer-form').hidden=!showForm;get('partial-response-actions').hidden=!showForm||!!spotCard;
      for(const node of get('question-fields').querySelectorAll('input,select,textarea'))node.disabled=s.busy||!!s.error?.can_retry||!showForm;
      pages.forEach((section,index)=>{section.hidden=index!==page;});
      get('question-progress').textContent=available?'Часть '+(page+1)+' из '+pages.length:'';
      get('back-button').hidden=page===0;get('check-button').textContent=page<pages.length-1?'Далее →':s.receipt?'Сохранить новую версию':'Сохранить ответ';
      get('response-receipt').hidden=!s.receipt||!!s.feedback;
      get('receipt-copy').textContent=s.restored_receipt_unverified?'Восстановлена копия квитанции. Сохранность ответа у преподавателя ещё не подтверждена.':s.receipt?.message||'';
      get('receipt-version').textContent=s.receipt?'Версия ответа: '+s.receipt.response_no+'.':'';
      get('revise-response').hidden=editing||!s.can_send_new_command;
      get('reveal-feedback').hidden=editing||!s.receipt||!revisable||s.phase==='FEEDBACK';
      get('reveal-feedback').textContent=s.assignment?.candidate_preview?'Передать на ручную проверку':'Показать обратную связь';
      get('hint-button').hidden=!available||!revisable||(requiresPurpose&&!s.receipt);
      get('hint').hidden=!s.hint;get('hint').textContent=s.hint?.text||'';
      get('hint-button').setAttribute('aria-expanded',String(!!s.hint));
      get('feedback').hidden=!s.feedback;
      get('feedback-title').textContent=s.feedback?.status==='AWAIT_TEACHER_REVIEW'?'Ожидаем проверки преподавателя':s.feedback?.assessment_status==='ABSTAIN'?'Нужно уточнить ответ':'Обратная связь по заданию';
      get('feedback-copy').textContent=s.feedback?.message||'';
      get('feedback-fields').replaceChildren();for(const item of s.feedback?.feedback||[])get('feedback-fields').append(el('li',item.text));
      const saved=get('saved-spot-answer');
      if(!spotCard||s.feedback?.status!=='AWAIT_TEACHER_REVIEW'){
        saved.hidden=true;get('retry-saved-answer').hidden=true;
      }
      else {
        saved.hidden=false;
        const key=s.assignment?.issuance_id;
        if(reviewKey!==key){
          const generation=++reviewGeneration;
          const cardId=spotCard.exercise_id;
          const sessionId=s.assignment.session_id;
          reviewKey=key;saved.textContent='Загружаем сохранённый ответ. Ожидается проверка преподавателя.';
          get('retry-saved-answer').hidden=true;
          Promise.resolve().then(()=>options.loadSavedAnswer()).then(value=>{
            const answer=reviewAPI.normalizeReview(value);
            if(answer.issuance_id!==key||answer.exercise_id!==cardId||
               answer.session_id!==sessionId)throw new Error('STALE_REVIEW');
            if(generation!==reviewGeneration)return;
            saved.textContent=`Сохранённый ответ: первый ход ${answer.first_play}; взяток ${answer.guaranteed}; объяснение: ${answer.explanation}. Ожидается проверка преподавателя.`;
          }).catch(()=>{
            if(generation!==reviewGeneration)return;
            saved.textContent='Сохранённый ответ пока недоступен. Ожидается проверка преподавателя.';
            get('retry-saved-answer').hidden=false;
          });
        }
      }
      get('next-exercise').hidden=!s.next_assignment;
      get('next-exercise').textContent=s.next_assignment?.prompt.exercise_id===s.assignment?.prompt.exercise_id?'Повторить задание с учётом разбора':'Открыть следующее задание';
      const error=localError||s.error;
      get('transport-error').hidden=!error;
      get('transport-error').textContent=error?'Не удалось завершить действие ('+error.code+'). '+(error.can_retry?'Повтори доставку того же ответа или запроса.':'Продолжение требует проверки подключения преподавателем.'):'';
      get('retry-delivery').hidden=!s.error?.can_retry;
      get('resume-session').hidden=!error||!!error.can_retry;get('resume-session').disabled=s.busy;
      for(const id of ['back-button','check-button','save-partial','dont-know','revise-response','reveal-feedback','hint-button','next-exercise'])get(id).disabled=s.busy||!!s.error?.can_retry||!s.can_send_new_command;
      get('retry-delivery').disabled=s.busy;
      get('finish-session').hidden=!available;
      get('lesson-work').hidden=summaryOpen||!!s.recovery;get('lesson-completion').hidden=!summaryOpen||!!s.recovery;
      get('recovered-delivery').hidden=!s.recovery;get('continue-recovery').disabled=s.busy||!!s.error;
      const recovered=get('recovered-content');recovered.replaceChildren();
      if(s.recovery){const result=s.recovery.result;recovered.append(el('p',result.message||result.text||'Восстановлена обратная связь преподавателя.'));for(const item of result.feedback||[])recovered.append(el('p',item.text));}
      get('recovery-notice').hidden=!s.recovery_notice;get('recovery-notice').textContent=s.recovery_notice||'';
      if(summaryOpen)renderSummary(s);
    }
    async function send(type,response){
      localError=null;
      const s=client.getState();
      const event={type,issuance_id:s.assignment?.issuance_id,prompt_sha256:s.assignment?.prompt.prompt_sha256};
      if(response!==undefined)event.response=response;
      try{
        const next=await client.dispatch(event);
        if(type==='first_answer'||type==='revise_answer'){editing=false;render(next);get('receipt-heading').focus();}
        else if(type==='reveal_feedback')get('feedback-title').focus();
        else if(type==='next_task'||type==='retry')get('question-heading').focus();
        return next;
      }catch(error){localError={code:error.code||'TRANSPORT_ERROR',can_retry:!!client.getState().error?.can_retry};render(client.getState());return null;}
    }
    function save(response){
      if(spotCard){
        try{response=spotAPI.responseFrom(spotCard,response.first_play,response.count,response.explanation);}
        catch(_error){localError={code:'INVALID_SPOT_RESPONSE',can_retry:false};render(client.getState());return Promise.resolve(null);}
      }
      if(requiresPurpose&&typeof response==='object'&&!response.explanation.trim()){
        page=pages.length-1;render(client.getState());get('purpose-error').hidden=false;
        get('answer-explanation').focus();return Promise.resolve(null);
      }
      return send(client.getState().receipt?'revise_answer':'first_answer',response);
    }
    function changePage(delta){page=Math.max(0,Math.min(pages.length-1,page+delta));render(client.getState());get('question-heading').focus();}
    get('answer-form').addEventListener('submit',event=>{
      event.preventDefault();if(get('answer-form').hidden||get('check-button').disabled)return;
      if(page<pages.length-1)changePage(1);else void save(collect());
    });
    get('back-button').addEventListener('click',()=>changePage(-1));
    get('save-partial').addEventListener('click',()=>{if(!get('partial-response-actions').hidden)void save(collect());});
    get('dont-know').addEventListener('click',()=>{if(!get('partial-response-actions').hidden)void save('Не знаю');});
    get('revise-response').addEventListener('click',()=>{editing=true;render(client.getState());get('question-heading').focus();});
    get('reveal-feedback').addEventListener('click',()=>{if(!get('reveal-feedback').hidden)void send('reveal_feedback');});
    get('hint-button').addEventListener('click',()=>{if(!get('hint-button').hidden)void send('hint');});
    get('next-exercise').addEventListener('click',()=>{if(!get('next-exercise').hidden)void send(lastState.next_assignment.prompt.exercise_id===lastState.assignment.prompt.exercise_id?'retry':'next_task');});
    get('retry-delivery').addEventListener('click',async()=>{
      if(get('retry-delivery').hidden)return;localError=null;
      try{await client.dispatch({type:'retry_delivery'});editing=false;render(client.getState());}
      catch(error){localError={code:error.code||'TRANSPORT_ERROR',can_retry:!!client.getState().error?.can_retry};render(client.getState());}
    });
    get('resume-session').addEventListener('click',async()=>{
      if(get('resume-session').hidden)return;localError=null;
      try{await client.connect();render(client.getState());}
      catch(error){localError={code:error.code||'TRANSPORT_ERROR',can_retry:false};render(client.getState());}
    });
    get('retry-saved-answer').addEventListener('click',()=>{
      if(get('retry-saved-answer').hidden)return;
      reviewKey=null;render(client.getState());
    });
    get('finish-session').addEventListener('click',()=>{returnFocus=document.activeElement;summaryOpen=true;render(client.getState());get('completion-heading').focus();});
    get('completion-return').addEventListener('click',()=>{summaryOpen=false;render(client.getState());(returnFocus||get('question-heading')).focus();});
    get('continue-recovery').addEventListener('click',()=>{if(!get('recovered-delivery').hidden)void send('continue_recovery');});
    client.subscribe(render);
    function snapshot(){return {schema:'bridge-lesson-ui-snapshot-v1',client:client.snapshot(),page,summary_open:summaryOpen};}
    async function restore(value){
      if(!value||value.schema!=='bridge-lesson-ui-snapshot-v1'||Object.keys(value).some(k=>!['schema','client','page','summary_open'].includes(k))||!Number.isInteger(value.page)||value.page<0||typeof value.summary_open!=='boolean')throw new clientAPI.ProtocolError('UNSAFE_SNAPSHOT','Invalid UI snapshot');
      await client.restore(value.client);
      page=value.client.issuance_id&&value.client.issuance_id!==client.getState().assignment?.issuance_id?0:Math.min(value.page,Math.max(0,pages.length-1));summaryOpen=value.summary_open;editing=false;localError=null;render(client.getState());
      return client.getState();
    }
    const ready=(options.snapshot?restore(options.snapshot):client.connect()).catch(error=>{localError={code:error.code||'TRANSPORT_ERROR',can_retry:false};render(client.getState());throw error;});
    return {client,ready,snapshot,restore};
  }
  let mounted=null;
  const api={createUI,mountTransport(transport,options={}){if(mounted)throw new Error('A transport is already mounted on this page');mounted=createUI(root.document,transport,options);return mounted;},mountSessionStore(store,options={}){return api.mountTransport(root.BridgeStoreTransport.createStoreTransport(store),options);}};
  if(typeof module!=='undefined')module.exports=api;
  else root.BridgeLesson=api;
})(typeof globalThis!=='undefined'?globalThis:this);
