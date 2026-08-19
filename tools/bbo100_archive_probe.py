from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

BASE = "https://webutil.bridgebase.com/v2/"
ROBOT_ARCHIVE = "https://webutil.bridgebase.com/v2/tarchive.php?d=bbombadmin&h=bbombadmin&m=h"
OUT = Path("bbo100_probe")
OUT.mkdir(exist_ok=True)
PROBE_VERSION = 4
TIMEOUT = 45


def links_from_html(html: str, base: str):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tag, attr in (("a", "href"), ("script", "src"), ("link", "href"), ("form", "action")):
        for node in soup.find_all(tag):
            value = node.get(attr)
            if value:
                out.append(urljoin(base, value))
    return list(dict.fromkeys(out))


def page_record(r: requests.Response, label: str):
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    links = links_from_html(html, r.url)
    return {
        "label": label,
        "status": r.status_code,
        "final_url": r.url,
        "bytes": len(r.content),
        "sha256": hashlib.sha256(r.content).hexdigest(),
        "text_signature": " ".join(soup.stripped_strings)[:1800],
        "links": links[:400],
        "handviewer_links": [u for u in links if "handviewer" in u.lower() or "myhand=" in u.lower() or "lin=" in u.lower()][:200],
        "myhand_ids": list(dict.fromkeys(re.findall(r"[Mm]-\d+-\d+", html)))[:200],
        "movie_markers": len(re.findall(r"movie|handviewer|myhand=", html, flags=re.I)),
        "lin_markers": len(re.findall(r"(?:^|[?&])lin=|\|md\|", html, flags=re.I)),
    }


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; BBO100-public-archive-research/1.0)", "Accept-Language": "en-US,en;q=0.8"})

    q = {k: v[0] for k, v in parse_qs(urlparse(ROBOT_ARCHIVE).query).items()}
    ar = s.post(urljoin(BASE, "tarchive.php?offset=0"), data=q, timeout=TIMEOUT)
    ar.raise_for_status()
    archive_html = ar.text
    (OUT / "archive.html").write_text(archive_html, encoding="utf-8")
    archive_links = links_from_html(archive_html, ar.url)
    tviews = [u for u in archive_links if "tview.php?" in u]

    manifest = {"probe_version": PROBE_VERSION, "archive_status": ar.status_code, "archive_bytes": len(ar.content), "tview_count": len(tviews), "tournaments": [], "mbt_post_probes": []}
    hand_links = []
    for i, url in enumerate(tviews[:40], start=1):
        tr = s.get(url, timeout=TIMEOUT)
        tr.raise_for_status()
        soup = BeautifulSoup(tr.text, "html.parser")
        links = links_from_html(tr.text, tr.url)
        mh = [u for u in links if "/myhands/" in u.lower() and ("hands.php" in u.lower() or "mbthands.php" in u.lower())]
        hand_links.extend(mh)
        manifest["tournaments"].append({"url": url, "title_text": " ".join(soup.stripped_strings)[:350], "hand_links": mh[:100]})

    hand_links = list(dict.fromkeys(hand_links))
    mbt = [u for u in hand_links if "/mbthands.php" in u]
    normal = [u for u in hand_links if "/hands.php" in u]
    manifest["unique_hand_links"] = len(hand_links)
    manifest["mbt_links"] = len(mbt)
    manifest["normal_links"] = len(normal)

    # mbthands first GET is only a timezone form. Browser JS POSTs offset back to the same tournament/user URL.
    # Reproduce that public POST exactly, with several timezone offsets.
    for idx, url in enumerate(mbt[:12], start=1):
        for off in (0, -180, 180):
            label = f"mbt_{idx:02d}_post_{off:+d}"
            try:
                r = s.post(url, data={"offset": str(off)}, timeout=TIMEOUT, allow_redirects=True)
                rec = page_record(r, label)
                rec["requested_url"] = url
                rec["offset"] = off
                (OUT / f"{label}.html").write_text(r.text, encoding="utf-8")
                (OUT / f"{label}_links.txt").write_text("\n".join(rec["links"]), encoding="utf-8")
                manifest["mbt_post_probes"].append(rec)
            except Exception as e:
                manifest["mbt_post_probes"].append({"label": label, "requested_url": url, "offset": off, "error": repr(e)})

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "tviews.txt").write_text("\n".join(tviews), encoding="utf-8")
    (OUT / "hand_page_links.txt").write_text("\n".join(hand_links), encoding="utf-8")

    print(json.dumps({
        "probe_version": PROBE_VERSION,
        "archive_status": manifest["archive_status"],
        "tview_count": manifest["tview_count"],
        "unique_hand_links_from_40": manifest["unique_hand_links"],
        "mbt_links": manifest["mbt_links"],
        "normal_links": manifest["normal_links"],
        "mbt_post_summary": [
            {k: x.get(k) for k in ("label", "status", "bytes", "final_url", "myhand_ids", "handviewer_links", "movie_markers", "lin_markers", "text_signature", "error") if k in x}
            for x in manifest["mbt_post_probes"][:18]
        ],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
