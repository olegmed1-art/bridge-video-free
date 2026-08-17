#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Canonical DDS Learning v2.3 runtime pins.
PYTHON_BIN="${PYTHON_BIN:-python3.14}"
DDS_REPO="${DDS_REPO:-https://github.com/dds-bridge/dds.git}"
DDS_TAG="${DDS_TAG:-v3.0.0}"
DDS_COMMIT="${DDS_COMMIT:-37c8a79f4c67c55d1a309ccb66dd00cb58af464a}"
BAZELISK_VERSION="${BAZELISK_VERSION:-1.29.0}"
USE_BAZEL_VERSION="${USE_BAZEL_VERSION:-7.6.1}"
DDS_REQUIRE_WHEEL_CACHE="${DDS_REQUIRE_WHEEL_CACHE:-0}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.14 is required for the pinned DDS3 v3.0.0 training runtime." >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "git is required." >&2
  exit 2
fi

"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip --version >/dev/null

WHEEL_CACHE="${DDS_WHEEL_CACHE:-$HERE/.wheel-cache/dds3}"
PROVENANCE="$WHEEL_CACHE/build_provenance.json"
REBUILT_MARKER="$WHEEL_CACHE/.cache_rebuilt"
mkdir -p "$WHEEL_CACHE"
rm -f "$REBUILT_MARKER"

validate_cached_wheel() {
  python - "$WHEEL_CACHE" "$PROVENANCE" "$DDS_COMMIT" "$USE_BAZEL_VERSION" <<'PY'
from __future__ import annotations
import hashlib
import json
import platform
import sys
from pathlib import Path

cache = Path(sys.argv[1])
provenance_path = Path(sys.argv[2])
expected_commit = sys.argv[3]
expected_bazel = sys.argv[4]
if not provenance_path.is_file():
    raise SystemExit(1)
try:
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
if provenance.get("schema") != "dds3-wheel-provenance-v1":
    raise SystemExit(1)
if provenance.get("dds_source_commit") != expected_commit:
    raise SystemExit(1)
if provenance.get("bazel_version") != expected_bazel:
    raise SystemExit(1)
if provenance.get("python_abi") != f"cp{sys.version_info.major}{sys.version_info.minor}":
    raise SystemExit(1)
wheel_name = str(provenance.get("wheel", "")).strip()
if not wheel_name or Path(wheel_name).name != wheel_name:
    raise SystemExit(1)
wheel = cache / wheel_name
if not wheel.is_file() or wheel.stat().st_size < 1024:
    raise SystemExit(1)
digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
if digest != provenance.get("wheel_sha256"):
    raise SystemExit(1)
print(wheel)
PY
}

if CACHED_WHEEL="$(validate_cached_wheel 2>/dev/null)"; then
  echo "Installing verified cached DDS3 wheel: $CACHED_WHEEL"
  python -m pip install --force-reinstall "$CACHED_WHEEL"
  python preflight.py --quick
  echo "DDS local environment restored from verified wheel cache. No DDS training was started."
  exit 0
fi

if [[ "$DDS_REQUIRE_WHEEL_CACHE" == "1" ]]; then
  echo "Verified DDS3 wheel cache is required but missing or invalid." >&2
  exit 4
fi

echo "DDS wheel cache is missing or invalid; rebuilding from the pinned source commit."
rm -f "$WHEEL_CACHE"/dds3-*.whl "$PROVENANCE"
mkdir -p .tools .build "${HOME}/.cache/bazel-dds3"

BAZELISK="$HERE/.tools/bazelisk"
if [[ ! -x "$BAZELISK" ]]; then
  python - "$BAZELISK" "$BAZELISK_VERSION" <<'PY'
from __future__ import annotations
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

out = Path(sys.argv[1])
version = sys.argv[2]
api = f"https://api.github.com/repos/bazelbuild/bazelisk/releases/tags/v{version}"
request = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json", "User-Agent": "bridge-school-dds-bootstrap"})
with urllib.request.urlopen(request, timeout=30) as response:
    release = json.load(response)
