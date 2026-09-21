"""Read-only release receipt verification under the worker's restricted login."""
import json
import os

import psycopg
from psycopg.rows import dict_row

from .parallel_work_intake import canonical_manifest_text, manifest_sha256


def validate_receipt(row):
    count = len(json.loads(canonical_manifest_text())["items"])
    if (not row or row.get("manifest_sha256") != manifest_sha256()
            or type(row.get("item_count")) is not int or row["item_count"] != count
            or type(row.get("registered_count")) is not int
            or not 0 <= row["registered_count"] <= count
            or row.get("replayed") is not True):
        raise ValueError("AUTOPILOT_ROLLOUT_MANIFEST_RECEIPT_INVALID")


def main():
    with psycopg.connect(os.environ["AUTOPILOT_DATABASE_URL"], autocommit=True,
                         row_factory=dict_row, connect_timeout=10,
                         options="-c statement_timeout=5000 -c default_transaction_read_only=on") as conn:
        row = conn.execute("SELECT * FROM autopilot.parallel_work_manifest_status(%s)",
                           (manifest_sha256(),)).fetchone()
    validate_receipt(row)
    print(f"AUTOPILOT_MANIFEST_RECEIPT_PASS sha256={manifest_sha256()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"AUTOPILOT_ROLLOUT_RECEIPT_FAILED error_type={type(exc).__name__}")
        raise SystemExit(1) from None
