#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PYTHON_BIN="${PYTHON_BIN:-python3.14}"
DDS_COMMIT="37c8a79f4c67c55d1a309ccb66dd00cb58af464a"
DDS_REPOSITORY="https://github.com/dds-bridge/dds.git"
BAZELISK_VERSION="1.29.0"
BAZELISK_SHA256="5a408715e932c0250d28bd84555f12edbf70117de42f9181691c736eacc4a992"
BAZEL_VERSION="7.6.1"
PIP_VERSION="26.2.1"
WHEEL_VERSION="0.48.0"
SETUPTOOLS_VERSION="84.0.0"
PACKAGING_VERSION="26.3"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.14 is required for the pinned DDS3 runtime." >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
  echo "git, curl and sha256sum are required." >&2
  exit 2
fi

rm -rf .venv
"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --disable-pip-version-check \
  "pip==${PIP_VERSION}" \
  "wheel==${WHEEL_VERSION}" \
  "setuptools==${SETUPTOOLS_VERSION}" \
  "packaging==${PACKAGING_VERSION}"

WHEEL_CACHE="${DDS_WHEEL_CACHE:-$HERE/.wheel-cache/dds3}"
BOOTSTRAP_MANIFEST="${DDS_BOOTSTRAP_MANIFEST:-$HERE/dds_bootstrap_manifest.json}"
mkdir -p "$WHEEL_CACHE" .tools .build "${HOME}/.cache/bazel-dds3"

write_manifest() {
  local wheel_path="$1"
  local source_mode="$2"
  DDS_MANIFEST_WHEEL="$wheel_path" \
  DDS_MANIFEST_SOURCE_MODE="$source_mode" \
  DDS_MANIFEST_OUT="$BOOTSTRAP_MANIFEST" \
  DDS_MANIFEST_COMMIT="$DDS_COMMIT" \
  DDS_MANIFEST_BAZELISK_VERSION="$BAZELISK_VERSION" \
  DDS_MANIFEST_BAZELISK_SHA="$BAZELISK_SHA256" \
  DDS_MANIFEST_BAZEL_VERSION="$BAZEL_VERSION" \
  python - <<'PY'
import hashlib
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

wheel = Path(os.environ["DDS_MANIFEST_WHEEL"]).resolve()
out = Path(os.environ["DDS_MANIFEST_OUT"])
if not wheel.is_file() or wheel.stat().st_size <= 0:
    raise SystemExit(f"Invalid DDS wheel: {wheel}")
manifest = {
    "schema": "dds-bootstrap-manifest-v1",
    "dds_source_repository": "https://github.com/dds-bridge/dds.git",
    "dds_source_commit": os.environ["DDS_MANIFEST_COMMIT"],
    "dds_wheel": str(wheel),
    "dds_wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    "dds_wheel_bytes": wheel.stat().st_size,
    "source_mode": os.environ["DDS_MANIFEST_SOURCE_MODE"],
    "bazelisk_version": os.environ["DDS_MANIFEST_BAZELISK_VERSION"],
    "bazelisk_sha256": os.environ["DDS_MANIFEST_BAZELISK_SHA"],
    "bazel_version": os.environ["DDS_MANIFEST_BAZEL_VERSION"],
    "python": platform.python_version(),
    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "mass_training_started": False,
}
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2))
PY
}

install_and_verify_cached_wheel() {
  local wheel_path="$1"
  if [[ ! -s "$wheel_path" ]]; then
    return 1
  fi
  echo "Checking cached DDS3 wheel: $wheel_path"
  if python -m pip install --disable-pip-version-check --force-reinstall "$wheel_path" \
    && DDS_PREFLIGHT_MODE=1 python preflight.py --quick; then
    write_manifest "$wheel_path" "verified_wheel_cache"
    echo "DDS local environment restored from verified wheel cache. No DDS training was started."
    return 0
  fi
  echo "Cached DDS3 wheel failed verification and will be rebuilt: $wheel_path" >&2
  rm -f "$wheel_path"
  return 1
}

CACHED_WHEEL="$(find "$WHEEL_CACHE" -maxdepth 1 -type f -name 'dds3-*.whl' -size +0c -print -quit)"
if [[ -n "$CACHED_WHEEL" ]] && install_and_verify_cached_wheel "$CACHED_WHEEL"; then
  exit 0
fi

BAZELISK="$HERE/.tools/bazelisk"
if [[ -f "$BAZELISK" ]]; then
  actual="$(sha256sum "$BAZELISK" | awk '{print $1}')"
  if [[ "$actual" != "$BAZELISK_SHA256" ]]; then
    echo "Discarding Bazelisk with unexpected SHA-256: $actual" >&2
    rm -f "$BAZELISK"
  fi
fi
if [[ ! -x "$BAZELISK" ]]; then
  curl --fail --silent --show-error --location \
    --output "$BAZELISK" \
    "https://github.com/bazelbuild/bazelisk/releases/download/v${BAZELISK_VERSION}/bazelisk-linux-amd64"
  echo "${BAZELISK_SHA256}  ${BAZELISK}" | sha256sum --check --status
  chmod 0755 "$BAZELISK"
fi
actual_bazelisk="$(sha256sum "$BAZELISK" | awk '{print $1}')"
[[ "$actual_bazelisk" == "$BAZELISK_SHA256" ]]

DDS_DIR="$HERE/.build/dds3"
if [[ ! -d "$DDS_DIR/.git" ]] || [[ "$(git -C "$DDS_DIR" rev-parse HEAD 2>/dev/null || true)" != "$DDS_COMMIT" ]]; then
  rm -rf "$DDS_DIR"
  git init --quiet "$DDS_DIR"
  git -C "$DDS_DIR" remote add origin "$DDS_REPOSITORY"
  git -C "$DDS_DIR" fetch --quiet --depth 1 origin "$DDS_COMMIT"
  git -C "$DDS_DIR" checkout --quiet --detach FETCH_HEAD
fi
actual_dds_commit="$(git -C "$DDS_DIR" rev-parse HEAD)"
if [[ "$actual_dds_commit" != "$DDS_COMMIT" ]]; then
  echo "DDS source commit mismatch: expected $DDS_COMMIT, got $actual_dds_commit" >&2
  exit 3
fi

pushd "$DDS_DIR" >/dev/null
export USE_BAZEL_VERSION="$BAZEL_VERSION"
"$BAZELISK" build \
  --disk_cache="${HOME}/.cache/bazel-dds3" \
  -c opt \
  //python:dds3_wheel_dist
WHEEL="$(find bazel-bin/python -type f -name 'dds3*.whl' -size +0c -print -quit)"
if [[ -z "$WHEEL" ]]; then
  echo "DDS3 wheel was not produced." >&2
  exit 4
fi
cp -f "$WHEEL" "$WHEEL_CACHE/"
CACHED_WHEEL="$WHEEL_CACHE/$(basename "$WHEEL")"
popd >/dev/null

python -m pip install --disable-pip-version-check --force-reinstall "$CACHED_WHEEL"
DDS_PREFLIGHT_MODE=1 python preflight.py --quick
write_manifest "$CACHED_WHEEL" "built_from_pinned_source"

echo "DDS local environment built from pinned source and wheel-cached. No DDS training was started."
