from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import os
import re
import statistics
import zipfile
from pathlib import Path

ORDER = ['S','W','N','E']
SEATS = 'NESW'
SUITS = 'SHDC'
RANKS = 'AKQJT98765432'
RANK_VALUE = {r: 14-i for i,r in enumerate(RANKS)}
DECK = {s+r for s in SUITS for r in RANKS}
PARTNER = {'S':'N','N':'S','W':'E','E':'W'}
VUL_MAP = {'o':'None','n':'NS','e':'EW','b':'Both','0':'None'}
STRAIN_TO_DDS = {'S':0,'H':1,'D':2,'C':3,'N':4}
EXPECTED_ARCHIVE_SHA256 = '18bcbc66e4aa79bb44907dd598046a5d755c3791e70e2497e82e3fae89d000dc'
EXPECTED_HOLDOUT_MANIFEST_SHA256 = '0c500c0d323a855f55664e08bb3af4159d792e7e307a1bef0012e2bd5021b8cc'
EXPECTED_HOLDOUT_RAW_SHA256 = '1d6e02dffab607011090a4de1908af520aa5d359cfab04619119de5ca10e87f7'
EXPECTED_ORDERED_HOLDOUT_ID_SHA256 = '4d0e38f51fb15b77dda061eac4bc86e06f7e61a56a7dbb54b510cf1060a63802'
EXPECTED_TASK_PACKET_SHA256 = '4ee99c708bb997f28101c03367ef74eaf8c7242a413d1869685723d7b6b46c0f'
PREDICTION_GATE_COMMIT = '5a6c0f015effc7a9a916591f52da100110fa7beb'
PREDICTION_COMMIT = '2426531ab1cc02bf94ee6a4178a56a6874abb1aa'


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json_file_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def parse_lin(raw: str):
    parts = raw.split('|')
    tokens=[]
    for i in range(0,len(parts)-1,2):
        tokens.append((parts[i],parts[i+1]))
    d=collections.defaultdict(list)
    for k,v in tokens:
        d[k].append(v)
    return tokens,d


def parse_hand(txt):
    txt=(txt or '').strip().upper()
    cards=[]; current=None
    for ch in txt:
        if ch in SUITS: current=ch
        elif ch in RANKS and current: cards.append(current+ch)
    return cards


def derive_complete_hands(md):
    dealer=md[0]
    fields=md[1:].split(',')
    hands={}
    for i,seat in enumerate(ORDER):
        txt=fields[i] if i<len(fields) else ''
        hands[seat]=parse_hand(txt) if txt else []
    known=set(sum(hands.values(),[]))
    missing=[s for s in ORDER if len(hands[s])==0]
    if len(missing)==1 and len(known)==39:
        hands[missing[0]]=sorted(DECK-known, key=lambda c:(SUITS.index(c[0]), -RANK_VALUE[c[1]]))
    if any(len(hands[s])!=13 for s in ORDER):
        raise ValueError(f'bad hand lengths {[len(hands[s]) for s in ORDER]}')
    if len(set(sum(hands.values(),[]))) != 52:
        raise ValueError('deal is not a complete 52-card deal')
    return dealer,hands


def clean_call(c):
    c=(c or '').upper().replace('!','').strip()
    if c in ('P','PASS'): return 'P'
    if c in ('D','DBL','X'): return 'X'
    if c in ('R','RDBL','XX'): return 'XX'
    return c.replace('NT','N')


def seat_cycle_from(dealer_code,n):
    idx=int(dealer_code)-1
    return [ORDER[(idx+i)%4] for i in range(n)]


def auction_with_alerts(tokens,dealer):
    entries=[]
    call_index=0
    for i,(k,v) in enumerate(tokens):
        if k!='mb': continue
        call=clean_call(v)
        seat=seat_cycle_from(dealer, call_index+1)[-1]
        alert=None
        if i+1<len(tokens) and tokens[i+1][0]=='an':
            alert=tokens[i+1][1].strip()
        entries.append({'seat':seat,'call':call,'alert':alert or None})
        call_index+=1
    return entries


