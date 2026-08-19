from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://webutil.bridgebase.com/v2/"
ROBOT_ARCHIVE = "https://webutil.bridgebase.com/v2/tarchive.php?d=bbombadmin&h=bbombadmin&m=h"
OUT = Path("bbo100_probe")
RAW = OUT / "raw_lin"
INVALID = OUT / "invalid_samples"
OUT.mkdir(exist_ok=True)
RAW.mkdir(exist_ok=True)
INVALID.mkdir(exist_ok=True)
PROBE_VERSION = 6
TIMEOUT = 45
TARGET_RAW_UNIQUE = 110
FINAL_CORPUS = 100
TRAIN_N = 80
SPLIT_SEED = "BBO100-fresh-public-MBT-2026-08-19-v1"
SUITS = "SHDC"
RANKS = "23456789TJQKA"


def all_links(html: str, base: str):
    soup = BeautifulSoup(html, "html.parser")
    return [(" ".join(a.stripped_strings), urljoin(base, a.get("href"))) for a in soup.find_all("a", href=True)]


def lin_nodes(raw: str):
    parts = raw.strip().split("|")
    d = {}
    for i in range(0, len(parts) - 1, 2):
        if parts[i]:
            d.setdefault(parts[i].lower(), []).append(parts[i + 1])
    return d


def cards_in_holding(h: str):
    cards=[]; suit=None
    for ch in h.upper().replace("10","T"):
        if ch in SUITS: suit=ch
        elif ch in RANKS:
            if suit is None: raise ValueError("rank before suit")
            cards.append(suit+ch)
        elif ch in " ,-\r\n\t": continue
        else: raise ValueError(f"bad holding char {ch!r}")
    return cards


def validate_lin(raw: str):
    d=lin_nodes(raw)
    if not d.get("md"): return False,"missing_md",{}
    md=d["md"][0]
    if not md or md[0] not in "1234": return False,"bad_dealer",{}
    hs=md[1:].strip(",").split(",") if md[1:].strip(",") else []
    if len(hs) not in (3,4): return False,f"holding_count_{len(hs)}",{}
    try: groups=[cards_in_holding(h) for h in hs]
    except Exception as e: return False,f"holding_parse:{type(e).__name__}",{}
    flat=[c for g in groups for c in g]
    if len(flat)!=len(set(flat)): return False,"duplicate_cards",{}
    if len(hs)==3 and len(flat)!=39: return False,f"three_hands_have_{len(flat)}",{}
    if len(hs)==4 and len(flat)!=52: return False,f"four_hands_have_{len(flat)}",{}
    play=[x.upper() for x in d.get("pc",[])]
    if play and any(not re.fullmatch(r"[SHDC](?:[2-9TJQKA]|10)",x) for x in play): return False,"bad_play_card",{}
    if len(play)<4: return False,"no_meaningful_play",{"play_cards":len(play)}
    return True,"ok",{
        "dealer_code":md[0],"three_or_four_hands":len(hs),"known_cards":len(flat),
        "auction_calls":len(d.get("mb",[])),"play_cards":len(play),"claim":d.get("mc",[None])[0],
        "board_label":d.get("ah",[None])[0],"names_present":bool(d.get("pn"))}