asset = next((item for item in release.get("assets", []) if item.get("name") == "bazelisk-linux-amd64"), None)
if asset is None:
    raise SystemExit("Bazelisk release asset not found")
browser_download_url = asset["browser_download_url"]
digest = asset.get('digest')
if not isinstance(digest, str) or not digest.startswith("sha256:"):
    raise SystemExit("Bazelisk release asset lacks an official SHA-256 digest")
expected = digest.split(":", 1)[1].lower()
out.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(dir=out.parent, delete=False) as handle:
    temporary = Path(handle.name)
try:
    with urllib.request.urlopen(browser_download_url, timeout=60) as response, temporary.open("wb") as handle:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            handle.write(block)
    actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Bazelisk SHA-256 mismatch: expected {expected}, got {actual}")
    temporary.replace(out)
    os.chmod(out, 0o755)
finally:
    if temporary.exists():
        temporary.unlink()
print(json.dumps({"bazelisk_version": version, "sha256": expected, "verified": True}, sort_keys=True))
PY
fi

DDS_DIR="$HERE/.build/dds3"
if [[ ! -d "$DDS_DIR/.git" ]]; then
  rm -rf "$DDS_DIR"
  git init --quiet "$DDS_DIR"
  git -C "$DDS_DIR" remote add origin "$DDS_REPO"
fi
git -C "$DDS_DIR" fetch --quiet --depth 1 origin "$DDS_COMMIT"
git -C "$DDS_DIR" checkout --quiet --detach FETCH_HEAD
ACTUAL_DDS_COMMIT="$(git -C "$DDS_DIR" rev-parse HEAD)"
if [[ "$ACTUAL_DDS_COMMIT" != "$DDS_COMMIT" ]]; then
  echo "DDS source provenance mismatch: expected $DDS_COMMIT, got $ACTUAL_DDS_COMMIT" >&2
  exit 3
fi

pushd "$DDS_DIR" >/dev/null
export USE_BAZEL_VERSION
"$BAZELISK" build --disk_cache="${HOME}/.cache/bazel-dds3" -c opt //python:dds3_wheel_dist
WHEEL="$(find bazel-bin/python -type f -name 'dds3*.whl' -print -quit)"
if [[ -z "$WHEEL" ]]; then
  echo "DDS3 wheel was not produced." >&2
  exit 3
fi
cp -f "$WHEEL" "$WHEEL_CACHE/"
CACHED_WHEEL="$WHEEL_CACHE/$(basename "$WHEEL")"
popd >/dev/null

python - "$CACHED_WHEEL" "$PROVENANCE" "$DDS_REPO" "$DDS_TAG" \
  "$DDS_COMMIT" "$ACTUAL_DDS_COMMIT" "$USE_BAZEL_VERSION" "$BAZELISK_VERSION" <<'PY'
from __future__ import annotations
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

wheel = Path(sys.argv[1])
out = Path(sys.argv[2])
payload = {
    "schema": "dds3-wheel-provenance-v1",
    "dds_repository": sys.argv[3],
    "dds_tag_label": sys.argv[4],
    "dds_source_commit": sys.argv[5],
    "actual_source_commit": sys.argv[6],
    "bazel_version": sys.argv[7],
    "bazelisk_version": sys.argv[8],
    "wheel": wheel.name,
    "wheel_size": wheel.stat().st_size,
    "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    "python_version": platform.python_version(),
    "python_abi": f"cp{sys.version_info.major}{sys.version_info.minor}",
    "platform": platform.platform(),
    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY

touch "$REBUILT_MARKER"
python -m pip install --force-reinstall "$CACHED_WHEEL"
python preflight.py --quick

echo "DDS local environment is technically ready from pinned source and wheel-cached. No DDS training was started."