def final_contract_and_declarer(dealer_code,calls):
    seats=seat_cycle_from(dealer_code,len(calls))
    bid_indices=[i for i,c in enumerate(calls) if re.fullmatch(r'[1-7][CDHSN]',c)]
    if not bid_indices: return None,None,None,None
    last=bid_indices[-1]; final_bid=calls[last]; level=int(final_bid[0]); strain=final_bid[1]
    dbl=''
    for c in calls[last+1:]:
        if c=='X': dbl='X'
        elif c=='XX': dbl='XX'
    final_bidder=seats[last]
    side={'S','N'} if final_bidder in {'S','N'} else {'W','E'}
    declarer=None
    for i,c in enumerate(calls):
        if re.fullmatch(r'[1-7][CDHSN]',c) and c[1]==strain and seats[i] in side:
            declarer=seats[i]; break
    return f'{level}{strain}{dbl}',declarer,PARTNER[declarer],strain


def card_beats(card,best,lead_suit,trump):
    cs,cr=card; bs,br=best
    if trump!='N':
        if cs==trump and bs!=trump: return True
        if cs!=trump and bs==trump: return False
    if cs==bs: return RANK_VALUE[cr]>RANK_VALUE[br]
    if cs==lead_suit and bs!=lead_suit and (trump=='N' or bs!=trump): return True
    return False


def winner_of_trick(trick,trump):
    lead_suit=trick[0][1][0]
    best_seat,best_card=trick[0]
    for seat,card in trick[1:]:
        if card_beats(card,best_card,lead_suit,trump):
            best_seat,best_card=seat,card
    return best_seat


def sort_cards(cards):
    return sorted(cards,key=lambda c:(SUITS.index(c[0]),-RANK_VALUE[c[1]]))


def simulate(raw):
    tokens,d=parse_lin(raw)
    dealer,hands=derive_complete_hands(d['md'][0])
    auction=auction_with_alerts(tokens,dealer)
    calls=[e['call'] for e in auction]
    contract,decl,dummy,trump=final_contract_and_declarer(dealer,calls)
    if not decl: raise ValueError('passed out / no declarer')
    vulnerability=VUL_MAP.get((d.get('sv') or [''])[0].lower(), (d.get('sv') or [''])[0])
    board=(d.get('ah') or [''])[0] or None
    plays=[v.upper() for k,v in tokens if k=='pc']
    current={s:list(h) for s,h in hands.items()}
    leader=ORDER[(ORDER.index(decl)+1)%4]
    pos=leader; trick=[]; trick_no=1; history=[]; events=[]
    for idx,actual in enumerate(plays):
        lead_suit=trick[0][1][0] if trick else None
        hand=current[pos]
        follow=[c for c in hand if c[0]==lead_suit] if lead_suit else []
        legal=(follow if follow else list(hand))
        ev={
            'index':idx,'seat':pos,'actual':actual,'legal':list(legal),
            'trick_no':trick_no,'current_trick':copy.deepcopy(trick),
            'hand_before':list(hand),'history_before':copy.deepcopy(history),
            'dummy_remaining':list(current[dummy]),
            'contract':contract,'declarer':decl,'dummy':dummy,'trump':trump,
            'dealer':dealer,'auction':auction,'vulnerability':vulnerability,'board':board,
            'hands':hands,
        }
        events.append(ev)
        if actual not in hand or actual not in legal:
            raise ValueError(f'illegal play idx={idx} seat={pos} actual={actual}')
        current[pos].remove(actual)
        trick.append((pos,actual))
        if len(trick)==4:
            history.append(list(trick))
            winner=winner_of_trick(trick,trump)
            leader=winner; pos=winner; trick=[]; trick_no+=1
        else:
            pos=ORDER[(ORDER.index(pos)+1)%4]
    return events


