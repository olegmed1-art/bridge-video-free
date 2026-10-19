import hashlib
import shutil

import pytest

from test_universal_video_connector_recovery import CODE, staged, receipt
import legacy_installed_packet as legacy


@pytest.fixture
def legacy_stage(staged):
    shutil.copyfile(CODE / (legacy.KEY + ".sql"), legacy.r.ROOT / "recovery" / (legacy.KEY + ".sql"))


def test_body_is_new_pinned_transition_and_never_migration():
    raw = (CODE / (legacy.KEY + ".sql")).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == legacy.BODY_SHA256
    assert b"ALTER DEFAULT PRIVILEGES FOR ROLE video_subset_owner" in raw
    assert b"DELETE FROM" not in raw and b"TRUNCATE TABLE" not in raw
    assert not (CODE.parents[1] / "migrations" / (legacy.KEY + ".sql")).exists()


def test_new_and_unknown_checksums_rejected_before_inputs():
    for checksum in (legacy.c.PINNED["0060_fixed_subset_admission"], "0" * 64, None):
        with pytest.raises(ValueError, match="NOT_SUPPORTED_LEGACY_0060"):
            legacy.recover_payload({}, installed_0060_checksum=checksum)


def test_verifier_does_not_run_recovery_or_default_acl_change(legacy_stage):
    statements = legacy.verify_payload(receipt(), installed_0060_checksum=legacy.c.LEGACY_0060_SHA256)["sql_statements"]
    assert not any("ALTER DEFAULT PRIVILEGES" in s or "DROP TRIGGER" in s for s in statements)
    assert legacy.c.TEMP_REVOKE in statements


def test_recovery_preserves_checksum_and_records_separate_transition(legacy_stage):
    statements = legacy.recover_payload(receipt(), installed_0060_checksum=legacy.c.LEGACY_0060_SHA256)["sql_statements"]
    assert not any("UPDATE public.schema_migration" in s for s in statements)
    assert any(legacy.KEY in s and "INSERT INTO" in s for s in statements)
    assert legacy.c.TEMP_REVOKE in statements


def test_tampered_recovery_body_refused(legacy_stage):
    with (legacy.r.ROOT / "recovery" / (legacy.KEY + ".sql")).open("ab") as file:
        file.write(b"-- tampered\n")
    with pytest.raises(ValueError, match="legacy recovery source changed"):
        legacy.recover_payload(receipt(), installed_0060_checksum=legacy.c.LEGACY_0060_SHA256)
