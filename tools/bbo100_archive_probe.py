from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

OUT = Path("bbo100_probe")
RAW = OUT / "raw_lin"
OUT.mkdir(exist_ok=True)
RAW.mkdir(exist_ok=True)
PROBE_VERSION = 9
TIMEOUT = 45
ROBOT_ARCHIVE = "https://webutil.bridgebase.com/v2/tarchive.php?d=bbombadmin&h=bbombadmin&m=h"
ARCHIVE_POST = "https://webutil.bridgebase.com/v2/tarchive.php?offset=0"
FETCH_INTERVAL_SEC = 7.0  # measured public limit: 5 fetchlin requests per ~30 s
RATE_RECOVERY_SEC = 31.0
TARGET_UNIQUE = 105
FINAL_CORPUS = 100
TRAIN_N = 80
CANDIDATE_TARGET = 230
SPLIT_SEED = "BBO100-fresh-public-MBT-2026-08-19-v2"
SKIP_TOURNAMENTS = {"36084-1787145301"}  # used in rate-limit diagnostics; exclude from fresh corpus
SUITS = "SHDC"
RANKS = "23456789TJQKA"


def links(html: str, base: str):
    soup = BeautifulSoup(html, "html.parser")
    return [(" ".join(a.stripped_strings), urljoin(base, a.get("href"))) for a in soup.find_all("a", href=True)]


def lin_nodes(raw: str):
    parts = raw.strip().split("|")
    out = {}
    for i in range(0, len(parts) - 1, 2):
        key = parts[i].lower()
        if key:
            out.setdefault(key, []).append(parts[i + 1])
    return out


def holding_cards(text: str):
    cards, suit = [], None
    for ch in text.upper().replace("10", "T"):
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
    hands = md[1:].strip(",").split(",") if md[1:].strip(",") else []
    if len(hands) not in (3, 4):
        return False, f"holding_count_{len(hands)}", {}
    try:
        groups = [holding_cards(h) for h in hands]
    except Exception as exc:
        return False, f"holding_parse_{type(exc).__name__}", {}
    flat = [c for g in groups for c in g]
    if len(flat) != len(set(flat)):
        return False, "duplicate_cards", {}
    expected = 39 if len(hands) == 3 else 52
    if len(flat) != expected:
        return False, f"known_cards_{len(flat)}", {}
    play = [x.upper() for x in d.get("pc", [])]
    if any(not re.fullmatch(r"[SHDC](?:[2-9TJQKA]|10)", x) for x in play):
        return False, "bad_play_card", {}
    if len(play) < 4:
        return False, "no_meaningful_play", {"play_cards": len(play)}
    return True, "ok", {
        "dealer_code": md[0],
        "known_cards": len(flat),
        "auction_calls": len(d.get("mb", [])),
        "play_cards": len(play),
        "claim": d.get("mc", [None])[0],
        "board_label": d.get("ah", [None])[0],
        "names": d.get("pn", [None])[0],
    }


