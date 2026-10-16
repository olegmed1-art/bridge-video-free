'use strict';
(function(root){
  const ARTIFACT_SHA256='88d8af64b387cf79294a6084d472fc5f6f0373d4c5febe7188be7883fde14cde';
  const SOURCE_SHA256={
    'spot.a7c2':'2932b39752d111ffa02d8c65be7a57b33d7f7b89195e69d5a9025143177533a4',
    'spot.f3b8':'712780972476641e2fdc3b9d5d9c9326fb7e202e516d5213d545622930cb963c'
  };
  const OPTIONS={'spot.a7c2':['DA','H2'],'spot.f3b8':['CA','HA']};
  const SUITS={S:'\u2660',H:'\u2665',D:'\u2666',C:'\u2663'};
  const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const keys=(value,expected)=>plain(value)&&Object.keys(value).sort().join(',')===expected.slice().sort().join(',');
  function reject(){throw new Error('INVALID_LEARNER_PRESENTATION');}
  function label(card){return SUITS[card[0]]+card.slice(1);}
  function normalize(value){
    if(!keys(value,['schema','status','cards'])||value.schema!=='two-spot-local-learner-presentation-v1'||
       value.status!=='CANDIDATE_NOT_DELIVERED'||!Array.isArray(value.cards)||value.cards.length!==2)reject();
    const seen=new Set();
    for(const card of value.cards){
      if(!keys(card,['exercise_id','source_sha256','presentation'])||!Object.hasOwn(SOURCE_SHA256,card.exercise_id)||seen.has(card.exercise_id)||card.source_sha256!==SOURCE_SHA256[card.exercise_id])reject();
      seen.add(card.exercise_id);
      const p=card.presentation;
      if(!keys(p,['format','body','inputs'])||p.format!=='markdown'||typeof p.body!=='string'||!p.body.trim()||p.body.length>6000||
         !keys(p.inputs,['first_play','count','explanation']))reject();
      const i=p.inputs;
      if(!keys(i.first_play,['type','options','required'])||i.first_play.type!=='choice'||i.first_play.required!==true||
         !Array.isArray(i.first_play.options)||i.first_play.options.join('|')!==OPTIONS[card.exercise_id].map(label).join('|')||
         !keys(i.count,['type','options','required'])||i.count.type!=='choice'||i.count.required!==true||
         !Array.isArray(i.count.options)||i.count.options.join('|')!=='0|1'||
         !keys(i.explanation,['type','max_length','required'])||i.explanation.type!=='text'||
         i.explanation.required!==true||i.explanation.max_length!==4000)reject();
    }
    return value;
  }
  async function loadPresentation(fetcher=root.fetch?.bind(root),crypto=root.crypto){
    if(typeof fetcher!=='function'||!crypto?.subtle)reject();
    const response=await fetcher('./learner-presentation.json',{cache:'no-store'});
    if(!response?.ok)reject();
    const bytes=await response.arrayBuffer();
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
    if(hash!==ARTIFACT_SHA256)reject();
    return normalize(JSON.parse(new TextDecoder().decode(bytes)));
  }
  function cardFor(artifact,assignment){
    normalize(artifact);
    if(!assignment?.candidate_preview||!Object.hasOwn(SOURCE_SHA256,assignment.prompt?.exercise_id))return null;
    const id=assignment.prompt.exercise_id;
    const card=artifact.cards.find(row=>row.exercise_id===id);
    const hand=assignment.prompt.presentation?.hand;
    if(!plain(hand)||OPTIONS[id].some(choice=>!Array.isArray(hand[choice[0]])||!hand[choice[0]].includes(choice.slice(1))))reject();
    return {exercise_id:id,body:card.presentation.body,options:OPTIONS[id].map(choice=>({value:choice,label:label(choice)}))};
  }
  function responseFrom(card,first_play,count,explanation){
    if(!card||!Object.hasOwn(OPTIONS,card.exercise_id)||!OPTIONS[card.exercise_id].includes(first_play)||
       !Number.isInteger(count)||![0,1].includes(count)||typeof explanation!=='string'||
       !explanation.trim()||explanation.length>4000)throw new Error('INVALID_SPOT_RESPONSE');
    return {first_play,count,explanation};
  }
  const api={loadPresentation,cardFor,responseFrom,normalize};
  if(typeof module!=='undefined')module.exports=api;else root.BridgeSpotLearner=api;
})(typeof globalThis!=='undefined'?globalThis:this);
