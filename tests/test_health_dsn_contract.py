#!/usr/bin/env python3
from __future__ import annotations

from database.runtime_health_preflight import normalize_health_dsn

DIRECT = (
    "postgresql://bridge_school_health_principal:synthetic-password@"
    "ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)
POOLED = DIRECT.replace(
    "ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech",
    "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech",
)


def expect_reject(value: str) -> None:
    try:
        normalize_health_dsn(value)
    except SystemExit:
        return
    raise AssertionError(f"expected rejection for: {value!r}")


def main() -> None:
    assert normalize_health_dsn(DIRECT) == DIRECT
    assert normalize_health_dsn(POOLED) == POOLED
    assert normalize_health_dsn(f'"{DIRECT}"') == DIRECT

    bad_values = [
        "synthetic-password",
        "BRIDGE_HEALTH_DATABASE_URL=" + DIRECT,
        DIRECT.replace("bridge_school_health_principal", "bridge_school_app_principal"),
        DIRECT.replace("ep-noisy-pine-b1pe30sf", "ep-wandering-night-b1ej3ow6"),
        DIRECT.replace("/neondb", "/otherdb"),
        DIRECT.replace("sslmode=require", "sslmode=prefer"),
        DIRECT.replace("channel_binding=require", "channel_binding=prefer"),
        DIRECT.replace(":synthetic-password@", "@"),
        DIRECT + "#fragment",
    ]
    for value in bad_values:
        expect_reject(value)

    print("HEALTH_DSN_CONTRACT: PASS")


if __name__ == "__main__":
    main()
