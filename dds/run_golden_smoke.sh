#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="${GITHUB_WORKSPACE:-$(cd "$(dirname "$0")/.." && pwd)}"; TEMP_ROOT="${TMPDIR:-$REPO_ROOT/.tmp}"; mkdir -p "$TEMP_ROOT"; WORK_DIR="$(mktemp -d "$TEMP_ROOT/dds-compare.XXXXXXXXXX")"; trap 'rm -rf "$WORK_DIR"' EXIT
cat > "$WORK_DIR/deals.txt" <<'EOF'
N:9.A93.J7632.A865 AKQJ642.Q4.84.K2 875.K752.KT9.Q97 T3.JT86.AQ5.JT43
N:Q632.872.AKJ4.T7 A9854.64.QT6.A64 T.AKQ953.8.K9832 KJ7.JT.97532.QJ5
N:Q9.762.JT732.Q92 .AKQJ85.AK964.87 K7543.T4.Q.AK653 AJT862.93.85.JT4
N:Q872.K762.T54.AK KJ64.AJT.KJ876.7 3.Q53.A9.QT98653 AT95.984.Q32.J42
N:3.KQ94.KJT53.953 K9.A3.AQ92.AJT82 Q86.JT765.876.KQ AJT7542.82.4.764
N:K53.QJ7.876.AK85 Q72.A842.QJT93.T AJT84.K95.A.QJ96 96.T63.K542.7432
N:J82.K62.K7.K8732 A95.743.AQ52.J65 KT74.QJT5.943.AT Q63.A98.JT86.Q94
N:K73.KQ742.AJT7.6 Q96.T98653.942.Q AT852..KQ63.A954 J4.AJ.85.KJT8732
N:9632.9.96.K97542 KQ.AQJ5.KT853.A3 J75.T84.J7.QJT86 AT84.K7632.AQ42.
N:Q952.765.J73.Q54 8.KQJ3.A854.AK96 AKJ63.82.T2.J873 T74.AT94.KQ96.T2
EOF
git clone --quiet --branch v3.0.0 https://github.com/dds-bridge/dds.git "$WORK_DIR/dds3"; git -C "$WORK_DIR/dds3" fetch --quiet origin cdd13cf5b700788ac8c1391501b42445b3129b45; git -C "$WORK_DIR/dds3" -c user.name=bridge-school-ci -c user.email=ci@invalid.example cherry-pick --no-commit cdd13cf5b700788ac8c1391501b42445b3129b45
cd "$WORK_DIR/dds3"; g++ -std=c++20 -O3 -fPIC -shared -pthread -I library/src library/src/*.cpp library/src/heuristic_sorting/*.cpp library/src/lookup_tables/*.cpp library/src/moves/*.cpp library/src/solver_context/*.cpp library/src/system/*.cpp library/src/trans_table/*.cpp library/src/utility/*.cpp -o libdds3.so; g++ -std=c++20 -O3 -pthread -I library/src "$REPO_ROOT/dds/dds_pbn_cli.cpp" -L. -ldds3 -o dds_pbn_cli
export LD_LIBRARY_PATH="$PWD"; mkdir -p "$WORK_DIR/out"; i=0; while IFS= read -r deal; do i=$((i+1)); ./dds_pbn_cli N None "$deal" > "$WORK_DIR/out/dds3-$i.json"; done < "$WORK_DIR/deals.txt"; cd "$WORK_DIR"; sha256sum out/dds3-*.json > out/dds3-frozen.sha256
# Only now build and run classic DDS 2.9.
git clone --quiet --branch v2.9.0 https://github.com/dds-bridge/dds.git "$WORK_DIR/dds"
make -C "$WORK_DIR/dds/src" -f Makefiles/Makefile_linux_shared >/dev/null
g++ -std=c++17 -O2 -I"$WORK_DIR/dds/include" "$REPO_ROOT/tmp_dds_compare/classic_dds_cli.cpp" -L"$WORK_DIR/dds/src" -ldds -Wl,-rpath,"$WORK_DIR/dds/src" -o "$WORK_DIR/classic_dds_cli"
sha256sum -c out/dds3-frozen.sha256 >/dev/null; i=0; while IFS= read -r deal; do i=$((i+1)); ./classic_dds_cli "$deal" > "out/dds-$i.json"; done < deals.txt; sha256sum -c out/dds3-frozen.sha256 >/dev/null
python3 - <<'PY'
import json
for i in range(1,11):
 a=json.load(open(f'out/dds3-{i}.json')); b=json.load(open(f'out/dds-{i}.json'))
 print('REPORT',i,'DDS3='+json.dumps(a,separators=(',',':'),sort_keys=True),'DDS='+json.dumps(b,separators=(',',':'),sort_keys=True),'MATCH='+str(a['dd_table']==b['dd_table']))
PY
