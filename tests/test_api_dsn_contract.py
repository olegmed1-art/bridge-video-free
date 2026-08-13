from bridge_school_api.db import normalize_dsn


def main() -> None:
    good_values = [
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech:5432/neondb?sslmode=verify-full&channel_binding=require",
    ]
    for value in good_values:
        assert normalize_dsn(value) == value

    bad_values = [
        "",
        "password-only",
        "postgresql://bridge_school_app_principal@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
        "postgresql://wrong_role:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.example/neondb?sslmode=require&channel_binding=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/otherdb?sslmode=require&channel_binding=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech:6543/neondb?sslmode=require&channel_binding=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb?channel_binding=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=disable&channel_binding=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require&sslmode=disable&channel_binding=require",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=disable",
        "postgresql://bridge_school_app_principal:test-value@ep-example-pooler.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require#fragment",
    ]
    for value in bad_values:
        try:
            normalize_dsn(value)
        except (RuntimeError, ValueError):
            continue
        raise AssertionError(f"expected normalize_dsn() to reject {value!r}")


if __name__ == "__main__":
    main()
