"""Classify GitHub secret URI authorities against the current endpoint inventory.

Incident #1912. The existing runtime preflights rewrite Neon hosts before
connecting, so their authentication failures cannot identify the saved source.
An endpoint may have moved between branches: this classification does NOT
establish a credential's historical branch, password validity, or the separate
Vercel Production binding. Recheck live Neon endpoint mapping when reading it.
"""
from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlsplit


ENDPOINTS = {
    "current_default": "ep-noisy-pine-b1pe30sf",
    "current_legacy": "ep-nameless-heart-b1hgrnc8",
    "current_shadow": "ep-floral-field-b1pjs2of",
    "current_preview": "ep-wandering-night-b1ej3ow6",
}
SUFFIX = ".c-5.eu-central-1.aws.neon.tech"
KEYS = (
    "BRIDGE_APP_DATABASE_URL",
    "BRIDGE_WORKER_DATABASE_URL",
    "BRIDGE_HEALTH_DATABASE_URL",
)


def classify(raw: str) -> dict[str, object]:
    if not raw:
        return {"saved_uri_authority_endpoint_current_mapping": "missing", "authority_override": False}
    try:
        uri = raw.strip()
        if not uri.startswith(("postgres://", "postgresql://")):
            raise ValueError("invalid scheme")
        parsed = urlsplit(uri)
        if not parsed.hostname or not parsed.username or not parsed.password:
            raise ValueError("incomplete URI")
        if parsed.port not in (None, 5432):
            raise ValueError("unsupported port")
        host = parsed.hostname.lower()
        query = parse_qs(parsed.query, keep_blank_values=True)
        override = any(key in query for key in ("host", "hostaddr", "port", "service"))
        if override:
            source = "ambiguous_override"
        elif host.endswith(".neon.tech"):
            source = "other_neon"
            for branch, prefix in ENDPOINTS.items():
                if host == prefix + SUFFIX:
                    source = branch + "_direct"
                    break
                if host == prefix + "-pooler" + SUFFIX:
                    source = branch + "_pooler"
                    break
        else:
            source = "other_non_neon"
        return {"saved_uri_authority_endpoint_current_mapping": source, "authority_override": override}
    except (ValueError, TypeError):
        return {"saved_uri_authority_endpoint_current_mapping": "malformed", "authority_override": False}


def main() -> None:
    # No arbitrary URI, hostname, exception, password, or hash enters output.
    print(json.dumps({key: classify(os.environ.get(key, "")) for key in KEYS}, sort_keys=True))


if __name__ == "__main__":
    main()