def persist_partial(manifest, records):
    manifest["harvested_unique_valid_playable"] = len(records)
    (OUT/"manifest_partial.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
    (OUT/"all_harvest_manifest_partial.jsonl").write_text("".join(json.dumps({k:v for k,v in r.items() if k!="raw"},ensure_ascii=False)+"\n" for r in records),encoding="utf-8")


def main():
    s=requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; BBO100-public-archive-research/1.0)","Accept-Language":"en-US,en;q=0.8"})
    q={k:v[0] for k,v in parse_qs(urlparse(ROBOT_ARCHIVE).query).items()}
    ar=s.post(urljoin(BASE,"tarchive.php?offset=0"),data=q,timeout=TIMEOUT); ar.raise_for_status()
    tviews=[u for _,u in all_links(ar.text,ar.url) if "tview.php?" in u]
    manifest={
        "probe_version":PROBE_VERSION,"source":"BBO Robot Tournament Archive -> public MBT hand pages -> fetchlin.php",
        "archive_url":ROBOT_ARCHIVE,"archive_status":ar.status_code,"archive_bytes":len(ar.content),
        "tview_count_offset0":len(tviews),"target_raw_unique":TARGET_RAW_UNIQUE,"final_corpus":FINAL_CORPUS,
        "train_n":TRAIN_N,"holdout_n":FINAL_CORPUS-TRAIN_N,"split_seed":SPLIT_SEED,
        "tournaments_scanned":[],"rejected":{},"invalid_examples":{}}
    records=[]; deal_seen=set(); tournament_seen=set(); invalid_saved=set()

    for turl in tviews[:180]:
        if len(records)>=TARGET_RAW_UNIQUE: break
        try:
            tr=s.get(turl,timeout=TIMEOUT)
            if tr.status_code!=200: continue
            soup=BeautifulSoup(tr.text,"html.parser"); title_text=" ".join(soup.stripped_strings)
            mbt_pages=[u for _,u in all_links(tr.text,tr.url) if "/myhands/mbthands.php" in u]
            if not mbt_pages: continue
            tid=parse_qs(urlparse(turl).query).get("t",[turl])[0]
            if tid in tournament_seen: continue
            tournament_seen.add(tid)
            tmeta={"tournament_id":tid,"tview_url":turl,"title":title_text[:300],"participant_pages_found":len(mbt_pages),"participants":[],"accepted_unique":0}

            # Up to 4 participants per MBT event; duplicate deal hash prevents double counting.
            for player_url in mbt_pages[:4]:
                if len(records)>=TARGET_RAW_UNIQUE: break
                try:
                    pr=s.post(player_url,data={"offset":"0"},timeout=TIMEOUT,allow_redirects=True)
                    if pr.status_code!=200: continue
                    lin_links=[u for txt,u in all_links(pr.text,pr.url) if "fetchlin.php" in u.lower()]
                    pmeta={"player_page_url":player_url,"lin_links_found":len(lin_links),"accepted_unique":0}
                    for lin_url in lin_links:
                        if len(records)>=TARGET_RAW_UNIQUE: break
                        try:
                            lr=s.get(lin_url,timeout=TIMEOUT,allow_redirects=True); lr.raise_for_status(); raw=lr.text.strip()
                            ok,reason,info=validate_lin(raw)
                            if not ok:
                                manifest["rejected"][reason]=manifest["rejected"].get(reason,0)+1
                                if reason not in invalid_saved:
                                    invalid_saved.add(reason)
                                    manifest["invalid_examples"][reason]={"url":lin_url,"bytes":len(lr.content),"prefix":raw[:180]}
                                    (INVALID/f"{len(invalid_saved):02d}_{re.sub('[^A-Za-z0-9_.-]+','_',reason)}.txt").write_text(raw[:5000],encoding="utf-8")
                                continue
                            d=lin_nodes(raw); md=d["md"][0]; deal_sha=hashlib.sha256(md.encode()).hexdigest()
                            if deal_sha in deal_seen:
                                manifest["rejected"]["duplicate_deal"]=manifest["rejected"].get("duplicate_deal",0)+1
                                continue
                            deal_seen.add(deal_sha); raw_sha=hashlib.sha256(raw.encode()).hexdigest()
                            rid=f"bbo-{len(records)+1:03d}-{deal_sha[:12]}"; path=RAW/f"{rid}.lin"; path.write_text(raw+"\n",encoding="utf-8")
                            bq=parse_qs(urlparse(lin_url).query)
                            rec={"id":rid,"type":"lin","source_url":lin_url,"tview_url":turl,"tournament_id":tid,
                                 "tournament_title":title_text[:300],"player_page_url":player_url,
                                 "board_param":bq.get("board",[None])[0],"username":bq.get("username",[None])[0],
                                 "raw_path":str(path),"raw_sha256":raw_sha,"deal_sha256":deal_sha,"validation":info,"raw":raw}
                            records.append(rec); pmeta["accepted_unique"]+=1; tmeta["accepted_unique"]+=1
                            time.sleep(0.12)
                        except Exception as e:
                            key="fetch_error:"+type(e).__name__; manifest["rejected"][key]=manifest["rejected"].get(key,0)+1
                    tmeta["participants"].append(pmeta)
                except Exception as e:
                    key="participant_error:"+type(e).__name__; manifest["rejected"][key]=manifest["rejected"].get(key,0)+1
            manifest["tournaments_scanned"].append(tmeta)
            persist_partial(manifest,records)
        except Exception as e:
            key="tournament_error:"+type(e).__name__; manifest["rejected"][key]=manifest["rejected"].get(key,0)+1

    persist_partial(manifest,records)
    print(json.dumps({"probe_version":PROBE_VERSION,"unique":len(records),"tournaments":len(manifest["tournaments_scanned"]),"rejected":manifest["rejected"],"invalid_examples":manifest["invalid_examples"],"yield":[{"id":x["tournament_id"],"accepted":x["accepted_unique"],"participants":len(x["participants"]),"title":x["title"][:90]} for x in manifest["tournaments_scanned"]]},indent=2,ensure_ascii=False))
    if len(records)<FINAL_CORPUS:
        raise RuntimeError(f"only {len(records)} unique validated playable LIN records harvested")

    ranked=sorted(records,key=lambda r:hashlib.sha256((SPLIT_SEED+r["deal_sha256"]).encode()).hexdigest())
    selected=ranked[:FINAL_CORPUS]; train=selected[:TRAIN_N]; holdout=selected[TRAIN_N:]
    def seed_view(r):
        return {"id":r["id"],"type":"lin","source_url":r["source_url"],"board_name":r["validation"].get("board_label") or r["board_param"],"raw":r["raw"]}
    (OUT/"all_harvest_manifest.jsonl").write_text("".join(json.dumps({k:v for k,v in r.items() if k!="raw"},ensure_ascii=False)+"\n" for r in records),encoding="utf-8")
    (OUT/"selected_100_manifest.jsonl").write_text("".join(json.dumps({k:v for k,v in r.items() if k!="raw"},ensure_ascii=False)+"\n" for r in selected),encoding="utf-8")
    (OUT/"training_80_seeds.json").write_text(json.dumps([seed_view(r) for r in train],ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"HOLDOUT_20_DO_NOT_DDS.json").write_text(json.dumps([seed_view(r) for r in holdout],ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"holdout_20_manifest_no_raw.jsonl").write_text("".join(json.dumps({k:v for k,v in r.items() if k!="raw"},ensure_ascii=False)+"\n" for r in holdout),encoding="utf-8")
    manifest.update({"harvested_unique_valid_playable":len(records),"selected_100":100,"training_80":80,"holdout_20":20,
                     "selection_rule":"sort by sha256(split_seed + deal_sha256), take first 100; first 80 train, last 20 holdout",
                     "holdout_policy":"Do not run DDS or inspect hidden cards before blind assistant decisions are recorded."})
    (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({"SUCCESS":True,"selected_100":100,"training_80":80,"holdout_20":20,"harvested_unique":len(records)},indent=2))

if __name__=="__main__": main()
