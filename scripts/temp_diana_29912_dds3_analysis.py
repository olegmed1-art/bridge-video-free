#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

SEATS = ['N','E','S','W']
RANKS = 'AKQJT98765432'


def post(base: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base.rstrip('/') + '/v1/compute',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read().decode('utf-8'))
    if out.get('engine') != 'DDS3' or out.get('fallback_used') is not False:
        raise RuntimeError('non-canonical DDS result rejected')
    return out


def validate_deal(hands: dict[str,str]) -> None:
    cards=[]
    for seat in SEATS:
        hand=hands[seat]
        suits=hand.split('.')
        if len(suits)!=4:
            raise ValueError(f'{seat}: bad hand')
        count=0
        for si, st in enumerate(suits):
            if st=='-': st=''
            count += len(st)
            for r in st:
                if r not in RANKS:
                    raise ValueError(f'{seat}: bad rank {r}')
                cards.append('SHDC'[si]+r)
        if count != 13:
            raise ValueError(f'{seat}: {count} cards')
    if len(cards)!=52 or len(set(cards))!=52:
        raise ValueError('deal is not 52 unique cards')


def parse_contract(contract: str):
    level=int(contract[0])
    rest=contract[1:]
    if rest.startswith('NT'):
        strain='NT'
    else:
        strain=rest[0]
    return level,strain


def opening_leader(declarer: str) -> str:
    return SEATS[(SEATS.index(declarer)+1)%4]


def diana_seat(direction: str) -> str:
    # Pair is consistently displayed Anna Petrenko - Diana Veksler.
    # On IBF result pages the second listed player occupies S (NS) or W (EW).
    return 'S' if direction == 'NS' else 'W'


