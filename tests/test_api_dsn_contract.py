from bridge_school_api.db import normalize_dsn


def main() -> None:
    good = "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.example/neondb?sslmode=require"
    assert normalize_dsn(good) == good

    bad_values = [
        "",
        "password-only",
        "postgresql://bridge_school_app_principal@ep-example-pooler.example/neondb",
        "postgresql://wrong_role:test-value@ep-example-pooler.example/neondb",
        "postgresql://bridge_school_app_principal:test-value@ep-example.example/neondb",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.example/otherdb",
    ]
    for value in bad_values:
        try:
            normalize_dsn(value)
        except RuntimeError:
            continue
        raise AssertionError(f"expected normalize_dsn() to reject {value!r}")


if __name__ == "__main__":
    main()