def save_partial(state, records, candidates):
    public_state = dict(state)
    public_state["accepted_unique"] = len(records)
    public_state["candidate_urls"] = len(candidates)
    (OUT / "manifest_partial.json").write_text(json.dumps(public_state, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "accepted_partial_manifest.jsonl").write_text(
        "".join(json.dumps({k: v for k, v in r.items() if k != "raw"}, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; BBO100-public-archive-research/1.0)",
        "Accept-Language": "en-US,en;q=0.8",
    })

    archive_params = {k: v[0] for k, v in parse_qs(urlparse(ROBOT_ARCHIVE).query).items()}
    ar = session.post(ARCHIVE_POST, data=archive_params, timeout=TIMEOUT)
    ar.raise_for_status()
    tviews = [u for _, u in links(ar.text, ar.url) if "tview.php?" in u]

    state = {
        "probe_version": PROBE_VERSION,
        "source": "BBO Robot Tournament Archive -> public MBT result pages -> public fetchlin.php",
        "archive_status": ar.status_code,
        "archive_bytes": len(ar.content),
        "tview_count_offset0": len(tviews),
        "rate_policy": {"fetch_interval_sec": FETCH_INTERVAL_SEC, "rate_recovery_sec": RATE_RECOVERY_SEC},
        "target_unique": TARGET_UNIQUE,
        "final_corpus": FINAL_CORPUS,
        "train_n": TRAIN_N,
        "holdout_n": FINAL_CORPUS - TRAIN_N,
        "split_seed": SPLIT_SEED,
        "skip_tournaments": sorted(SKIP_TOURNAMENTS),
        "metadata_tournaments": [],
        "rejected": {},
        "http_status_counts": {},
        "rate_429_count": 0,
    }

    # Phase 1: gather public LIN URLs only. No card data is fetched here.
    candidates = []
    seen_urls = set()
    for turl in tviews[:80]:
        if len(candidates) >= CANDIDATE_TARGET:
            break
        tid = parse_qs(urlparse(turl).query).get("t", [turl])[0]
        if tid in SKIP_TOURNAMENTS:
            continue
        tr = session.get(turl, timeout=TIMEOUT)
        if tr.status_code != 200:
            continue
        soup = BeautifulSoup(tr.text, "html.parser")
        title = " ".join(soup.stripped_strings)[:240]
        participant_pages = [u for _, u in links(tr.text, tr.url) if "/myhands/mbthands.php" in u]
        if not participant_pages:
            continue
        tmeta = {"tournament_id": tid, "title": title, "participants": 0, "candidate_lin_urls": 0}
        for player_url in participant_pages:
            if len(candidates) >= CANDIDATE_TARGET:
                break
            pr = session.post(player_url, data={"offset": "0"}, timeout=TIMEOUT, allow_redirects=True)
            if pr.status_code != 200:
                continue
            tmeta["participants"] += 1
            for _, lin_url in links(pr.text, pr.url):
                if "fetchlin.php" not in lin_url.lower() or lin_url in seen_urls:
                    continue
                seen_urls.add(lin_url)
                candidates.append({"lin_url": lin_url, "player_page_url": player_url, "tview_url": turl, "tournament_id": tid, "tournament_title": title})
                tmeta["candidate_lin_urls"] += 1
                if len(candidates) >= CANDIDATE_TARGET:
                    break
            time.sleep(0.08)
        state["metadata_tournaments"].append(tmeta)
        time.sleep(0.1)

    (OUT / "candidate_urls_manifest.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in candidates), encoding="utf-8"
    )
    print(json.dumps({"phase": "metadata", "candidate_urls": len(candidates), "tournaments": len(state["metadata_tournaments"])}, ensure_ascii=False), flush=True)
    if len(candidates) < FINAL_CORPUS:
        raise RuntimeError(f"only {len(candidates)} candidate LIN URLs found")

    # Phase 2: fetch LIN at a deliberately slower rate than the measured public limit.
    records = []
    deal_seen = set()
    last_fetch_started = None
    for idx, candidate in enumerate(candidates, start=1):
        if len(records) >= TARGET_UNIQUE:
            break
        url = candidate["lin_url"]
        attempts = 0
        while True:
            attempts += 1
            if last_fetch_started is not None:
                wait = FETCH_INTERVAL_SEC - (time.monotonic() - last_fetch_started)
                if wait > 0:
                    time.sleep(wait)
            last_fetch_started = time.monotonic()
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True, headers={"Referer": candidate["player_page_url"]})
            status_key = str(r.status_code)
            state["http_status_counts"][status_key] = state["http_status_counts"].get(status_key, 0) + 1
            if r.status_code == 429:
                state["rate_429_count"] += 1
                print(json.dumps({"phase": "fetch", "candidate": idx, "status": 429, "action": f"sleep {RATE_RECOVERY_SEC}s then retry"}), flush=True)
                time.sleep(RATE_RECOVERY_SEC)
                last_fetch_started = None
                if attempts < 4:
                    continue
            if r.status_code != 200:
                key = f"http_{r.status_code}"
                state["rejected"][key] = state["rejected"].get(key, 0) + 1
                break
            raw = r.text.strip()
            ok, reason, info = validate_lin(raw)
            if not ok:
                state["rejected"][reason] = state["rejected"].get(reason, 0) + 1
                break
            d = lin_nodes(raw)
            md = d["md"][0]
            deal_sha = hashlib.sha256(md.encode("utf-8")).hexdigest()
            if deal_sha in deal_seen:
                state["rejected"]["duplicate_deal"] = state["rejected"].get("duplicate_deal", 0) + 1
                break
            deal_seen.add(deal_sha)
            raw_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            rid = f"bbo-{len(records)+1:03d}-{deal_sha[:12]}"
            raw_path = RAW / f"{rid}.lin"
            raw_path.write_text(raw + "\n", encoding="utf-8")
            q = parse_qs(urlparse(url).query)
            rec = {
                "id": rid,
                "type": "lin",
                "source_url": url,
                "tview_url": candidate["tview_url"],
                "tournament_id": candidate["tournament_id"],
                "tournament_title": candidate["tournament_title"],
                "player_page_url": candidate["player_page_url"],
                "board_param": q.get("board", [None])[0],
                "username": q.get("username", [None])[0],
                "raw_path": str(raw_path),
                "raw_sha256": raw_sha,
                "deal_sha256": deal_sha,
                "validation": info,
                "raw": raw,
            }
            records.append(rec)
            if len(records) % 10 == 0 or len(records) == TARGET_UNIQUE:
                print(json.dumps({"phase": "fetch", "accepted_unique": len(records), "candidate_index": idx, "rejected": state["rejected"], "429": state["rate_429_count"]}, ensure_ascii=False), flush=True)
                save_partial(state, records, candidates)
            break

    save_partial(state, records, candidates)
    if len(records) < FINAL_CORPUS:
        raise RuntimeError(f"only {len(records)} unique validated playable LIN records harvested")

    # Phase 3: split BEFORE any DDS analysis. Selection depends only on content hashes.
    ranked = sorted(records, key=lambda r: hashlib.sha256((SPLIT_SEED + r["deal_sha256"]).encode("utf-8")).hexdigest())
    selected = ranked[:FINAL_CORPUS]
    train = selected[:TRAIN_N]
    holdout = selected[TRAIN_N:]

    def seed_view(r):
        return {
            "id": r["id"],
            "type": "lin",
            "source_url": r["source_url"],
            "board_name": r["validation"].get("board_label") or r["board_param"],
            "raw": r["raw"],
        }

    def no_raw(r):
        return {k: v for k, v in r.items() if k != "raw"}

    (OUT / "all_harvest_manifest.jsonl").write_text("".join(json.dumps(no_raw(r), ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    (OUT / "selected_100_manifest.jsonl").write_text("".join(json.dumps(no_raw(r), ensure_ascii=False) + "\n" for r in selected), encoding="utf-8")
    (OUT / "training_80_seeds.json").write_text(json.dumps([seed_view(r) for r in train], ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "HOLDOUT_20_DO_NOT_DDS.json").write_text(json.dumps([seed_view(r) for r in holdout], ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "holdout_20_manifest_no_raw.jsonl").write_text("".join(json.dumps(no_raw(r), ensure_ascii=False) + "\n" for r in holdout), encoding="utf-8")

    state.update({
        "accepted_unique": len(records),
        "selected_100": len(selected),
        "training_80": len(train),
        "holdout_20": len(holdout),
        "selection_rule": "sort by sha256(split_seed + deal_sha256), take first 100; first 80 train, last 20 holdout",
        "holdout_policy": "No DDS and no hidden-hand inspection until blind assistant decisions are recorded.",
    })
    (OUT / "manifest.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"SUCCESS": True, "accepted_unique": len(records), "selected_100": 100, "training_80": 80, "holdout_20": 20, "rate_429_count": state["rate_429_count"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
