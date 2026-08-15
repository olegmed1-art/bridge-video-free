#!/usr/bin/env python3
from __future__ import annotations

from database.runtime_worker_preflight import normalize_dsn

GOOD = (
    "postgresql://bridge_school_worker_principal:synthetic-password@"
    "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def expect_reject(value: str) -> None:
    try:
        normalize_dsn(value)
    except SystemExit:
        return
    raise AssertionError(f"expected rejection for: {value!r}")


def main() -> None:
    assert normalize_dsn(GOOD) == GOOD
    assert normalize_dsn(f'"{GOOD}"') == GOOD
    assert normalize_dsn(GOOD.replace("-pooler", "")) == GOOD
    assert normalize_dsn(
        GOOD.replace("ep-noisy-pine-b1pe30sf-pooler", "ep-wandering-night-b1ej3ow6-pooler")
    ) == GOOD

    bad_values = [
        "synthetic-password",
        "BRIDGE_WORKER_DATABASE_URL=" + GOOD,
        GOOD.replace("bridge_school_worker_principal", "bridge_school_app_principal"),
        GOOD.replace(
            "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech",
            "database.example.com",
        ),
        GOOD.replace("/neondb", "/otherdb"),
        GOOD.replace("sslmode=require", "sslmode=prefer"),
        GOOD.replace("channel_binding=require", "channel_binding=prefer"),
        GOOD.replace(":synthetic-password@", "@"),
    ]
    for value in bad_values:
        expect_reject(value)

    print("WORKER_DSN_CONTRACT: PASS")


if __name__ == "__main__":
    main()
