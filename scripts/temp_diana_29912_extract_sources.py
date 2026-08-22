#!/usr/bin/env python3
from __future__ import annotations
import argparse,html,json,re
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup

SUIT_MAP={'♠':'S','♥':'H','♦':'D','♣':'C'}
SEATS=['N','E','S','W']; SUITS='SHDC'; RANKS='AKQJT98765432'
VUL_CYCLE=['None','NS','EW','Both','NS','EW','Both','None','EW','Both','None','NS','Both','None','NS','EW']
DEALER_CYCLE=['N','E','S','W']
FIELD_SIZES={1:8,2:8,4:8,5:9,6:8}

def norm_card(s):
    s=' '.join(s.split())
    for sym,c in SUIT_MAP.items():
        if sym in s: return c+s.replace(sym,'').replace(' ','').upper().replace('10','T')
    raise ValueError('bad card '+repr(s))

def contract_parse(s):
    s=' '.join(s.split()).replace('NT','N').replace('10','T')
    m=re.search(r'([1-7])\s*([N♠♥♦♣])\s*(XX|X)?\s*([=]|[+-]\d+)\s*\[([NESW])\]',s)
    if not m: raise ValueError('contract '+repr(s))
    strain={'N':'NT','♠':'S','♥':'H','♦':'D','♣':'C'}[m.group(2)]
    dbl=m.group(3) or ''; delta=0 if m.group(4)=='=' else int(m.group(4))
    return f'{m.group(1)}{strain}{dbl}',delta,m.group(5)

def hand_from_td(td):
    t=html.unescape(str(td)); t=re.sub(r'<br\s*/?>','\n',t,flags=re.I); t=re.sub(r'<[^>]+>','',t)
    lines=[re.sub(r'\s+',' ',z).strip() for z in t.splitlines() if re.sub(r'\s+',' ',z).strip()]
    vals=[]
    for sym in ['♠','♥','♦','♣']:
        found=''
        for line in lines:
            if sym in line:
                found=line.split(sym,1)[1].strip().replace(' ','').replace('10','T'); break
        vals.append(found or '-')
    return '.'.join(vals)

def parse_hands(board_html):
    s=BeautifulSoup(board_html,'html.parser'); deal=s.find('table',class_='deal')
    if not deal: raise ValueError('no deal table')
    rows=deal.find_all('tr',recursive=False)
    n=hand_from_td(rows[0].find_all('td',recursive=False)[1]); mids=rows[1].find_all('td',recursive=False)
    w=hand_from_td(mids[0]); e=hand_from_td(mids[2]); south=hand_from_td(rows[2].find_all('td',recursive=False)[1])
    return {'N':n,'E':e,'S':south,'W':w}

def validate_hands(h):
    cards=[]
    for seat in SEATS:
        parts=h[seat].split('.'); n=0
        if len(parts)!=4: raise ValueError((seat,h[seat]))
        for suit,st in zip(SUITS,parts):
            if st=='-': st=''
            n+=len(st)
            for r in st:
                if r not in RANKS: raise ValueError((seat,suit,r))
                cards.append(suit+r)
        if n!=13: raise ValueError((seat,n,h[seat]))
    if len(cards)!=52 or len(set(cards))!=52: raise ValueError('deal not 52 unique cards')

def pbn(h): return 'N:'+' '.join(h[x] for x in SEATS)

def meta_personal(text):
    s=BeautifulSoup(text,'html.parser'); t0=s.find_all('table')[0]; meta=' '.join(t0.stripped_strings)
    rm=re.search(r'מושב\s*(\d+)\s*מתוך\s*(\d+)',meta); dm=re.search(r'תאריך:\s*(\d{2}/\d{2}/\d{2})',meta)
    session=int(rm.group(1)); total=int(rm.group(2)); date=datetime.strptime(dm.group(1),'%d/%m/%y').strftime('%Y-%m-%d')
    players=[]
    for tr in s.find_all('table')[1].find_all('tr'):
        c=[x.get_text(' ',strip=True) for x in tr.find_all('td',recursive=False)]
        if len(c)>=2 and c[0].isdigit(): players.append(c)
    names=[r[1] for r in players[:2]]; diana_idx=next(i for i,n in enumerate(names) if 'דיאנה' in n and 'קסלר' in n)
    first=next(' '.join(tr.stripped_strings) for tr in s.find_all('table')[2].find_all('tr') if 'תוצאה:' in ' '.join(tr.stripped_strings))
    score=float(re.search(r'תוצאה:\s*([+-]?\d+(?:\.\d+)?)',first).group(1)); rank=int(re.search(r'דרוג:\s*(\d+)',first).group(1))
    return {'session':session,'sessions_total':total,'date':date,'names':names,'diana_index':diana_idx,'partner':names[1-diana_idx],'session_score':score,'rank':rank,'field_size':FIELD_SIZES[session]}

def parse_round(root,round_no):
    d=root/f'r{round_no}'; text=(d/'personal.html').read_text('utf-8'); meta=meta_personal(text); s=BeautifulSoup(text,'html.parser')
    rows=[]
    for tr in s.find_all('table')[2].find_all('tr'):
        c=[x.get_text(' ',strip=True) for x in tr.find_all('td',recursive=False)]
        if len(c)==8 and c[0].isdigit(): rows.append(c)
    links=[x for x in (d/'board-links.txt').read_text().splitlines() if x]
    boards=[]; skipped=[]
    for c in rows:
        board=int(c[0]); direction=c[1]; score_text=c[2] or c[3]
        if not re.fullmatch(r'-?\d+',score_text or '') or not c[5] or '[' not in c[6]:
            skipped.append({'board':board,'reason':'no played contract/lead or adjusted score','row':c}); continue
        pair_score=int(score_text) if direction=='NS' else -int(score_text)
        contract,delta,decl=contract_parse(c[6]); lead=norm_card(c[5])
        idx=next((i for i,href in enumerate(links,1) if re.search(r'(?:[?&])board='+str(board)+r'(?:&|$)',href)),None)
        if idx is None: raise RuntimeError(f'board link missing r{round_no} b{board}')
        hands=parse_hands((d/'boards'/f'board-{idx}.html').read_text('utf-8')); validate_hands(hands)
        diana_seat={'NS':['N','S'],'EW':['E','W']}[direction][meta['diana_index']]
        boards.append({'board':board,'pair_direction':direction,'pair_score':pair_score,'pair_matchpoints':float(c[4]),'opening_lead':lead,'contract':contract,'result_delta':delta,'declarer':decl,'dealer':DEALER_CYCLE[(board-1)%4],'vulnerability':VUL_CYCLE[(board-1)%16],'hands':hands,'pbn':pbn(hands),'diana_seat':diana_seat})
    return {'source':{'event':29912,'round':round_no,'site_dd_used':False},'tournament':meta,'skipped_rows':skipped,'boards':boards}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args(); a.out_dir.mkdir(parents=True,exist_ok=True)
    for r in [1,2,4,5,6]:
        out=parse_round(a.root,r); fp=a.out_dir/f'tournament_29912_round{r}_diana_facts.json'; fp.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
        print(r,out['tournament']['date'],out['tournament']['partner'],len(out['boards']),'played',len(out['skipped_rows']),'skipped')
if __name__=='__main__': main()
