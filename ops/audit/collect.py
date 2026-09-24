"""Read-only advisory scans. Findings do not fail CI; scanner failures do."""
import collections
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    os.chdir(root)
    output = root / 'audit-reports'
    output.mkdir(exist_ok=True)
    tracked = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
    inventory = {
        'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'python_files': sum(p.endswith('.py') for p in tracked),
        'javascript_typescript_files': sum(p.endswith(('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs')) for p in tracked),
        'package_manifests': [p for p in tracked if Path(p).name == 'package.json'],
        'knip': 'NOT_APPLICABLE_NO_JS_TS_PROJECT',
    }
    if inventory['javascript_typescript_files'] or inventory['package_manifests']:
        inventory['knip'] = 'CONFIGURATION_REQUIRED_NEW_JS_TS_PROJECT'
    (output / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    errors = []
    summary = ['## Repository audit (advisory)', f"Revision: `{inventory['revision']}`",
               f"Knip: {inventory['knip']}"]
    if inventory['knip'].startswith('CONFIGURATION_REQUIRED'):
        summary.append('Knip is not running: configure project entry points before claiming coverage.')
    commands = {
        'ruff': [sys.executable, '-m', 'ruff', 'check', '.', '--isolated', '--target-version', 'py312',
                 '--select', 'E4,E7,E9,F', '--no-fix', '--output-format', 'json',
                 '--exclude', 'audit-reports', '--output-file', str(output / 'ruff.json')],
        'trivy': ['trivy', 'fs', '--scanners', 'vuln,misconfig', '--format', 'json',
                  '--output', str(output / 'trivy.json'), '--exit-code', '0',
                  '--timeout', '8m', '--skip-dirs', '.git', '--skip-dirs', 'audit-reports', '.'],
    }
    for name, command in commands.items():
        try:
            executable = [sys.executable, '-m', 'ruff'] if name == 'ruff' else [name]
            version = subprocess.check_output(executable + ['--version'], text=True, timeout=30).strip()
            with (output / f'{name}.log').open('w') as log:
                result = subprocess.run(command, stdout=log, stderr=log, timeout=540)
            if result.returncode not in ({0, 1} if name == 'ruff' else {0}):
                raise RuntimeError(f'exit={result.returncode}')
            report = json.loads((output / f'{name}.json').read_text())
            if name == 'ruff':
                counts = collections.Counter(row['code'] for row in report)
            else:
                counts = collections.Counter(
                    f"{kind}:{item['Severity']}"
                    for section in report.get('Results', [])
                    for kind in ('Vulnerabilities', 'Misconfigurations')
                    for item in section.get(kind, []) or []
                )
            summary.append(f"{name}: scan completed; counts={dict(sorted(counts.items()))}")
            (output / f'{name}-version.txt').write_text(version + '\n')
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            errors.append(name)
            summary.append(f'{name}: SCANNER_ERROR ({type(exc).__name__}); not a clean scan.')
    summary.append('No fixes, dependency updates, runtime changes or file deletions performed by this workflow.')
    text = '\n\n'.join(summary) + '\n'
    (output / 'summary.md').write_text(text)
    print(text)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(text)
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
