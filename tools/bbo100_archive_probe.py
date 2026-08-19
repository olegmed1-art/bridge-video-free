from __future__ import annotations

import json
import time
from pathlib import Path

import requests

OUT = Path("bbo100_probe")
OUT.mkdir(exist_ok=True)
PROBE_VERSION = 7
TIMEOUT = 45
BASE = "https://www.bridgebase.com/myhands/fetchlin.php"
MBTID = "36084-1787145301"
USERNAME = "azat1_"


def main():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; BBO100-public-archive-research/1.0)",
        "Accept-Language": "en-US,en;q=0.8",
        "Referer": f"https://www.bridgebase.com/myhands/mbthands.php?tourney={MBTID}-&username={USERNAME}",
    })
    rows = []
    for board in range(1, 9):
        url = f"{BASE}?mbtid={MBTID}&board={board}&username={USERNAME}"
        r = s.get(url, timeout=TIMEOUT, allow_redirects=True)
        row = {
            "board": board,
            "status": r.status_code,
            "bytes": len(r.content),
            "retry_after": r.headers.get("Retry-After"),
            "server": r.headers.get("Server"),
            "content_type": r.headers.get("Content-Type"),
            "cache_control": r.headers.get("Cache-Control"),
            "body_prefix": r.text[:240],
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        time.sleep(1.0)
    (OUT / "rate_probe.json").write_text(json.dumps({"probe_version": PROBE_VERSION, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__":
    main()
