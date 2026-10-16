import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap


WORKFLOW = Path('.github/workflows/neon-independent-backup-restore.yml')


def _schema_hex(names):
    return json.dumps(names, ensure_ascii=True, separators=(',', ':')).encode('utf-8').hex()


def _restore_verification_script():
    workflow = WORKFLOW.read_text(encoding='utf-8')
    section = workflow.split('      - name: Verify restored schema and critical read paths\n', 1)[1]
    section = section.split('      - name: Publish non-sensitive recovery evidence\n', 1)[0]
    return textwrap.dedent(section.split('        run: |\n', 1)[1])


def _run_schema_inventory(source, restored):
    script = _restore_verification_script().split("python - <<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    env = os.environ.copy()
    env.update({
        'SOURCE_SCHEMA_NAMES_HEX': _schema_hex(source),
        'RESTORED_SCHEMA_NAMES_HEX': _schema_hex(restored),
        'SOURCE_SCHEMA_COUNT': str(len(source)),
        'RESTORED_SCHEMA_COUNT': str(len(restored)),
    })
    return subprocess.run(
        [sys.executable, '-c', script], env=env,
        capture_output=True, text=True, check=False,
    )


def _read_schema_inventory(output):
    prefix = 'Schema inventory: '
    assert output.startswith(prefix)
    remaining = output[len(prefix):].removesuffix('\n')
    values = {}
    decoder = json.JSONDecoder()
    for index, label in enumerate(('source', 'restored', 'only_source', 'only_restored')):
        field = ('; ' if index else '') + label + '='
        assert remaining.startswith(field)
        value, end = decoder.raw_decode(remaining[len(field):])
        values[label] = value
        remaining = remaining[len(field) + end:]
    assert remaining == ''
    return values


def _run_restore_verification(overrides=None):
    mock_psql = '''
psql() {
  case "$*" in
    *"FROM information_schema.tables"*) printf '%s\\n' "$MOCK_TABLES" ;;
    *"json_agg(schema_name"*) printf '%s\\n' "$MOCK_SCHEMA_NAMES_HEX" ;;
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
        'EXPECTED_SCHEMA_NAMES_HEX': _schema_hex(['assistant_lab', 'public', 'reporting']),
        'EXPECTED_JOBS': '4',
        'EXPECTED_RESEARCH_JOBS': '2',
        'MOCK_TABLES': '10',
        'MOCK_SCHEMAS': '3',
        'MOCK_SCHEMA_NAMES_HEX': _schema_hex(['assistant_lab', 'public', 'reporting']),
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


def test_source_statistics_transports_schema_names_without_output_injection():
    workflow = WORKFLOW.read_text(encoding='utf-8')
    section = workflow.split('      - name: Read sanitized source statistics\n', 1)[1]
    section = section.split('      - name: Create consistent logical backup\n', 1)[0]
    script = textwrap.dedent(section.split('        run: |\n', 1)[1])
    encoded = _schema_hex(['assistant_lab', 'public', 'weird|\nsource_jobs=999'])
    env = os.environ.copy()
    env.update({
        'DATABASE_URL': 'unused-by-mock',
        'MOCK_STATS': f'10|3|4|2|{encoded}',
        'GITHUB_OUTPUT': 'step-outputs.txt',
    })
    with tempfile.TemporaryDirectory() as temp_dir:
        result = subprocess.run(
            ['bash', '-c', 'docker() { printf "%s\\n" "$MOCK_STATS"; }\n' + script],
            cwd=temp_dir, env=env, capture_output=True, text=True, check=False,
        )
        outputs = Path(temp_dir, 'step-outputs.txt').read_text(encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    assert outputs.splitlines() == [
        'source_tables=10', 'source_schemas=3', f'source_schema_names_hex={encoded}',
        'source_jobs=4', 'source_research_jobs=2',
    ]
    assert result.stdout == ''


def test_restore_verification_accepts_matching_values():
    result, outputs = _run_restore_verification()
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'tables source=10 restored=10' in result.stdout
    assert 'recovery_checkpoint_exists=t; recovery_verification_exists=t' in result.stdout
    assert 'Schema inventory: source=["assistant_lab","public","reporting"]; restored=["assistant_lab","public","reporting"]; only_source=[]; only_restored=[]' in result.stdout
    assert 'restored_tables=10\n' in outputs
    assert 'restored_research_jobs=2\n' in outputs


def test_restore_verification_names_each_failure_and_keeps_counts():
    cases = [
        ({'MOCK_TABLES': '11'}, 'Restored table count mismatch'),
        ({'MOCK_SCHEMAS': '4', 'MOCK_SCHEMA_NAMES_HEX': _schema_hex(['assistant_lab', 'extra', 'public', 'reporting'])}, 'Restored schema count mismatch'),
        ({'MOCK_JOBS': '5'}, 'Restored job count mismatch'),
        ({'MOCK_RESEARCH_JOBS': '3'}, 'Restored research job count mismatch'),
        ({'MOCK_CHECKPOINT_EXISTS': 'f'}, 'Recovery checkpoint missing'),
        ({'MOCK_VERIFICATION_EXISTS': 'f'}, 'Recovery verification missing'),
    ]
    for overrides, reason in cases:
        result, outputs = _run_restore_verification(overrides)
        assert result.returncode == 1, (overrides, result.stdout, result.stderr)
        assert 'Restore verification:' in result.stdout, overrides
        assert f'::error title={reason}::' in result.stdout, overrides
        assert result.stdout.index('Restore verification:') < result.stdout.index(reason)
        assert 'restored_tables=' in outputs, overrides
        assert 'restored_research_jobs=' in outputs, overrides


def test_restore_verification_reports_all_simultaneous_mismatches():
    result, _ = _run_restore_verification({
        'MOCK_TABLES': '11',
        'MOCK_SCHEMAS': '4',
        'MOCK_SCHEMA_NAMES_HEX': _schema_hex(['assistant_lab', 'extra', 'public', 'reporting']),
        'MOCK_JOBS': '5',
        'MOCK_RESEARCH_JOBS': '3',
        'MOCK_CHECKPOINT_EXISTS': 'f',
        'MOCK_VERIFICATION_EXISTS': 'f',
    })
    assert result.returncode == 1
    assert result.stdout.count('::error title=') == 6


def test_restore_verification_reports_exact_schema_difference_without_changing_count_gate():
    result, _ = _run_restore_verification({
        'MOCK_SCHEMA_NAMES_HEX': _schema_hex(['assistant_lab', 'public', 'sandbox']),
    })
    assert result.returncode == 0
    assert 'source=["assistant_lab","public","reporting"]' in result.stdout
    assert 'restored=["assistant_lab","public","sandbox"]' in result.stdout
    assert 'only_source=["reporting"]; only_restored=["sandbox"]' in result.stdout
    assert 'Restored schema count mismatch' not in result.stdout


def test_restore_verification_reports_schema_difference_before_count_failure():
    source = [f'schema_{number:02d}' for number in range(11)]
    result, outputs = _run_restore_verification({
        'EXPECTED_TABLES': '333', 'MOCK_TABLES': '333',
        'EXPECTED_SCHEMAS': '11', 'MOCK_SCHEMAS': '9',
        'EXPECTED_JOBS': '28', 'MOCK_JOBS': '28',
        'EXPECTED_RESEARCH_JOBS': '10', 'MOCK_RESEARCH_JOBS': '10',
        'EXPECTED_SCHEMA_NAMES_HEX': _schema_hex(source),
        'MOCK_SCHEMA_NAMES_HEX': _schema_hex(source[:9]),
    })
    assert result.returncode == 1
    assert 'schemas source=11 restored=9' in result.stdout
    assert 'only_source=["schema_09","schema_10"]; only_restored=[]' in result.stdout
    assert result.stdout.index('Schema inventory:') < result.stdout.index('Restored schema count mismatch')
    assert result.stdout.count('::error title=') == 1
    assert 'restored_schemas=9\n' in outputs


def test_restore_verification_escapes_unusual_schema_names():
    names = ['assistant_lab', 'public', 'weird\n::error title=injected::\r|"']
    encoded = _schema_hex(names)
    result, _ = _run_restore_verification({
        'EXPECTED_SCHEMA_NAMES_HEX': encoded,
        'MOCK_SCHEMA_NAMES_HEX': encoded,
    })
    assert result.returncode == 0
    assert 'weird\\n::error title=injected::\\r|\\"' in result.stdout
    assert result.stdout.count('Schema inventory:') == 1
    assert not any(line.startswith('::error') for line in result.stdout.splitlines())


def test_schema_inventory_blocks_legacy_runner_commands_and_preserves_names():
    for command in ('error', 'stop-commands', 'add-mask'):
        shared = f'prefix##[{command}]shared'
        source_only = f'source##[{command}]payload'
        restored_only = f'restored##[{command}]payload'
        source = sorted(['public', shared, source_only])
        restored = sorted(['public', shared, restored_only])
        result = _run_schema_inventory(source, restored)
        assert result.returncode == 0, result.stdout + result.stderr
        assert '##[' not in result.stdout, command
        assert r'\u0023\u0023[' in result.stdout, command
        assert not any(line.lstrip().startswith('::') for line in result.stdout.splitlines())
        assert _read_schema_inventory(result.stdout) == {
            'source': source, 'restored': restored,
            'only_source': [source_only], 'only_restored': [restored_only],
        }


def test_schema_inventory_round_trip_preserves_literal_escapes_and_all_schemas():
    names = sorted([
        '#', r'\u0023', 'pg_temp_42', 'pg_toast_temp_42',
        'weird\n::error title=injected::\r|"',
        '; restored=[]; only_source=[]', '\u0421\u0445\u0435\u043c\u0430',
    ])
    result = _run_schema_inventory(names, names)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '#' not in result.stdout
    assert len(result.stdout.splitlines()) == 1
    assert not result.stdout.lstrip().startswith('::')
    assert _read_schema_inventory(result.stdout) == {
        'source': names, 'restored': names, 'only_source': [], 'only_restored': [],
    }


def test_restore_verification_rejects_invalid_schema_inventory():
    for variable, value in [
        ('EXPECTED_SCHEMA_NAMES_HEX', 'not-hex'),
        ('MOCK_SCHEMA_NAMES_HEX', 'not-hex'),
        ('MOCK_SCHEMA_NAMES_HEX', _schema_hex(['public'])),
    ]:
        result, _ = _run_restore_verification({variable: value})
        assert result.returncode == 1
        assert 'Invalid schema inventory' in result.stdout
        assert value not in result.stdout


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
    test_schema_inventory_blocks_legacy_runner_commands_and_preserves_names()
    test_schema_inventory_round_trip_preserves_literal_escapes_and_all_schemas()
    test_source_statistics_transports_schema_names_without_output_injection()
    test_restore_verification_accepts_matching_values()
    test_restore_verification_names_each_failure_and_keeps_counts()
    test_restore_verification_reports_all_simultaneous_mismatches()
    test_restore_verification_reports_exact_schema_difference_without_changing_count_gate()
    test_restore_verification_reports_schema_difference_before_count_failure()
    test_restore_verification_escapes_unusual_schema_names()
    test_restore_verification_rejects_invalid_schema_inventory()
    test_restore_verification_rejects_malformed_values_without_logging_them()
    print('NEON_INDEPENDENT_BACKUP_CONTRACT_PASS')


if __name__ == '__main__':
    main()
