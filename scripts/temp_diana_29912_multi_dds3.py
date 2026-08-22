#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re,urllib.request
from pathlib import Path

SEATS=['N','E','S','W']; SUITS='SHDC'; RANKS='AKQJT98765432'

def post(base,token,payload):
    req=urllib.request.Request(base.rstrip('/')+'/v1/compute',data=json.dumps(payload).encode(),headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=60) as r: out=json.loads(r.read().decode())
    if out.get('engine')!='DDS3' or out.get('fallback_used') is not False: raise RuntimeError('non-canonical DDS3 result rejected')
    return out

def contract_parts(c):
    m=re.match(r'([1-7])(NT|[SHDC])',c)
    if not m: raise ValueError(c)
    return int(m.group(1)),m.group(2)

def actual_tricks(b): return 6+contract_parts(b['contract'])[0]+int(b['result_delta'])
def side(seat): return 'NS' if seat in {'N','S'} else 'EW'
def next_seat(seat): return SEATS[(SEATS.index(seat)+1)%4]
def partner_seat(seat): return SEATS[(SEATS.index(seat)+2)%4]
def opening_leader(decl): return next_seat(decl)

def validate_hands(h):
    cards=[]
    for seat in SEATS:
        parts=h[seat].split('.'); assert len(parts)==4
        n=0
        for suit,st in zip(SUITS,parts):
            if st=='-': st=''
            n+=len(st)
            for r in st:
                if r not in RANKS: raise ValueError((seat,suit,r))
                cards.append(suit+r)
        if n!=13: raise ValueError((seat,n,h[seat]))
    if len(cards)!=52 or len(set(cards))!=52: raise ValueError('not 52 unique cards')

def remove_card(hands,seat,card):
    suit,rank=card[0],card[1:]
    if rank=='10': rank='T'
    out=dict(hands); parts=out[seat].split('.'); si=SUITS.index(suit); st='' if parts[si]=='-' else parts[si]
    if rank not in st: raise ValueError(f'{card} not in {seat} hand {out[seat]}')
    st=st.replace(rank,'',1); parts[si]=st or '-'; out[seat]='.'.join(parts); return out

def pbn(h): return 'N:'+' '.join(h[x] for x in SEATS)

def actor_for_leader(diana,leader):
    if leader==diana: return 'Diana'
    if leader==partner_seat(diana): return 'Partner'
    return 'Opponent'