def select_event(rec, ordinal):
    events=simulate(rec['raw'])
    if not events: raise ValueError('no play')
    decl=events[0]['declarer']; dummy=events[0]['dummy']
    requested=['opening_lead','declarer_continuation','defense_continuation'][(ordinal-1)%3]
    opening=events[0]
    declarer_next=next((e for e in events if e['index']>0 and e['seat']==decl and len(e['legal'])>1),None)
    defense_next=next((e for e in events if e['index']>0 and e['seat'] not in {decl,dummy} and len(e['legal'])>1),None)
    choices={'opening_lead':opening,'declarer_continuation':declarer_next,'defense_continuation':defense_next}
    if choices[requested] is not None:
        selected=requested
    else:
        fallback_order={
            'opening_lead':['declarer_continuation','defense_continuation'],
            'declarer_continuation':['defense_continuation','opening_lead'],
            'defense_continuation':['declarer_continuation','opening_lead']
        }[requested]
        selected=next(x for x in fallback_order if choices[x] is not None)
    return requested, selected, choices[selected], events


def safe_task(rec, ordinal):
    requested, selected, e, _ = select_event(rec, ordinal)
    history=[]
    for tno,tr in enumerate(e['history_before'],start=1):
        history.append({'trick':tno,'cards':[{'seat':s,'card':c} for s,c in tr]})
    current=[{'seat':s,'card':c} for s,c in e['current_trick']]
    visible_dummy=e['index']>0
    return {
        'task_id':f'BBO100-H{ordinal:02d}-{selected}',
        'holdout_ordinal':ordinal,
        'deal_id':rec['id'],
        'board':e['board'],
        'requested_family':requested,
        'selected_family':selected,
        'fallback_used':selected!=requested,
        'contract':e['contract'],
        'declarer':e['declarer'],
        'dummy':e['dummy'],
        'vulnerability':e['vulnerability'],
        'seat_to_act':e['seat'],
        'role':'declarer' if e['seat']==e['declarer'] else 'defender',
        'trick_number':e['trick_no'],
        'auction':e['auction'],
        'own_hand_remaining':sort_cards(e['hand_before']),
        'dummy_visible':visible_dummy,
        'dummy_hand_remaining':sort_cards(e['dummy_remaining']) if visible_dummy else None,
        'completed_tricks':history,
        'current_trick':current,
        'legal_cards':sort_cards(e['legal']),
        'instruction':'Choose exactly one card from legal_cards using only the visible bridge information in this task. No DDS, no hidden-hand inspection, no use of the recorded action.'
    }


def make_packet(holdout):
    tasks=[safe_task(rec,i) for i,rec in enumerate(holdout,1)]
    counts=collections.Counter(t['selected_family'] for t in tasks)
    packet={
        'created_from_archive_sha256': EXPECTED_ARCHIVE_SHA256,
        'family_counts': dict(sorted(counts.items())),
        'holdout_manifest_sha256': EXPECTED_HOLDOUT_MANIFEST_SHA256,
        'ordered_holdout_id_sha256': EXPECTED_ORDERED_HOLDOUT_ID_SHA256,
        'schema':'bbo100-blind-play-packet-v1',
        'selection_rule':{
            'actual_action_included':False,
            'dds_called':False,
            'declarer_continuation':'first declarer card-play decision after opening lead with more than one legal card',
            'defense_continuation':'first defender card-play decision after opening lead with more than one legal card',
            'fallback':'only if requested family unavailable; none were needed in this packet',
            'families_by_ordinal_cycle':['opening_lead','declarer_continuation','defense_continuation'],
            'hidden_hands_exposed':False,
            'opening_lead':'first card-play decision',
        },
        'task_count':len(tasks),
        'tasks':tasks,
    }
    return packet


def render_pbn(hands):
    def suit_ranks(cards,s):
        have={c[1] for c in cards if c[0]==s}
        return ''.join(r for r in RANKS if r in have)
    def one(seat):
        return '.'.join(suit_ranks(hands[seat],s) for s in SUITS)
    return 'N:'+' '.join(one(s) for s in ['N','E','S','W'])


