#!/usr/bin/env python3
from __future__ import annotations

from starlette.responses import Response

from bridge_school_api.main import apply_response_security_headers


def main() -> None:
    protected = Response()
    protected.headers["Vercel-CDN-Cache-Control"] = "public, max-age=60"
    protected.headers["CDN-Cache-Control"] = "public, max-age=60"
    apply_response_security_headers("/v1/students", protected)
    assert protected.headers["cache-control"] == "private, no-store, max-age=0"
    assert protected.headers["pragma"] == "no-cache"
    assert protected.headers["x-content-type-options"] == "nosniff"
    assert protected.headers["referrer-policy"] == "no-referrer"
    assert "vercel-cdn-cache-control" not in protected.headers
    assert "cdn-cache-control" not in protected.headers

    public = Response()
    public.headers["Cache-Control"] = "public, max-age=15"
    apply_response_security_headers("/healthz", public)
    assert public.headers["cache-control"] == "public, max-age=15"
    assert public.headers["x-content-type-options"] == "nosniff"
    assert public.headers["referrer-policy"] == "no-referrer"

    print("API_RESPONSE_HEADERS: PASS")


if __name__ == "__main__":
    main()
