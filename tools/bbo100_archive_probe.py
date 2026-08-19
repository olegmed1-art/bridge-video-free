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
OUT.mkdir(exist_ok=True)
RAW.mkdir(exist_ok=True)
PROBE_VERSION = 5
TIMEOUT = 45
TARGET_RAW_UNIQUE = 130
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
    cards = []
    suit = None
    for ch in h.upper().replace("10", "T"):
        if ch in SUITS:
            suit = ch
        elif ch in RANKS:
            if suit is None:
                raise ValueError("rank before suit")
            cards.append(suit + ch)
        elif ch in " ,-\r\n\t":
            continue
        else:
            raise ValueError(f"bad holding char {ch!r}")
    return cards


def validate_lin(raw: str):
    d = lin_nodes(raw)
    if not d.get("md"):
        return False, "missing_md", {}
    md = d["md"][0]
    if not md or md[0] not in "1234":
        return False, "bad_dealer", {}
    hs = md[1:].strip(",").split(",") if md[1:].strip(",") else []
    if len(hs) not in (3, 4):
        return False, f"holding_count_{len(hs)}", {}
    try:
        groups = [cards_in_holding(h) for h in hs]
    except Exception as e:
        return False, f"holding_parse:{e}", {}
    flat = [c for g in groups for c in g]
    if len(flat) != len(set(flat)):
        return False, "duplicate_cards", {}
    if len(hs) == 3 and len(flat) != 39:
        return False, f"three_hands_have_{len(flat)}", {}
    if len(hs) == 4 and len(flat) != 52:
        return False, f"four_hands_have_{len(flat)}", {}
    play = [x.upper() for x in d.get("pc", [])]
    if play and any(not re.fullmatch(r"[SHDC](?:[2-9TJQKA]|10)", x) for x in play):
        return False, "bad_play_card", {}
    # Exclude pass-outs / records with no meaningful play from the 100-deal training corpus.
    if len(play) < 4:
        return False, "no_meaningful_play", {"play_cards": len(play)}
    info = {
        "dealer_code": md[0],
        "three_or_four_hands": len(hs),
        "known_cards": len(flat),
        "auction_calls": len(d.get("mb", [])),
        "play_cards": len(play),
        "claim": d.get("mc", [None])[0],
        "board_label": d.get("ah", [None])[0],
        "names_present": bool(d.get("pn")),
    }
    return True, "ok", info


