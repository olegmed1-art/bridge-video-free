from __future__ import annotations

import os

from bridge_school_api.db import (
    DatabaseConfigurationError,
    PREVIEW_DIRECT_HOST,
    PREVIEW_HOST,
    PRODUCTION_DIRECT_HOST,
    PRODUCTION_HOST,
    normalize_dsn,
)


def make_dsn(host: str, *, sslmode: str = "require", channel_binding: str = "require") -> str:
    return (
        f"postgresql://bridge_school_app_principal:test-value@{host}/neondb"
        f"?sslmode={sslmode}&channel_binding={channel_binding}"
    )


def expect_reject(value: str) -> None:
    try:
        normalize_dsn(value)
    except (DatabaseConfigurationError, ValueError):
        return
    raise AssertionError(f"expected normalize_dsn() to reject {value!r}")


def main() -> None:
    production = make_dsn(PRODUCTION_HOST)
    preview = make_dsn(PREVIEW_HOST, sslmode="verify-full")
    production_direct = make_dsn(PRODUCTION_DIRECT_HOST)
    preview_direct = make_dsn(PREVIEW_DIRECT_HOST, sslmode="verify-full")

    original_env = os.environ.get("VERCEL_ENV")
    try:
        os.environ.pop("VERCEL_ENV", None)
        assert normalize_dsn(production) == production
        assert normalize_dsn(preview) == preview
        assert normalize_dsn(production_direct) == production
        assert normalize_dsn(preview_direct) == preview

        os.environ["VERCEL_ENV"] = "production"
        assert normalize_dsn(production) == production
        assert normalize_dsn(production_direct) == production
        expect_reject(preview)

        os.environ["VERCEL_ENV"] = "preview"
        assert normalize_dsn(preview) == preview
        assert normalize_dsn(preview_direct) == preview
        expect_reject(production)

        os.environ.pop("VERCEL_ENV", None)
        bad_values = [
            "",
            "password-only",
            production.replace(":test-value@", "@"),
            production.replace("bridge_school_app_principal", "wrong_role"),
            production.replace(PRODUCTION_HOST, "ep-example-pooler.c-5.eu-central-1.aws.neon.tech"),
            production.replace("/neondb", "/otherdb"),
            production.replace(f"@{PRODUCTION_HOST}/", f"@{PRODUCTION_HOST}:6543/"),
            production.split("?")[0],
            production.replace("&channel_binding=require", ""),
            production.replace("sslmode=require", "sslmode=disable"),
            production.replace("channel_binding=require", "channel_binding=disable"),
            production.replace("sslmode=require", "sslmode=require&sslmode=disable"),
            production + "#fragment",
        ]
        for value in bad_values:
            expect_reject(value)
    finally:
        if original_env is None:
            os.environ.pop("VERCEL_ENV", None)
        else:
            os.environ["VERCEL_ENV"] = original_env

    print("API_DSN_CONTRACT: PASS")


if __name__ == "__main__":
    main()