def analyze_board(base,token,b):
    validate_hands(b['hands'])
    dd=post(base,token,{'operation':'dd_table','pbn':b['pbn'],'dealer':b['dealer'],'vulnerability':b['vulnerability']})
    if dd.get('operation')!='dd_table' or dd.get('input_validated') is not True: raise RuntimeError('bad DD provenance')
    level,strain=contract_parts(b['contract']); decl=b['declarer']; act=actual_tricks(b)
    dd_tricks=int(dd['dd_table'][strain][SEATS.index(decl)])
    target=b['pair_direction']; target_par=int(dd['par_score_ns']) if target=='NS' else -int(dd['par_score_ns'])
    diana=b['diana_seat']; leader=opening_leader(decl); actor=actor_for_leader(diana,leader)
    start=post(base,token,{'operation':'position_all_moves','position':{'pbn':b['pbn'],'trump':strain,'first':leader,'current_trick':[]}})
    if side(leader)==side(decl): raise RuntimeError('opening leader on declarer side')
    after_h=remove_card(b['hands'],leader,b['opening_lead'])
    nxt=next_seat(leader)
    if side(nxt)!=side(decl): raise RuntimeError('next seat after opening leader is not declarer side')
    # DDS Deal.first remains the leader of the current trick; current_trick length determines who plays next.
    after=post(base,token,{'operation':'position_all_moves','position':{'pbn':pbn(after_h),'trump':strain,'first':leader,'current_trick':[b['opening_lead']]}})
    defender_best=int(start['best_tricks'])
    declarer_after=int(after['best_tricks'])
    defender_after=int(after['tricks_remaining'])-declarer_after
    regret=defender_best-defender_after
    if regret<0: raise RuntimeError(f'negative opening lead regret board {b["board"]}')
    mapped={m['card']:m for m in start['moves']}.get(b['opening_lead'])
    if mapped is not None and int(mapped['regret'])!=regret:
        raise RuntimeError(f'mapped vs explicit regret mismatch board {b["board"]}: {mapped["regret"]} != {regret}')
    return {**b,'dds3':dd,'same_contract':{'actual_tricks':act,'dds3_tricks':dd_tricks,'actual_minus_dd_declarer':act-dd_tricks},
            'par_for_pair':target_par,'pair_score_minus_par':int(b['pair_score'])-target_par,
            'diana_declarer':decl==diana,'diana_opening_leader':leader==diana,'opening_leader':leader,
            'opening_lead_dds3':{'actor':actor,'recorded_lead':b['opening_lead'],'regret':regret,
                                'best_defender_tricks_before':defender_best,'defender_tricks_after_recorded_lead':defender_after,
                                'declarer_ceiling_after_recorded_lead':declarer_after,'optimal_leads':start['optimal_cards'],
                                'recorded_move_in_start_map':mapped,'start_nodes':start['nodes'],'after_nodes':after['nodes']}}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--facts-dir',type=Path,required=True); ap.add_argument('--base-url',required=True); ap.add_argument('--token',required=True); ap.add_argument('--out-json',type=Path,required=True); ap.add_argument('--out-md',type=Path,required=True)
    a=ap.parse_args(); sessions=[]; engine_version=None
    for fp in sorted(a.facts_dir.glob('tournament_29912_round*_diana_facts.json'),key=lambda p:int(re.search(r'round(\d+)',p.name).group(1))):
        f=json.loads(fp.read_text())
        rows=[]
        for b in f['boards']:
            r=analyze_board(a.base_url,a.token,b); rows.append(r)
            ev=r['dds3'].get('engine_version')
            if engine_version is None: engine_version=ev
            elif ev!=engine_version: raise RuntimeError('engine changed during run')
        ddecl=[r for r in rows if r['diana_declarer']]
        dleads=[r for r in rows if r['diana_opening_leader']]
        ses={
          'round':f['source']['round'],'tournament':f['tournament'],'boards':rows,
          'summary':{
            'played_boards':len(rows),
            'diana_declarer_boards':[r['board'] for r in ddecl],
            'diana_declarer_shortfalls':[{'board':r['board'],'tricks_lost':-r['same_contract']['actual_minus_dd_declarer']} for r in ddecl if r['same_contract']['actual_minus_dd_declarer']<0],
            'diana_declarer_post_lead_shortfalls':[{'board':r['board'],'tricks_below_post_lead_ceiling':r['opening_lead_dds3']['declarer_ceiling_after_recorded_lead']-r['same_contract']['actual_tricks']} for r in ddecl if r['same_contract']['actual_tricks']<r['opening_lead_dds3']['declarer_ceiling_after_recorded_lead']],
            'opponent_opening_lead_gifts_to_diana':[{'board':r['board'],'gift_tricks':r['opening_lead_dds3']['regret']} for r in ddecl if r['opening_lead_dds3']['regret']>0],
            'diana_opening_leads':[r['board'] for r in dleads],
            'diana_opening_lead_errors':[{'board':r['board'],'regret':r['opening_lead_dds3']['regret']} for r in dleads if r['opening_lead_dds3']['regret']>0],
          }
        }
        sessions.append(ses)
    aggregate={
      'sessions':[s['round'] for s in sessions],
      'played_boards':sum(s['summary']['played_boards'] for s in sessions),
      'diana_declarer_count':sum(len(s['summary']['diana_declarer_boards']) for s in sessions),
      'diana_declarer_shortfall_events':sum(len(s['summary']['diana_declarer_shortfalls']) for s in sessions),
      'diana_declarer_tricks_lost_vs_initial_dd':sum(x['tricks_lost'] for s in sessions for x in s['summary']['diana_declarer_shortfalls']),
      'diana_opening_lead_count':sum(len(s['summary']['diana_opening_leads']) for s in sessions),
      'diana_opening_lead_error_count':sum(len(s['summary']['diana_opening_lead_errors']) for s in sessions),
      'diana_opening_lead_total_regret':sum(x['regret'] for s in sessions for x in s['summary']['diana_opening_lead_errors']),
      'opponent_opening_lead_gift_events_to_diana':sum(len(s['summary']['opponent_opening_lead_gifts_to_diana']) for s in sessions),
      'opponent_opening_lead_gift_tricks_to_diana':sum(x['gift_tricks'] for s in sessions for x in s['summary']['opponent_opening_lead_gifts_to_diana']),
    }
    report={'schema':'diana-29912-multi-session-dds3-v1','policy':{'engine':'DDS3','engine_version':engine_version,'fallback_used':False,'site_dd_used':False,'full_play_records_available':False,'auction_records_available':False},'aggregate':aggregate,'sessions':sessions}
    a.out_json.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['# Диана Векслер — event 29912 — сквозной DDS3-разбор','',f'DDS3: {engine_version}; fallback=false; DD сайта не использовался.','',
           f"Завершённые сессии с Дианой: {', '.join(map(str,aggregate['sessions']))}. Сыгранных/валидных для анализа сдач: {aggregate['played_boards']}.",'']
    lines += ['## Сводка по сессиям','', '| Сессия | Дата | Партнёр | Место | Score | Разыгрывает | Недоборы DD | Первые ходы | Ошибки хода | Подарки на первом ходе соперника |','|---:|:---:|:---|---:|---:|---:|---:|---:|---:|---:|']
    for s in sessions:
        t=s['tournament']; q=s['summary']
        lines.append(f"| {s['round']} | {t['date']} | {t['partner']} | {t['rank']}/{t['field_size']} | {t['session_score']:+.1f} | {len(q['diana_declarer_boards'])} | {len(q['diana_declarer_shortfalls'])} | {len(q['diana_opening_leads'])} | {len(q['diana_opening_lead_errors'])} | {len(q['opponent_opening_lead_gifts_to_diana'])} |")
    lines += ['', '## Подтверждённые недоборы Дианы как разыгрывающей','', '| Сессия | № | Контракт | Факт | DD до хода | Regret первого хода защиты | Потолок после хода | Недобор после хода | MP |','|---:|---:|:---:|---:|---:|---:|---:|---:|---:|']
    for s in sessions:
      for r in s['boards']:
        if r['diana_declarer'] and r['same_contract']['actual_tricks']<r['opening_lead_dds3']['declarer_ceiling_after_recorded_lead']:
          l=r['opening_lead_dds3']; c=r['same_contract']
          lines.append(f"| {s['round']} | {r['board']} | {r['contract']} {r['declarer']} | {c['actual_tricks']} | {c['dds3_tricks']} | {l['regret']} | {l['declarer_ceiling_after_recorded_lead']} | {l['declarer_ceiling_after_recorded_lead']-c['actual_tricks']} | {r['pair_matchpoints']:+.1f} |")
    lines += ['', '## Первые ходы Дианы с DDS3-regret > 0','', '| Сессия | № | Контракт | Ход | Regret | Оптимальные ходы | MP |','|---:|---:|:---:|:---:|---:|:---|---:|']
    for s in sessions:
      for r in s['boards']:
        l=r['opening_lead_dds3']
        if r['diana_opening_leader'] and l['regret']>0:
          lines.append(f"| {s['round']} | {r['board']} | {r['contract']} {r['declarer']} | {r['opening_lead']} | {l['regret']} | {', '.join(l['optimal_leads'])} | {r['pair_matchpoints']:+.1f} |")
    lines += ['', '## Ограничение','', 'Без полного покарточного протокола нельзя назвать конкретную последующую карту, на которой произошёл swing. Без аукциона нельзя приписывать торговую ошибку конкретной заявке. Par остаётся только open-card ориентиром.']
    a.out_md.write_text('\n'.join(lines)+'\n')
    print(json.dumps(aggregate,ensure_ascii=False,sort_keys=True))

if __name__=='__main__': main()