def evaluate_one(rec, ordinal, prediction, dds_context):
    from playline import replay_line
    from dds_play import _candidate_scores
    import dds3

    requested, selected, event, events = select_event(rec, ordinal)
    task_id=f'BBO100-H{ordinal:02d}-{selected}'
    if prediction.get('task_id') != task_id:
        raise RuntimeError(f'prediction task mismatch at ordinal {ordinal}')
    predicted=str(prediction.get('predicted_card') or '').upper().replace('10','T')
    actual=str(event['actual']).upper().replace('10','T')
    legal=set(event['legal'])
    if predicted not in legal:
        raise RuntimeError(f'blind prediction {predicted} is not legal for {task_id}')
    if actual not in legal:
        raise RuntimeError(f'recorded card {actual} is not legal for {task_id}')

    tokens,d=parse_lin(rec['raw'])
    _,hands=derive_complete_hands(d['md'][0])
    pbn=render_pbn(hands)
    plays=[v.upper() for k,v in tokens if k=='pc']
    prefix=plays[:event['index']]
    decl_idx=SEATS.index(event['declarer'])
    trump=STRAIN_TO_DDS[event['trump']]
    opening_leader=(decl_idx+1)%4
    replay=replay_line(deal=pbn,declarer=decl_idx,trump=trump,cards=prefix,opening_leader=opening_leader)
    snapshot=replay['snapshots'][-1]
    if snapshot['next_seat_name'] != event['seat']:
        raise RuntimeError(f'position seat mismatch for {task_id}: {snapshot["next_seat_name"]} != {event["seat"]}')
    candidates=_candidate_scores(dds=dds3,snapshot=snapshot,trump=trump,thread_index=0,context=dds_context)
    scores=candidates['scores']
    if predicted not in scores or actual not in scores:
        raise RuntimeError(f'DDS candidate set missing legal action for {task_id}: predicted={predicted in scores} actual={actual in scores}')
    best=int(candidates['best_side_to_play_tricks'])
    pred_score=int(scores[predicted]); actual_score=int(scores[actual])
    pred_regret=best-pred_score; actual_regret=best-actual_score
    return {
        'task_id':task_id,
        'holdout_ordinal':ordinal,
        'deal_id':rec['id'],
        'family':selected,
        'role':'declarer' if event['seat']==event['declarer'] else 'defender',
        'contract':event['contract'],
        'seat_to_act':event['seat'],
        'trick_number':event['trick_no'],
        'predicted_card':predicted,
        'recorded_card':actual,
        'exact_match_recorded':predicted==actual,
        'predicted_dd_regret':pred_regret,
        'recorded_dd_regret':actual_regret,
        'assistant_minus_recorded_regret':pred_regret-actual_regret,
        'assistant_vs_recorded':'better' if pred_regret<actual_regret else 'worse' if pred_regret>actual_regret else 'equal',
        'predicted_zero_regret':pred_regret==0,
        'recorded_zero_regret':actual_regret==0,
        'optimal_cards':candidates['optimal_cards'],
        'candidate_scores':scores,
        'best_side_to_play_tricks':best,
        'predicted_side_to_play_tricks':pred_score,
        'recorded_side_to_play_tricks':actual_score,
        'confidence':prediction.get('confidence'),
        'nodes':int(candidates.get('nodes') or 0),
    }


def aggregate(rows):
    def stats(sub):
        if not sub: return {'tasks':0}
        regrets=[r['predicted_dd_regret'] for r in sub]
        return {
            'tasks':len(sub),
            'exact_match_recorded':sum(r['exact_match_recorded'] for r in sub),
            'exact_match_rate':sum(r['exact_match_recorded'] for r in sub)/len(sub),
            'zero_dd_regret':sum(r['predicted_zero_regret'] for r in sub),
            'zero_dd_regret_rate':sum(r['predicted_zero_regret'] for r in sub)/len(sub),
            'mean_dd_regret':sum(regrets)/len(regrets),
            'median_dd_regret':statistics.median(regrets),
            'max_dd_regret':max(regrets),
            'regret_2plus':sum(x>=2 for x in regrets),
            'assistant_better_than_recorded':sum(r['assistant_vs_recorded']=='better' for r in sub),
            'assistant_equal_to_recorded':sum(r['assistant_vs_recorded']=='equal' for r in sub),
            'assistant_worse_than_recorded':sum(r['assistant_vs_recorded']=='worse' for r in sub),
            'mean_recorded_dd_regret':sum(r['recorded_dd_regret'] for r in sub)/len(sub),
        }
    families=sorted({r['family'] for r in rows})
    return {'overall':stats(rows),'by_family':{f:stats([r for r in rows if r['family']==f]) for f in families}}


