#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${GITHUB_WORKSPACE:-$(cd "$(dirname "$0")/.." && pwd)}"
DDS_TAG="v3.0.0"
DDS_FIX_COMMIT="cdd13cf5b700788ac8c1391501b42445b3129b45"
TEMP_ROOT="${TMPDIR:-$REPO_ROOT/.tmp}"
mkdir -p "$TEMP_ROOT"
WORK_DIR="$(mktemp -d "$TEMP_ROOT/dds-golden.XXXXXXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

git clone --quiet --branch "$DDS_TAG" https://github.com/dds-bridge/dds.git "$WORK_DIR/dds"
git -C "$WORK_DIR/dds" fetch --quiet origin "$DDS_FIX_COMMIT"
git -C "$WORK_DIR/dds" -c user.name=bridge-school-ci -c user.email=ci@invalid.example \
  cherry-pick --no-commit "$DDS_FIX_COMMIT"

cd "$WORK_DIR/dds"
g++ -std=c++20 -O3 -fPIC -shared -pthread -I library/src \
  library/src/*.cpp library/src/heuristic_sorting/*.cpp \
  library/src/lookup_tables/*.cpp library/src/moves/*.cpp \
  library/src/solver_context/*.cpp library/src/system/*.cpp \
  library/src/trans_table/*.cpp library/src/utility/*.cpp -o libdds3.so
g++ -std=c++20 -O3 -pthread -I library/src "$REPO_ROOT/dds/dds_pbn_cli.cpp" \
  -L. -ldds3 -o dds_pbn_cli

export LD_LIBRARY_PATH="$PWD"
deal='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'
./dds_pbn_cli N None "$deal" > run1.json
./dds_pbn_cli N None "$deal" > run2.json
cmp run1.json run2.json
python3 - <<'PY'
import hashlib, json
raw1 = open('run1.json', 'rb').read()
raw2 = open('run2.json', 'rb').read()
assert hashlib.sha256(raw1).digest() == hashlib.sha256(raw2).digest()
d = json.loads(raw1)
assert d['hand_order'] == ['N', 'E', 'S', 'W']
assert d['strain_order'] == ['S', 'H', 'D', 'C', 'NT']
assert d['par_score_ns'] == -110
assert d['par_contracts'] == ['2S-EW']
assert all(len(v) == 4 and all(0 <= x <= 13 for x in v) for v in d['dd_table'].values())
print('DDS_GOLDEN_SMOKE: PASS sha256=' + hashlib.sha256(raw1).hexdigest())
PY