def main():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; BBO100-public-archive-research/1.0)",
        "Accept-Language": "en-US,en;q=0.8",
    })

    q = {k: v[0] for k, v in parse_qs(urlparse(ROBOT_ARCHIVE).query).items()}
    ar = s.post(urljoin(BASE, "tarchive.php?offset=0"), data=q, timeout=TIMEOUT)
    ar.raise_for_status()
    archive_links = all_links(ar.text, ar.url)
    tviews = [u for _, u in archive_links if "tview.php?" in u]

    manifest = {
        "probe_version": PROBE_VERSION,
        "source": "BBO Robot Tournament Archive -> public MBT hand pages -> fetchlin.php",
        "archive_url": ROBOT_ARCHIVE,
        "archive_status": ar.status_code,
        "archive_bytes": len(ar.content),
        "tview_count_offset0": len(tviews),
        "target_raw_unique": TARGET_RAW_UNIQUE,
        "final_corpus": FINAL_CORPUS,
        "train_n": TRAIN_N,
        "holdout_n": FINAL_CORPUS - TRAIN_N,
        "split_seed": SPLIT_SEED,
        "tournaments_scanned": [],
        "rejected": {},
    }

    records = []
    deal_seen = set()
    tournament_seen = set()

    # Scan recent robot tournaments. Prefer one human participant per MBT tournament
    # to reduce within-tournament dependence; move to the next tournament once that page is harvested.
    for tindex, turl in enumerate(tviews[:250], start=1):
        if len(records) >= TARGET_RAW_UNIQUE:
            break
        tr = s.get(turl, timeout=TIMEOUT)
        if tr.status_code != 200:
            continue
        soup = BeautifulSoup(tr.text, "html.parser")
        title_text = " ".join(soup.stripped_strings)
        links = all_links(tr.text, tr.url)
        mbt_pages = [u for _, u in links if "/myhands/mbthands.php" in u]
        if not mbt_pages:
            continue
        # tview tournament id is the stable unit; only take one participant page per tournament.
        tq = parse_qs(urlparse(turl).query)
        tid = tq.get("t", [turl])[0]
        if tid in tournament_seen:
            continue
        tournament_seen.add(tid)
        player_url = mbt_pages[0]

        pr = s.post(player_url, data={"offset": "0"}, timeout=TIMEOUT, allow_redirects=True)
        if pr.status_code != 200:
            continue
        plinks = all_links(pr.text, pr.url)
        lin_links = [(txt, u) for txt, u in plinks if "fetchlin.php" in u.lower()]
        tmeta = {
            "tournament_id": tid,
            "tview_url": turl,
            "title": title_text[:300],
            "player_page_url": player_url,
            "lin_links_found": len(lin_links),
            "accepted_unique": 0,
        }

        for link_index, (_, lin_url) in enumerate(lin_links, start=1):
            if len(records) >= TARGET_RAW_UNIQUE:
                break
            try:
                lr = s.get(lin_url, timeout=TIMEOUT, allow_redirects=True)
                lr.raise_for_status()
                raw = lr.text.strip()
                ok, reason, info = validate_lin(raw)
                if not ok:
                    manifest["rejected"][reason] = manifest["rejected"].get(reason, 0) + 1
                    continue
                d = lin_nodes(raw)
                md = d["md"][0]
                deal_sha = hashlib.sha256(md.encode("utf-8")).hexdigest()
                if deal_sha in deal_seen:
                    manifest["rejected"]["duplicate_deal"] = manifest["rejected"].get("duplicate_deal", 0) + 1
                    continue
                deal_seen.add(deal_sha)
                raw_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                rid = f"bbo-{len(records)+1:03d}-{deal_sha[:12]}"
                path = RAW / f"{rid}.lin"
                path.write_text(raw + "\n", encoding="utf-8")
                board_q = parse_qs(urlparse(lin_url).query)
                rec = {
                    "id": rid,
                    "type": "lin",
                    "source_url": lin_url,
                    "tview_url": turl,
                    "tournament_id": tid,
                    "tournament_title": title_text[:300],
                    "player_page_url": player_url,
                    "board_param": board_q.get("board", [None])[0],
                    "username": board_q.get("username", [None])[0],
                    "raw_path": str(path),
                    "raw_sha256": raw_sha,
                    "deal_sha256": deal_sha,
                    "validation": info,
                    "raw": raw,
                }
                records.append(rec)
                tmeta["accepted_unique"] += 1
                time.sleep(0.03)
            except Exception as e:
                key = "fetch_error:" + type(e).__name__
                manifest["rejected"][key] = manifest["rejected"].get(key, 0) + 1
        manifest["tournaments_scanned"].append(tmeta)

    if len(records) < FINAL_CORPUS:
        raise RuntimeError(f"only {len(records)} unique validated playable LIN records harvested")

    # Deterministic split created before any DDS analysis. It depends only on content hashes.
    ranked = sorted(records, key=lambda r: hashlib.sha256((SPLIT_SEED + r["deal_sha256"]).encode()).hexdigest())
    selected = ranked[:FINAL_CORPUS]
    train = selected[:TRAIN_N]
    holdout = selected[TRAIN_N:]

    def seed_view(r):
        return {"id": r["id"], "type": "lin", "source_url": r["source_url"], "board_name": r["validation"].get("board_label") or r["board_param"], "raw": r["raw"]}

    (OUT / "all_harvest_manifest.jsonl").write_text("".join(json.dumps({k:v for k,v in r.items() if k != "raw"}, ensure_ascii=False)+"\n" for r in records), encoding="utf-8")
    (OUT / "selected_100_manifest.jsonl").write_text("".join(json.dumps({k:v for k,v in r.items() if k != "raw"}, ensure_ascii=False)+"\n" for r in selected), encoding="utf-8")
    (OUT / "training_80_seeds.json").write_text(json.dumps([seed_view(r) for r in train], ensure_ascii=False, indent=2), encoding="utf-8")
    # Full raw holdout is intentionally isolated and must not be DDS-inspected before blind assistant choices.
    (OUT / "HOLDOUT_20_DO_NOT_DDS.json").write_text(json.dumps([seed_view(r) for r in holdout], ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "holdout_20_manifest_no_raw.jsonl").write_text("".join(json.dumps({k:v for k,v in r.items() if k != "raw"}, ensure_ascii=False)+"\n" for r in holdout), encoding="utf-8")

    manifest["harvested_unique_valid_playable"] = len(records)
    manifest["selected_100"] = len(selected)
    manifest["training_80"] = len(train)
    manifest["holdout_20"] = len(holdout)
    manifest["selection_rule"] = "sort by sha256(split_seed + deal_sha256), take first 100; first 80 train, last 20 holdout"
    manifest["holdout_policy"] = "Do not run DDS or inspect hidden cards before blind assistant decisions are recorded."
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({
        "probe_version": PROBE_VERSION,
        "archive_status": manifest["archive_status"],
        "tview_count_offset0": manifest["tview_count_offset0"],
        "mbt_tournaments_scanned": len(manifest["tournaments_scanned"]),
        "harvested_unique_valid_playable": len(records),
        "selected_100": len(selected),
        "training_80": len(train),
        "holdout_20": len(holdout),
        "rejected": manifest["rejected"],
        "tournament_yield": [{"id": x["tournament_id"], "lin": x["lin_links_found"], "accepted": x["accepted_unique"], "title": x["title"][:80]} for x in manifest["tournaments_scanned"]],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
