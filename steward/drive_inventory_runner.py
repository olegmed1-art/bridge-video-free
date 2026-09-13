"""Credential-aware CLI for the read-only School Systems Drive inventory."""
from __future__ import annotations

from typing import Sequence

from steward.drive_inventory import (
    DriveInventoryError,
    DriveListClient,
    build_drive_inventory,
    parse_args,
    write_inventory_outputs,
)
from universal_video.drive_adapter import access_token


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        client = DriveListClient(
            token_provider=access_token,
            timeout_seconds=args.timeout_seconds,
        )
        manifest = build_drive_inventory(
            client,
            root_folder_id=args.root_folder_id,
            root_name=args.root_name,
        )
        write_inventory_outputs(manifest, args.output_dir)
    except DriveInventoryError as exc:
        print(f"SCHOOL_STEWARD_DRIVE_AUDIT_FAIL code={exc.code}")
        return 2
    except Exception:
        print("SCHOOL_STEWARD_DRIVE_AUDIT_FAIL code=UNEXPECTED")
        return 3
    print(
        "SCHOOL_STEWARD_DRIVE_AUDIT_PASS "
        f"manifest={args.output_dir / 'drive-inventory.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