def report_markdown(evidence):
    a=evidence['aggregate']['overall']
    lines=[
        '# BBO-100 HOLDOUT-20 — blind card-play evaluation', '',
        f"**Status:** {evidence['status']}", '',
        'Blind predictions were committed before the oracle was opened. BBO/GIB recorded actions are shown only as a descriptive comparator; they are not treated as school truth or a bidding standard.', '',
        '## Overall', '',
        '| Metric | Value |','|---|---:|',
        f"| Tasks | {a['tasks']} |",
        f"| Exact match with recorded card | {a['exact_match_recorded']} / {a['tasks']} ({a['exact_match_rate']:.1%}) |",
        f"| Zero DD-regret | {a['zero_dd_regret']} / {a['tasks']} ({a['zero_dd_regret_rate']:.1%}) |",
        f"| Mean DD-regret | {a['mean_dd_regret']:.3f} |",
        f"| Median DD-regret | {a['median_dd_regret']:.3f} |",
        f"| Max DD-regret | {a['max_dd_regret']} |",
        f"| Assistant better / equal / worse than recorded action | {a['assistant_better_than_recorded']} / {a['assistant_equal_to_recorded']} / {a['assistant_worse_than_recorded']} |",
        '', '## By family', '',
        '| Family | Tasks | Zero regret | Mean regret | Exact recorded | Better / equal / worse |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for fam,st in evidence['aggregate']['by_family'].items():
        lines.append(f"| {fam} | {st['tasks']} | {st.get('zero_dd_regret',0)}/{st['tasks']} | {st.get('mean_dd_regret',0):.3f} | {st.get('exact_match_recorded',0)}/{st['tasks']} | {st.get('assistant_better_than_recorded',0)} / {st.get('assistant_equal_to_recorded',0)} / {st.get('assistant_worse_than_recorded',0)} |")
    lines += ['', '## Gate and interpretation', '',
        '- DDS3 is used only after the 20 predictions were durably committed.',
        '- Equal-optimal cards are preserved; a different card with the same DDS score is not counted as an error.',
        '- DDS is a double-dummy technical oracle. This result does not by itself prove practical single-dummy correctness.',
        '- BBO/GIB bidding is not promoted to school canon or methodology.',
        '- No canon, methodology, curriculum, or student-profile write is authorized by this benchmark.',
        '- N=20 is a small transfer sample; confidence calibration is descriptive only and cannot promote a rule or skill.',
        '', '## Per task', '',
        '| Task | Family | Pred | BBO | Pred regret | BBO regret | Optimum |',
        '|---|---|---:|---:|---:|---:|---|']
    for r in evidence['rows']:
        opt=', '.join(r['optimal_cards'])
        lines.append(f"| {r['task_id']} | {r['family']} | {r['predicted_card']} | {r['recorded_card']} | {r['predicted_dd_regret']} | {r['recorded_dd_regret']} | {opt} |")
    return '\n'.join(lines)+'\n'


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--archive',required=True,type=Path)
    ap.add_argument('--predictions',required=True,type=Path)
    ap.add_argument('--out-json',required=True,type=Path)
    ap.add_argument('--out-md',required=True,type=Path)
    args=ap.parse_args()
    if os.environ.get('DDS_TRAINING_CONFIRM')!='YES':
        raise SystemExit('DDS evaluation blocked: DDS_TRAINING_CONFIRM=YES required')
    archive_bytes=args.archive.read_bytes()
    archive_sha=sha256_bytes(archive_bytes)
    if archive_sha != EXPECTED_ARCHIVE_SHA256:
        raise SystemExit(f'archive sha mismatch {archive_sha}')
    with zipfile.ZipFile(args.archive) as zf:
        holdout_raw_bytes=zf.read('HOLDOUT_20_DO_NOT_DDS.json')
        holdout_manifest_bytes=zf.read('holdout_20_manifest_no_raw.jsonl')
        if sha256_bytes(holdout_raw_bytes)!=EXPECTED_HOLDOUT_RAW_SHA256:
            raise SystemExit('holdout raw sha mismatch')
        if sha256_bytes(holdout_manifest_bytes)!=EXPECTED_HOLDOUT_MANIFEST_SHA256:
            raise SystemExit('holdout manifest sha mismatch')
        holdout=json.loads(holdout_raw_bytes)
    if len(holdout)!=20:
        raise SystemExit(f'expected 20 holdout deals, got {len(holdout)}')
    packet=make_packet(holdout)
    packet_bytes=canonical_json_file_bytes(packet)
    packet_sha=sha256_bytes(packet_bytes)
    if packet_sha != EXPECTED_TASK_PACKET_SHA256:
        raise SystemExit(f'blind task packet mismatch {packet_sha}')
    predictions=json.loads(args.predictions.read_text(encoding='utf-8'))
    if predictions.get('gate_commit')!=PREDICTION_GATE_COMMIT:
        raise SystemExit('prediction gate commit mismatch')
    if predictions.get('blind_task_packet_sha256')!=EXPECTED_TASK_PACKET_SHA256:
        raise SystemExit('prediction task packet hash mismatch')
    pred_rows=predictions.get('predictions') or []
    if len(pred_rows)!=20:
        raise SystemExit(f'expected 20 predictions, got {len(pred_rows)}')
    import dds3
    context=dds3.SolverContext() if hasattr(dds3,'SolverContext') else None
    rows=[evaluate_one(rec,i,pred_rows[i-1],context) for i,rec in enumerate(holdout,1)]
    evidence={
        'schema':'bbo100-blind-dds-evaluation-v1',
        'status':'BLIND_EVALUATION_COMPLETE',
        'model':'GPT-5.6 Sol',
        'prediction_gate_commit':PREDICTION_GATE_COMMIT,
        'prediction_commit':PREDICTION_COMMIT,
        'source_archive_sha256':archive_sha,
        'holdout_manifest_sha256':EXPECTED_HOLDOUT_MANIFEST_SHA256,
        'holdout_raw_sha256':EXPECTED_HOLDOUT_RAW_SHA256,
        'ordered_holdout_id_sha256':EXPECTED_ORDERED_HOLDOUT_ID_SHA256,
        'blind_task_packet_sha256':packet_sha,
        'task_count':20,
        'dds':{
            'version_pin':'3.0.0',
            'source_commit_pin':'37c8a79f4c67c55d1a309ccb66dd00cb58af464a',
            'solver_context_reused': context is not None,
            'oracle_opened_only_after_prediction_commit':True,
        },
        'aggregate':aggregate(rows),
        'rows':rows,
        'interpretation':{
            'recorded_bbo_action_is_truth':False,
            'bbo_gib_bidding_is_school_standard':False,
            'dds_is_double_dummy_only':True,
            'single_dummy_correctness_proven':False,
            'equal_optimal_moves_preserved':True,
            'canon_write':'DENY','methodology_write':'DENY','student_profile_write':'DENY',
            'sample_size_warning':'N=20; descriptive transfer evidence only',
        },
    }
    args.out_json.parent.mkdir(parents=True,exist_ok=True)
    args.out_json.write_bytes(canonical_json_file_bytes(evidence))
    args.out_md.write_text(report_markdown(evidence),encoding='utf-8')
    print(json.dumps({'status':evidence['status'],'aggregate':evidence['aggregate'],'json_sha256':sha256_bytes(args.out_json.read_bytes()),'md_sha256':sha256_bytes(args.out_md.read_bytes())},ensure_ascii=False,sort_keys=True))

if __name__=='__main__':
    main()
