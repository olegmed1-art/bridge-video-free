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
PROBE_VERSION = 3
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


def text_signature(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    txt = " ".join(soup.stripped_strings)
    return txt[:1200]


def add_query(url: str, **params) -> str:
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    for k, v in params.items():
        q[k] = [str(v)]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), p.fragment))


def diagnose_page(session: requests.Session, url: str, label: str):
    r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    html = r.text
    links = links_from_html(html, r.url)
    rec = {
        "label": label,
        "requested_url": url,
        "status": r.status_code,
        "final_url": r.url,
        "bytes": len(r.content),
        "sha256": hashlib.sha256(r.content).hexdigest(),
        "cookies": sorted(session.cookies.keys()),
        "title": BeautifulSoup(html, "html.parser").title.string.strip() if BeautifulSoup(html, "html.parser").title and BeautifulSoup(html, "html.parser").title.string else None,
        "text_signature": text_signature(html),
        "links": links[:250],
        "handviewer_links": [u for u in links if "handviewer" in u.lower() or "myhand=" in u.lower() or "lin=" in u.lower()][:100],
        "php_links": [u for u in links if ".php" in u.lower()][:150],
        "myhand_ids": list(dict.fromkeys(re.findall(r"[Mm]-\d+-\d+", html)))[:100],
        "lin_markers": len(re.findall(r"(?:^|[?&])lin=|\|md\|", html, flags=re.I)),
        "login_markers": sorted(set(m.lower() for m in re.findall(r"login|password|myhands_login", html, flags=re.I))),
    }
    (OUT / f"{label}.html").write_text(html, encoding="utf-8")
    (OUT / f"{label}_links.txt").write_text("\n".join(links), encoding="utf-8")
    return rec


def main():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; BBO100-public-archive-research/1.0)",
        "Accept-Language": "en-US,en;q=0.8",
    })

    q = {k: v[0] for k, v in parse_qs(urlparse(ROBOT_ARCHIVE).query).items()}
    r = s.post(urljoin(BASE, "tarchive.php?offset=0"), data=q, timeout=TIMEOUT)
    r.raise_for_status()
    archive_html = r.text
    (OUT / "archive.html").write_text(archive_html, encoding="utf-8")

    archive_links = links_from_html(archive_html, r.url)
    tviews = [u for u in archive_links if "tview.php?" in u]
    if not tviews:
        tviews = list(dict.fromkeys(re.findall(r"https?://[^\"'<>\s]*tview\.php\?[^\"'<>\s]+", archive_html)))

    manifest = {
        "probe_version": PROBE_VERSION,
        "archive_url": ROBOT_ARCHIVE,
        "post_url": r.url,
        "status": r.status_code,
        "archive_bytes": len(r.content),
        "archive_link_count": len(archive_links),
        "tview_count": len(tviews),
        "first_tviews": tviews[:30],
        "tournament_probes": [],
        "hand_page_probes": [],
    }

    hand_links = []
    for i, url in enumerate(tviews[:30], start=1):
        try:
            tr = s.get(url, timeout=TIMEOUT)
            tr.raise_for_status()
            html = tr.text
            soup = BeautifulSoup(html, "html.parser")
            links = links_from_html(html, tr.url)
            mh = [u for u in links if "/myhands/" in u.lower() and ("hands.php" in u.lower() or "mbthands.php" in u.lower())]
            hand_links.extend(mh)
            rec = {
                "url": url,
                "status": tr.status_code,
                "bytes": len(tr.content),
                "title_text": " ".join(soup.stripped_strings)[:350],
                "myhands_count": len(mh),
                "myhands_links": mh[:40],
            }
            (OUT / f"tview_{i:02d}.html").write_text(html, encoding="utf-8")
            manifest["tournament_probes"].append(rec)
        except Exception as e:
            manifest["tournament_probes"].append({"url": url, "error": repr(e)})

    hand_links = list(dict.fromkeys(hand_links))
    manifest["unique_hand_page_links_from_30_tournaments"] = len(hand_links)
    manifest["first_hand_page_links"] = hand_links[:30]

    # Probe both normal hands.php and mbthands.php if possible.
    selected = []
    for kind in ("/hands.php", "/mbthands.php"):
        selected.extend([u for u in hand_links if kind in u][:4])
    selected.extend(hand_links[:4])
    selected = list(dict.fromkeys(selected))[:12]

    for idx, url in enumerate(selected, start=1):
        variants = [(url, f"hand_{idx:02d}_base")]
        variants.append((add_query(url, from_login=0), f"hand_{idx:02d}_from0"))
        variants.append((add_query(url, from_login=1), f"hand_{idx:02d}_from1"))
        for vurl, label in variants:
            try:
                manifest["hand_page_probes"].append(diagnose_page(s, vurl, label))
            except Exception as e:
                manifest["hand_page_probes"].append({"label": label, "requested_url": vurl, "error": repr(e)})

    # Probe the documented Handviewer myhand mechanism with its public docs example.
    public_examples = [
        "https://www.bridgebase.com/tools/handviewer.html?myhand=M-103428497-1223755219",
        "https://www.bridgebase.com/tools/handviewer.html?bbo=y&myhand=M-3192794530-1580644538",
    ]
    manifest["handviewer_example_probes"] = []
    for idx, url in enumerate(public_examples, start=1):
        try:
            manifest["handviewer_example_probes"].append(diagnose_page(s, url, f"handviewer_example_{idx}"))
        except Exception as e:
            manifest["handviewer_example_probes"].append({"requested_url": url, "error": repr(e)})

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "tviews.txt").write_text("\n".join(tviews), encoding="utf-8")
    (OUT / "hand_page_links.txt").write_text("\n".join(hand_links), encoding="utf-8")

    # Keep log concise: detailed diagnostics are in the artifact.
    print(json.dumps({
        "probe_version": PROBE_VERSION,
        "archive_status": manifest["status"],
        "archive_bytes": manifest["archive_bytes"],
        "tview_count": manifest["tview_count"],
        "unique_hand_page_links_from_30_tournaments": manifest["unique_hand_page_links_from_30_tournaments"],
        "hand_page_probe_summary": [
            {k: rec.get(k) for k in ("label", "status", "final_url", "bytes", "title", "myhand_ids", "lin_markers", "login_markers", "error") if k in rec}
            for rec in manifest["hand_page_probes"]
        ],
        "handviewer_example_summary": [
            {k: rec.get(k) for k in ("label", "status", "final_url", "bytes", "title", "myhand_ids", "lin_markers", "login_markers", "error") if k in rec}
            for rec in manifest["handviewer_example_probes"]
        ],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
