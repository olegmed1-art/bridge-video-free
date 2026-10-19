'use strict';
(function (root) {
  const IDS = new Set(['diana19-q-001', 'diana19-q-002', 'diana19-q-003',
    'diana19-visible-exercise-001', 'diana19-visible-exercise-002']);
  const card = value => (value.slice(0, -1)==='T'?'10':value.slice(0, -1)) +
    ({S:'♠',H:'♥',D:'♦',C:'♣'}[value.slice(-1)] || '');
  function mount(document, transport, idFactory) {
    const el = id => document.getElementById(id);
    let current = null, ticket = 0, pending = null;
    const picker = el('lesson-picker');
    function lines(id, values, tag='p') {
      const parent = el(id); parent.replaceChildren();
      for (const value of values) { const child=document.createElement(tag); child.textContent=value; parent.appendChild(child); }
    }
    function render(view) {
      const q=view.question;
      if (!IDS.has(q.lesson_id) || view.automatic_assessment !== false) throw new Error('Invalid lesson');
      current=view;
      el('lesson-content').hidden=false;
      el('lesson-title').textContent=q.title_ru;
      el('lesson-question').textContent=q.question_ru;
      el('visible-holdings').textContent=Object.entries(q.visible_holdings).map(([seat, cards])=>seat+': '+cards.map(card).join(' ')).join('\n');
      lines('lesson-conditions',q.conditions_ru,'li');
      lines('lesson-options',q.options.map((option,i)=>option.option_id+' (ответ '+(i+1)+'): '+option.cards.map(card).join(' ')+'; сыграть '+card(option.play)));
      const saved=!!view.saved;
      const complete=view.stage?view.stage.complete:saved;
      if (typeof complete!=='boolean' || (view.stage && !['initial','followup'].includes(view.stage.stage_id))) throw new Error('Invalid stage');
      el('answer-form').hidden=complete;
      el('saved-response').hidden=!saved;
      el('lesson-explanation').hidden=!complete;
      el('learner-answer').value='';
      el('saved-answer').textContent=!saved?'':view.saved_answers?view.saved_answers.map((item,i)=>
        'Ответ '+(i+1)+': '+item.answer).join('\n\n'):view.saved.answer;
      if (saved && view.saved.assessment !== null) throw new Error('Invalid saved lesson');
      if (complete) {
        if (!saved || !view.explanation) throw new Error('Invalid completed lesson');
        lines('explanation-text',view.explanation.paragraphs);
        const source=view.explanation.source;
        el('source-link').removeAttribute('href');
        el('source-link').hidden=!source.video_url;
        if (source.video_url) {
          const sourceUrl=new URL(source.video_url);
          if (sourceUrl.protocol !== 'https:' || sourceUrl.hostname !== 'drive.google.com' || sourceUrl.username || sourceUrl.password) throw new Error('Invalid source');
          el('source-link').href=sourceUrl.href;
        }
        el('source-timecode').textContent='Таймкод: '+source.timecode_start+' — '+source.timecode_end;
        el('explanation-scope').textContent=view.explanation.scope_notice+' Ссылка на видео взята из исходных метаданных.';
      } else {
        if (view.explanation !== null) throw new Error('Premature explanation');
        lines('explanation-text',[]);
        el('source-timecode').textContent=''; el('source-link').removeAttribute('href');
        el('explanation-scope').textContent='';
      }
      el('save-answer').textContent='Сохранить ответ и продолжить';
      el('diana-status').textContent=complete?'Ответ сохранён. Разбор доступен.':
        saved?'Первый ответ сохранён. Продолжайте с подсказкой; этот этап не является самостоятельной оценкой.':'Сначала запишите свой ответ.';
    }
    async function select() {
      const mine=++ticket; current=null; pending=null;
      el('lesson-content').hidden=true;
      el('diana-status').textContent='Загрузка вопроса…';
      const id=picker.value;
      if (!IDS.has(id)) throw new Error('Unknown lesson');
      const view=await transport.view(id);
      if (mine===ticket) render(view);
    }
    async function submit(event) {
      event.preventDefault();
      if (!current || (current.stage?current.stage.complete:current.saved)) return;
      const answer=el('learner-answer').value;
      if (!answer.trim() || answer.length>4000) {el('diana-status').textContent='Введите ответ длиной до 4000 знаков.';return;}
      const id=current.question.lesson_id;
      if (!pending || pending.lesson_id!==id || pending.message.answer!==answer ||
          pending.message.question_sha256!==current.question_sha256) pending={lesson_id:id,
        message:{request_id:idFactory(),question_sha256:current.question_sha256,answer,
          ...(current.stage?{stage_id:current.stage.stage_id}:{})}};
      const mine=ticket;
      picker.disabled=true;el('save-answer').disabled=true;el('learner-answer').disabled=true;
      el('diana-status').textContent='Сохранение ответа…';
      try {
        const view=await transport.answer(id,pending.message);
        if (mine===ticket) {render(view);pending=null;}
      } catch (error) {
        el('diana-status').textContent='Не удалось подтвердить сохранение. Повторите отправку или заново выберите вопрос.';
      } finally {
        picker.disabled=false;el('save-answer').disabled=false;el('learner-answer').disabled=false;
      }
    }
    picker.addEventListener('change',()=>void select().catch(()=>{el('diana-status').textContent='Вопрос временно недоступен.';}));
    el('answer-form').addEventListener('submit',event=>void submit(event));
    const ready=(async()=>{
      const catalog=await transport.catalog();
      if (catalog.status!=='STAGING' || !Array.isArray(catalog.lessons) ||
          !catalog.lessons.length || catalog.lessons.length>IDS.size ||
          new Set(catalog.lessons.map(item=>item.lesson_id)).size!==catalog.lessons.length) throw new Error('Invalid catalog');
      for (const lesson of catalog.lessons) {
        if(!IDS.has(lesson.lesson_id)) throw new Error('Unknown lesson');
        const option=document.createElement('option');option.value=lesson.lesson_id;option.textContent=lesson.title_ru;picker.appendChild(option);
      }
      picker.value=catalog.lessons[0].lesson_id;
      await select();
    })();
    return {ready,select,submit};
  }
  function browserTransport(document, window) {
    const host=document.getElementById('diana19-host');
    const config=host?JSON.parse(host.textContent):{catalog:'/school/lessons/diana19/catalog',lesson:'/school/lessons/diana19/lesson/',answer:'/school/lessons/diana19/answer/'};
    function endpoint(kind,id='') {
      if(kind!=='catalog' && !IDS.has(id)) throw new Error('Unknown lesson');
      const url=new URL(config[kind]+id,window.location.href);
      if (url.origin!==window.location.origin || url.username || url.password) throw new Error('Invalid endpoint');
      const route=url.pathname==='/index.php'?url.searchParams.get('route'):url.pathname.replace('/school/lessons/','practice/');
      if(route!==('practice/diana19/'+kind+(kind==='catalog'?'':'/'+id))) throw new Error('Invalid route');
      return url.href;
    }
    async function call(kind,id,body) {
      const headers={};
      if(body){headers['Content-Type']='application/json';headers['X-CSRF-Token']=document.querySelector('meta[name="bridge-school-csrf-token"]').content;}
      const response=await window.fetch(endpoint(kind,id),{method:body?'POST':'GET',headers,credentials:'same-origin',cache:'no-store',body:body?JSON.stringify(body):undefined});
      if(!response.ok)throw new Error('Lesson unavailable');
      return response.json();
    }
    return {catalog:()=>call('catalog'),view:id=>call('lesson',id),answer:(id,message)=>call('answer',id,message)};
  }
  const api={mount,browserTransport};
  if(typeof module==='object' && module.exports)module.exports=api;
  else {root.Diana19Lesson=api;void mount(root.document,browserTransport(root.document,root),()=>root.crypto.randomUUID()).ready.catch(()=>{root.document.getElementById('diana-status').textContent='Уроки временно недоступны.';});}
})(typeof window==='undefined'?globalThis:window);
