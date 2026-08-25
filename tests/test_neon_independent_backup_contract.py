from pathlib import Path


def test_neon_independent_backup_restore_is_fail_closed():
    workflow = Path('.github/workflows/neon-independent-backup-restore.yml').read_text(encoding='utf-8')
    required = [
        "NEON_DATABASE_URL",
        "NEON_BACKUP_PASSPHRASE",
        "environment: database-production",
        "postgres:18",
        "pg_dump",
        "--format=custom",
        "openssl enc -aes-256-cbc",
        "rm -f neon.dump",
        "actions/upload-artifact@",
        "retention-days: 35",
        "retention-days: 90",
        "actions/download-artifact@",
        "sha256sum restore-input/neon.dump.enc",
        "pg_restore",
        "--exit-on-error",
        "assistant_lab.job",
        "assistant_lab.research_job",
        "recovery_checkpoint",
        "recovery_verification",
        "raw_dump_uploaded=false",
        "/recovery neon-backup-restore",
    ]
    for marker in required:
        assert marker in workflow


def test_raw_dump_is_not_uploaded():
    workflow = Path('.github/workflows/neon-independent-backup-restore.yml').read_text(encoding='utf-8')
    upload_section = workflow.split('Upload encrypted daily backup', 1)[1].split('Upload longer-lived monthly generation', 1)[0]
    assert 'neon.dump.enc' in upload_section
    assert '\n            neon.dump\n' not in upload_section
    assert 'rm -f neon.dump' in workflow


def test_backup_and_restore_use_distinct_jobs():
    workflow = Path('.github/workflows/neon-independent-backup-restore.yml').read_text(encoding='utf-8')
    assert '\n  backup:\n' in workflow
    assert '\n  restore:\n' in workflow
    assert 'needs: backup' in workflow
    assert 'Download encrypted backup from independent artifact store' in workflow


def main():
    test_neon_independent_backup_restore_is_fail_closed()
    test_raw_dump_is_not_uploaded()
    test_backup_and_restore_use_distinct_jobs()
    print('NEON_INDEPENDENT_BACKUP_CONTRACT_PASS')


if __name__ == '__main__':
    main()
