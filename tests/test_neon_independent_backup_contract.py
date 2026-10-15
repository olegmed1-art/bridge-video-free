import os
from pathlib import Path
import subprocess
import tempfile
import textwrap


WORKFLOW = Path('.github/workflows/neon-independent-backup-restore.yml')


def _restore_verification_script():
    workflow = WORKFLOW.read_text(encoding='utf-8')
    section = workflow.split('      - name: Verify restored schema and critical read paths\n', 1)[1]
    section = section.split('      - name: Publish non-sensitive recovery evidence\n', 1)[0]
    return textwrap.dedent(section.split('        run: |\n', 1)[1])


def _run_restore_verification(overrides=None):
    mock_psql = '''
psql() {
  case "$*" in
    *"FROM information_schema.tables"*) printf '%s\\n' "$MOCK_TABLES" ;;
    *"FROM information_schema.schemata"*) printf '%s\\n' "$MOCK_SCHEMAS" ;;
    *"FROM assistant_lab.job"*) printf '%s\\n' "$MOCK_JOBS" ;;
    *"FROM assistant_lab.research_job"*) printf '%s\\n' "$MOCK_RESEARCH_JOBS" ;;
    *"public.recovery_checkpoint"*) printf '%s\\n' "$MOCK_CHECKPOINT_EXISTS" ;;
    *"public.recovery_verification"*) printf '%s\\n' "$MOCK_VERIFICATION_EXISTS" ;;
    *) return 2 ;;
  esac
}
'''
    env = os.environ.copy()
    env.update({
        'EXPECTED_TABLES': '10',
        'EXPECTED_SCHEMAS': '3',
        'EXPECTED_JOBS': '4',
        'EXPECTED_RESEARCH_JOBS': '2',
        'MOCK_TABLES': '10',
        'MOCK_SCHEMAS': '3',
        'MOCK_JOBS': '4',
        'MOCK_RESEARCH_JOBS': '2',
        'MOCK_CHECKPOINT_EXISTS': 't',
        'MOCK_VERIFICATION_EXISTS': 't',
        'GITHUB_OUTPUT': 'step-outputs.txt',
    })
    env.update(overrides or {})
    with tempfile.TemporaryDirectory() as temp_dir:
        result = subprocess.run(
            ['bash', '-c', mock_psql + _restore_verification_script()],
            cwd=temp_dir, env=env, capture_output=True, text=True, check=False,
        )
        output_file = Path(temp_dir, 'step-outputs.txt')
        outputs = output_file.read_text(encoding='utf-8') if output_file.exists() else ''
    return result, outputs


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


def test_restore_verification_accepts_matching_values():
    result, outputs = _run_restore_verification()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'tables source=10 restored=10' in result.stdout
    assert 'recovery_checkpoint_exists=t; recovery_verification_exists=t' in result.stdout
    assert 'restored_tables=10\n' in outputs
    assert 'restored_research_jobs=2\n' in outputs


def test_restore_verification_names_each_failure_and_keeps_counts():
    cases = [
        ('MOCK_TABLES', '11', 'Restored table count mismatch'),
        ('MOCK_SCHEMAS', '4', 'Restored schema count mismatch'),
        ('MOCK_JOBS', '5', 'Restored job count mismatch'),
        ('MOCK_RESEARCH_JOBS', '3', 'Restored research job count mismatch'),
        ('MOCK_CHECKPOINT_EXISTS', 'f', 'Recovery checkpoint missing'),
        ('MOCK_VERIFICATION_EXISTS', 'f', 'Recovery verification missing'),
    ]
    for variable, value, reason in cases:
        result, outputs = _run_restore_verification({variable: value})
        assert result.returncode == 1, (variable, result.stdout, result.stderr)
        assert 'Restore verification:' in result.stdout, variable
        assert f'::error title={reason}::' in result.stdout, variable
        assert result.stdout.index('Restore verification:') < result.stdout.index(reason)
        assert 'restored_tables=' in outputs, variable
        assert 'restored_research_jobs=' in outputs, variable


def test_restore_verification_reports_all_simultaneous_mismatches():
    result, _ = _run_restore_verification({
        'MOCK_TABLES': '11',
        'MOCK_SCHEMAS': '4',
        'MOCK_JOBS': '5',
        'MOCK_RESEARCH_JOBS': '3',
        'MOCK_CHECKPOINT_EXISTS': 'f',
        'MOCK_VERIFICATION_EXISTS': 'f',
    })
    assert result.returncode == 1
    assert result.stdout.count('::error title=') == 6


def test_restore_verification_rejects_malformed_values_without_logging_them():
    for variable, value, reason in [
        ('EXPECTED_TABLES', 'not-a-count', 'Invalid restore verification count'),
        ('MOCK_TABLES', 'not-a-count', 'Invalid restore verification count'),
        ('MOCK_CHECKPOINT_EXISTS', 'not-a-boolean', 'Invalid restore verification boolean'),
    ]:
        result, outputs = _run_restore_verification({variable: value})
        assert result.returncode == 1, (variable, result.stdout, result.stderr)
        assert f'::error title={reason}::' in result.stdout, variable
        assert value not in result.stdout
        assert outputs == ''


def main():
    test_neon_independent_backup_restore_is_fail_closed()
    test_raw_dump_is_not_uploaded()
    test_backup_and_restore_use_distinct_jobs()
    test_restore_verification_accepts_matching_values()
    test_restore_verification_names_each_failure_and_keeps_counts()
    test_restore_verification_reports_all_simultaneous_mismatches()
    test_restore_verification_rejects_malformed_values_without_logging_them()
    print('NEON_INDEPENDENT_BACKUP_CONTRACT_PASS')


if __name__ == '__main__':
    main()
