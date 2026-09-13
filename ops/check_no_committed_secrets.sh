#!/usr/bin/env bash
set -Eeuo pipefail

fail=0

printf 'Checking tracked filenames for secret material...\n'
while IFS= read -r path; do
  base="${path##*/}"
  case "$base" in
    .env|.env.*|*.env|*.pem|*.key|*.p12|*.pfx|id_rsa*|id_ed25519*|oci_api_key*|credentials*|secrets*)
      case "$base" in
        *.example|.env.example|.env.*.example) ;;
        *) printf 'FORBIDDEN_TRACKED_SECRET_FILENAME %s\n' "$path" >&2; fail=1 ;;
      esac
      ;;
  esac
done < <(git ls-files)

printf 'Checking tracked text for private-key material...\n'
if git grep -nEI -- '-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----|BEGIN PRIVATE KEY' -- . \
    ':(exclude)ops/check_no_committed_secrets.sh' \
    ':(exclude)**/*.md' \
    ':(exclude)**/*.example' >/tmp/bridge-secret-scan.out 2>/dev/null; then
  printf 'HIGH_CONFIDENCE_PRIVATE_KEY_SIGNATURE\n' >&2
  cat /tmp/bridge-secret-scan.out >&2
  fail=1
fi
rm -f /tmp/bridge-secret-scan.out

printf 'Checking tracked text for non-placeholder PostgreSQL credentials...\n'
if ! python3 - <<'PY'
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

placeholder_markers = ("secret", "password", "test", "synthetic", "changeme", "example", "dummy", "placeholder", "fake")
url_re = re.compile(r"postgres(?:ql)?://[^\s\"'<>]+", re.I)
violations = []

for raw_path in subprocess.check_output(["git", "ls-files"], text=True).splitlines():
    path = Path(raw_path)
    if raw_path == "ops/check_no_committed_secrets.sh" or path.suffix in {".md", ".example"}:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in url_re.finditer(line):
            raw = match.group(0)
            try:
                parsed = urlsplit(raw)
            except ValueError:
                continue
            if parsed.password is None:
                continue
            host = (parsed.hostname or "").lower()
            password = parsed.password.lower()
            if host in {"localhost", "127.0.0.1", "::1"}:
                continue
            if "example" in host or host.endswith(".invalid"):
                continue
            if any(marker in password for marker in placeholder_markers):
                continue
            if password.startswith("${") or password.startswith("<"):
                continue
            violations.append(f"{raw_path}:{lineno}: PostgreSQL URL contains an embedded non-placeholder password")

if violations:
    print("\n".join(violations))
    raise SystemExit(1)
PY
then
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  printf 'SECRET_GATE_FAIL\n' >&2
  exit 1
fi

printf 'SECRET_GATE_PASS\n'
