from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://webutil.bridgebase.com/v2/"
ROBOT_ARCHIVE = "https://webutil.bridgebase.com/v2/tarchive.php?d=bbombadmin&h=bbombadmin&m=h"
OUT = Path("bbo100_probe")
OUT.mkdir(exist_ok=True)


def links_from_html(html: str, base: str):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        out.append(urljoin(base, a["href"]))
    return list(dict.fromkeys(out))


def main():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; BBO100-public-archive-research/1.0)",
        "Accept-Language": "en-US,en;q=0.8",
    })

    q = {k: v[0] for k, v in parse_qs(urlparse(ROBOT_ARCHIVE).query).items()}
    r = s.post(urljoin(BASE, "tarchive.php?offset=0"), data=q, timeout=45)
    r.raise_for_status()
    archive_html = r.text
    (OUT / "archive.html").write_text(archive_html, encoding="utf-8")

    archive_links = links_from_html(archive_html, r.url)
    tviews = [u for u in archive_links if "tview.php?" in u]
    if not tviews:
        # Keep any textual tournament ids for diagnostics if BBO changed markup.
        tviews = list(dict.fromkeys(re.findall(r"https?://[^\"'<>\s]*tview\.php\?[^\"'<>\s]+", archive_html)))

    manifest = {
        "archive_url": ROBOT_ARCHIVE,
        "post_url": r.url,
        "status": r.status_code,
        "archive_bytes": len(r.content),
        "archive_link_count": len(archive_links),
        "tview_count": len(tviews),
        "tviews": tviews,
        "tournament_probes": [],
    }

    for i, url in enumerate(tviews[:20], start=1):
        try:
            tr = s.get(url, timeout=45)
            rec = {
                "url": url,
                "status": tr.status_code,
                "final_url": tr.url,
                "bytes": len(tr.content),
            }
            tr.raise_for_status()
            html = tr.text
            (OUT / f"tview_{i:02d}.html").write_text(html, encoding="utf-8")
            links = links_from_html(html, tr.url)
            rec["link_count"] = len(links)
            rec["myhands_links"] = [u for u in links if "myhands" in u.lower() or "hands.php" in u.lower()][:200]
            rec["handviewer_links"] = [u for u in links if "handviewer" in u.lower() or "lin=" in u.lower()][:200]
            rec["lin_markers"] = len(re.findall(r"(?:^|[?&])lin=", html, flags=re.I))
            # Save a compact href dump so later debugging need not parse full HTML.
            (OUT / f"tview_{i:02d}_links.txt").write_text("\n".join(links), encoding="utf-8")
            manifest["tournament_probes"].append(rec)
        except Exception as e:
            manifest["tournament_probes"].append({"url": url, "error": repr(e)})

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "tviews.txt").write_text("\n".join(tviews), encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
