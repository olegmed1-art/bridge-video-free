from pathlib import Path


def test_neon_independent_backup_restore_is_fail_closed():
    workflow = Path('.github/workflows/neon-independent-backup-restore.yml').read_text(encoding='utf-8')
    required = [
        "LIGHT_MAINTENANCE_DATABASE_URL",
        "NEON_BACKUP_PASSPHRASE",
        "environment: database-production",
        "postgres:18",
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
    source = Path('ops/neon_backup_source.py').read_text(encoding='utf-8')
    assert 'secrets.NEON_DATABASE_URL' not in workflow
    assert 'python -m ops.neon_backup_source stats' in workflow
    assert 'python -m ops.neon_backup_source dump' in workflow
    assert 'pg_dump' in source and '--format=custom' in source
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


def test_review_source_binding_and_monthly_scope_are_explicit():
    workflow = Path('.github/workflows/neon-independent-backup-restore.yml').read_text(encoding='utf-8')
    assert 'ref: ${{ github.sha }}' in workflow
    assert 'test "$GITHUB_SHA" = "$current_main"' in workflow
    assert workflow.index('Require the event revision') < workflow.index('Read sanitized source statistics')
    assert workflow.count('BACKUP_PASSPHRASE: ${{ secrets.NEON_BACKUP_PASSPHRASE }}') == 2
    assert 'if [ "$(date -u +%d)" = "01" ]; then monthly=true; else monthly=false; fi' in workflow
    contract = workflow.split('  contract:', 1)[1].split('\n  backup:', 1)[0]
    assert 'secrets.' not in contract and 'environment:' not in contract
    assert 'test_neon_backup_source.py' in contract


def main():
    test_neon_independent_backup_restore_is_fail_closed()
    test_raw_dump_is_not_uploaded()
    test_backup_and_restore_use_distinct_jobs()
    test_review_source_binding_and_monthly_scope_are_explicit()
    print('NEON_INDEPENDENT_BACKUP_CONTRACT_PASS')


if __name__ == '__main__':
    main()
