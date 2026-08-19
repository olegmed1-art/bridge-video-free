from __future__ import annotations

import json
import time
from pathlib import Path

import requests

OUT = Path("bbo100_probe")
OUT.mkdir(exist_ok=True)
PROBE_VERSION = 8
TIMEOUT = 45
BASE = "https://www.bridgebase.com/myhands/fetchlin.php"
MBTID = "36084-1787145301"
USERNAME = "azat1_"


def fetch(s, board, t0, label):
    url = f"{BASE}?mbtid={MBTID}&board={board}&username={USERNAME}"
    r = s.get(url, timeout=TIMEOUT, allow_redirects=True)
    row = {
        "label": label,
        "elapsed_sec": round(time.monotonic() - t0, 3),
        "board": board,
        "status": r.status_code,
        "bytes": len(r.content),
        "retry_after": r.headers.get("Retry-After"),
        "body_prefix": r.text[:180],
    }
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


def main():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; BBO100-public-archive-research/1.0)",
        "Accept-Language": "en-US,en;q=0.8",
        "Referer": f"https://www.bridgebase.com/myhands/mbthands.php?tourney={MBTID}-&username={USERNAME}",
    })
    rows=[]; t0=time.monotonic()
    for board in range(1,6):
        rows.append(fetch(s, board, t0, f"initial_{board}"))
        time.sleep(0.5)
    rows.append(fetch(s, 6, t0, "limit_confirmation"))
    time.sleep(30)
    rows.append(fetch(s, 6, t0, "after_30s"))
    if rows[-1]["status"] == 429:
        time.sleep(31)
        rows.append(fetch(s, 6, t0, "after_61s_total"))
    if rows[-1]["status"] == 429:
        time.sleep(60)
        rows.append(fetch(s, 6, t0, "after_121s_total"))
    (OUT/"rate_window_probe.json").write_text(json.dumps({"probe_version":PROBE_VERSION,"rows":rows},indent=2,ensure_ascii=False),encoding="utf-8")

if __name__=="__main__": main()
