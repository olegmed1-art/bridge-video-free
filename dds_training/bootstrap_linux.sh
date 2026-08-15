#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# DDS3 v3.0.0's current Bazel wheel target resolves its pybind toolchain to
# Python 3.14. Pin the runtime to the same interpreter to avoid ABI mismatch.
PYTHON_BIN="${PYTHON_BIN:-python3.14}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.14 is required for the pinned DDS3 v3.0.0 training runtime." >&2
  echo "Install python3.14 (WSL2/Linux) and run again." >&2
  exit 2
fi

if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  echo "git and curl are required." >&2
  exit 2
fi

"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools

mkdir -p .tools .build "${HOME}/.cache/bazel-dds3"
BAZELISK_VERSION="1.29.0"
BAZELISK="$HERE/.tools/bazelisk"
if [[ ! -x "$BAZELISK" ]]; then
  curl -fsSL -o "$BAZELISK" \
    "https://github.com/bazelbuild/bazelisk/releases/download/v${BAZELISK_VERSION}/bazelisk-linux-amd64"
  chmod +x "$BAZELISK"
fi

DDS_DIR="$HERE/.build/dds3"
if [[ ! -d "$DDS_DIR/.git" ]]; then
  rm -rf "$DDS_DIR"
  git clone --depth 1 --branch v3.0.0 https://github.com/dds-bridge/dds.git "$DDS_DIR"
else
  git -C "$DDS_DIR" fetch --depth 1 origin tag v3.0.0
  git -C "$DDS_DIR" checkout --detach v3.0.0
fi

pushd "$DDS_DIR" >/dev/null
export USE_BAZEL_VERSION="7.6.1"
"$BAZELISK" build --disk_cache="${HOME}/.cache/bazel-dds3" -c opt //python:dds3_wheel_dist
WHEEL="$(find bazel-bin/python -type f -name 'dds3*.whl' -print -quit)"
if [[ -z "$WHEEL" ]]; then
  echo "DDS3 wheel was not produced." >&2
  exit 3
fi
python -m pip install --force-reinstall "$WHEEL"
popd >/dev/null

python preflight.py --quick

echo "DDS local environment is technically ready. No DDS training was started."
