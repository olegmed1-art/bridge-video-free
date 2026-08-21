#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,urllib.request
from pathlib import Path
SEATS=['N','E','S','W']

def post(base,token,payload):
    q=urllib.request.Request(base.rstrip('/')+'/v1/compute',data=json.dumps(payload).encode(),headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(q,timeout=60) as r: out=json.loads(r.read())
    assert out['engine']=='DDS3' and out['fallback_used'] is False
    return out

def strain(contract):
    x=contract[1:]
    return 'NT' if x.startswith('NT') else x[0]

def leader(decl): return SEATS[(SEATS.index(decl)+1)%4]
def side(seat): return 'NS' if seat in {'N','S'} else 'EW'
def diana_seat(direction): return 'S' if direction=='NS' else 'W'

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--facts',type=Path,required=True); ap.add_argument('--base-url',required=True); ap.add_argument('--token',required=True); ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args(); f=json.loads(a.facts.read_text('utf-8')); rows=[]
    for b in f['boards']:
        first=leader(b['declarer'])
        p=post(a.base_url,a.token,{'operation':'position_all_moves','position':{'pbn':b['pbn'],'trump':strain(b['contract']),'first':first,'current_trick':[]}})
        moves={m['card']:m for m in p['moves']}; card=b['opening_lead']
        if side(first)==b['pair_direction']:
            owner='Diana' if first==diana_seat(b['pair_direction']) else 'Anna'
        else: owner='Opponent'
        row={'board':b['board'],'contract':b['contract'],'declarer':b['declarer'],'leader':first,'owner':owner,'lead':card,'optimal_cards':p['optimal_cards'],'best_tricks_for_defending_side_to_play':p['best_tricks'],'pair_matchpoints':b['pair_matchpoints']}
        if card in moves:
            row['lead_move']=moves[card]
            row['lead_mapping']='exact'
        else:
            row['lead_move']=None
            row['lead_mapping']='recorded lead not returned as a DDS3 move; no regret claimed'
        rows.append(row)
    out={'schema':'diana-29912-opening-leads-dds3-v1','engine':'DDS3','fallback_used':False,'rows':rows}
    a.out.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for r in rows:
        if r['lead_move']:
            print(r['board'],r['owner'],r['lead'],'regret',r['lead_move']['regret'],'optimal',','.join(r['optimal_cards']))
        else:
            print(r['board'],r['owner'],r['lead'],'UNMAPPED','optimal',','.join(r['optimal_cards']))
if __name__=='__main__': main()