def actual_tricks(board: dict) -> int:
    level,_=parse_contract(board['contract'])
    return 6+level+int(board['result_delta'])


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--facts',type=Path,required=True)
    ap.add_argument('--base-url',required=True)
    ap.add_argument('--token',required=True)
    ap.add_argument('--out-json',type=Path,required=True)
    ap.add_argument('--out-md',type=Path,required=True)
    args=ap.parse_args()
    facts=json.loads(args.facts.read_text('utf-8'))
    results=[]
    engine_version=None
    for b in facts['boards']:
        validate_deal(b['hands'])
        dd=post(args.base_url,args.token,{
            'operation':'dd_table','pbn':b['pbn'],'dealer':b['dealer'],'vulnerability':b['vulnerability']
        })
        if dd.get('operation')!='dd_table' or dd.get('input_validated') is not True:
            raise RuntimeError(f"board {b['board']}: DDS provenance invalid")
        if engine_version is None: engine_version=dd.get('engine_version')
        elif engine_version != dd.get('engine_version'): raise RuntimeError('DDS engine changed inside run')
        level,strain=parse_contract(b['contract'])
        decl=b['declarer']
        act=actual_tricks(b)
        dd_tricks=int(dd['dd_table'][strain][SEATS.index(decl)])
        side='NS' if decl in {'N','S'} else 'EW'
        target=b['pair_direction']
        if target==side:
            target_delta=act-dd_tricks
        else:
            target_delta=dd_tricks-act
        target_par=int(dd['par_score_ns']) if target=='NS' else -int(dd['par_score_ns'])
        r={
            **b,
            'dds3':dd,
            'same_contract':{
                'actual_tricks':act,'dds3_tricks':dd_tricks,
                'actual_minus_dd_declarer':act-dd_tricks,
                'target_pair_delta_vs_dd':target_delta,
            },
            'par_for_target_pair':target_par,
            'pair_score_minus_par':int(b['pair_score'])-target_par,
            'diana_seat':diana_seat(target),
            'diana_declarer':decl==diana_seat(target),
            'diana_opening_leader':opening_leader(decl)==diana_seat(target),
        }
        results.append(r)

    # Evaluate only opening leads that can be attributed to Diana by compass seat.
    for r in results:
        if not r['diana_opening_leader']:
            continue
        _,trump=parse_contract(r['contract'])
        pos=post(args.base_url,args.token,{
            'operation':'position_all_moves',
            'position':{'pbn':r['pbn'],'trump':trump,'first':r['diana_seat'],'current_trick':[]}
        })
        moves={m['card']:m for m in pos['moves']}
        lead=r['opening_lead']
        if lead not in moves:
            raise RuntimeError(f"board {r['board']}: recorded lead {lead} absent from DDS3 legal moves")
        r['diana_opening_lead_dds3']={
            'recorded_lead':lead,
            'recorded_move':moves[lead],
            'best_tricks':pos['best_tricks'],
            'optimal_cards':pos['optimal_cards'],
            'moves':pos['moves'],
            'tricks_remaining':pos['tricks_remaining'],
            'nodes':pos['nodes'],
            'solver_context':pos['solver_context'],
            'engine':pos['engine'],'engine_version':pos.get('engine_version'),'fallback_used':pos['fallback_used'],
        }

    ddecl=[r for r in results if r['diana_declarer']]
    dleads=[r for r in results if r['diana_opening_leader']]
    shortfalls=[r for r in ddecl if r['same_contract']['actual_minus_dd_declarer']<0]
    lead_errors=[r for r in dleads if r['diana_opening_lead_dds3']['recorded_move']['regret']>0]
    summary={
        'boards':len(results),
        'engine':'DDS3','engine_version':engine_version,'fallback_used':False,
        'diana_declarer_boards':[r['board'] for r in ddecl],
        'diana_declarer_shortfall_boards':[r['board'] for r in shortfalls],
        'diana_opening_lead_boards':[r['board'] for r in dleads],
        'diana_opening_lead_regret_boards':[r['board'] for r in lead_errors],
    }
    report={'schema':'diana-29912-round6-dds3-analysis-v1','source':facts['source'],'tournament':facts['tournament'],
            'policy':{'dds3_only':True,'no_site_dd_used':True,'no_play_record':True,'no_auction_record':True,
                      'player_attribution':'Diana is second listed player: S on NS, W on EW.'},
            'summary':summary,'boards':results}
    args.out_json.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    lines=[
      '# Диана Векслер — турнир 29912, сессия 6 — DDS3 evidence', '',
      f"Дата: {facts['tournament']['date']}; пара: Anna Petrenko — Diana Veksler; место: {facts['tournament']['rank']} из {facts['tournament']['field_size']}; session score: {facts['tournament']['session_score']:+.1f}.",
      f"DDS3: {engine_version}; fallback=false. Числа DD ниже получены только этим запуском DDS3; DD-таблицы сайта не использовались.", '',
      '## Сдачи, где Диана — разыгрывающая', '',
      '| № | Контракт | Факт | DDS3 того же контракта | Δ факт-DD | MP | Par пары | Score-Par |',
      '|---:|:---:|---:|---:|---:|---:|---:|---:|',
    ]
    for r in ddecl:
        c=r['same_contract']
        lines.append(f"| {r['board']} | {r['contract']} {r['declarer']} | {c['actual_tricks']} | {c['dds3_tricks']} | {c['actual_minus_dd_declarer']:+d} | {r['pair_matchpoints']:+.1f} | {r['par_for_target_pair']} | {r['pair_score_minus_par']:+d} |")
    lines += ['', 'Отрицательная Δ — подтверждённый результат-level недобор относительно double-dummy максимума в фактически сыгранном контракте. Без покарточной записи конкретная карта ошибки не называется.', '',
              '## Первые ходы Дианы в защите', '',
              '| № | Контракт соперников | Ход | Regret DDS3 | Оптимальные первые ходы | MP |',
              '|---:|:---:|:---:|---:|:---|---:|']
    for r in dleads:
        x=r['diana_opening_lead_dds3']; m=x['recorded_move']
        lines.append(f"| {r['board']} | {r['contract']} {r['declarer']} | {r['opening_lead']} | {m['regret']} | {', '.join(x['optimal_cards'])} | {r['pair_matchpoints']:+.1f} |")
    lines += ['', 'Здесь regret относится именно к реально записанному первому ходу. Для последующих защитных карт данных нет, поэтому first swing после первой карты не реконструируется.', '',
              '## Пары/торговля: кандидаты для обсуждения', '',
              'DDS3 Par используется только как open-card ориентир. Он не доказывает, какая заявка была правильной в системе школы, потому что фактический аукцион на странице отсутствует.', '',
              '| № | Направление | Факт. контракт | Score пары | DDS3 Par пары | Разница | MP |',
              '|---:|:---:|:---:|---:|---:|---:|---:|']
    for r in sorted(results,key=lambda x:(x['pair_score_minus_par'],x['board'])):
        lines.append(f"| {r['board']} | {r['pair_direction']} | {r['contract']} {r['declarer']} | {r['pair_score']} | {r['par_for_target_pair']} | {r['pair_score_minus_par']:+d} | {r['pair_matchpoints']:+.1f} |")
    args.out_md.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
