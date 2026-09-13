#!/usr/bin/env python3
from __future__ import annotations

from app import _replace_vercel_oidc_header


def main() -> None:
    incoming = [
        (b"accept", b"application/json"),
        (b"x-vercel-oidc-token", b"client-supplied-token"),
        (b"x-other", b"kept"),
    ]
    trusted = _replace_vercel_oidc_header(incoming, "deployment-oidc-token")
    assert (b"accept", b"application/json") in trusted
    assert (b"x-other", b"kept") in trusted
    oidc = [value for key, value in trusted if key.lower() == b"x-vercel-oidc-token"]
    assert oidc == [b"deployment-oidc-token"], oidc

    absent = _replace_vercel_oidc_header(incoming, "")
    assert not [value for key, value in absent if key.lower() == b"x-vercel-oidc-token"]
    print("VERCEL_OIDC_CONTEXT_CONTRACT: PASS")


if __name__ == "__main__":
    main()
