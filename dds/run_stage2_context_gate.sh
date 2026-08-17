#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${GITHUB_WORKSPACE:-$(cd "$(dirname "$0")/.." && pwd)}"
DDS_TAG="v3.0.0"
DDS_CONTEXT_COMMIT="cdd13cf5b700788ac8c1391501b42445b3129b45"
TEMP_ROOT="${TMPDIR:-$REPO_ROOT/.tmp}"
mkdir -p "$TEMP_ROOT"
WORK_DIR="$(mktemp -d "$TEMP_ROOT/dds-stage2.XXXXXXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

git clone --quiet --branch "$DDS_TAG" https://github.com/dds-bridge/dds.git "$WORK_DIR/dds"
git -C "$WORK_DIR/dds" fetch --quiet origin "$DDS_CONTEXT_COMMIT"
git -C "$WORK_DIR/dds" -c user.name=bridge-school-ci -c user.email=ci@invalid.example \
  cherry-pick --no-commit "$DDS_CONTEXT_COMMIT"

cd "$WORK_DIR/dds"
g++ -std=c++20 -O3 -fPIC -shared -pthread -I library/src \
  library/src/*.cpp library/src/heuristic_sorting/*.cpp \
  library/src/lookup_tables/*.cpp library/src/moves/*.cpp \
  library/src/solver_context/*.cpp library/src/system/*.cpp \
  library/src/trans_table/*.cpp library/src/utility/*.cpp -o libdds3.so

g++ -std=c++20 -O3 -pthread -I library/src \
  "$REPO_ROOT/dds/dds_stage2_context_gate.cpp" -L. -ldds3 -o dds_stage2_context_gate

export LD_LIBRARY_PATH="$PWD"

# This exact PBN is already used by the project's golden DDS smoke. DealID is a
# deterministic content identity for this gate, not a tournament-board number.
deal='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'
deal_id="$(printf '%s' "$deal" | sha256sum | awk '{print $1}')"

# Two fresh-process runs prove the gate itself is reproducible. Each process also solves
# the same DealID twice through ONE SolverContext and verifies TT object continuity.
./dds_stage2_context_gate "$deal_id" "$deal" > stage2_run1.json
./dds_stage2_context_gate "$deal_id" "$deal" > stage2_run2.json

python3 - "$deal_id" <<'PY'
import hashlib
import json
import sys

expected_deal_id = sys.argv[1]
with open('stage2_run1.json', encoding='utf-8') as f:
    r1 = json.load(f)
with open('stage2_run2.json', encoding='utf-8') as f:
    r2 = json.load(f)

for result in (r1, r2):
    assert result['gate_version'] == 'stage2-dds-context-v1'
    assert result['deal_id'] == expected_deal_id
    assert result['dds_upstream_commit'] == 'cdd13cf5b700788ac8c1391501b42445b3129b45'
    assert result['solver_api'] == 'solve_board(SolverContext&)'
    assert result['tt_lazy_before_solve'] is True
    assert result['tt_created_by_solve'] is True
    assert result['same_context_tt_instance'] is True
    assert result['sibling_context_same_thread_shares_tt'] is True
    assert result['repeated_solve_result_equal'] is True
    assert 1 <= result['result']['cards'] <= 13
    assert len(result['result']['suit']) == result['result']['cards']
    assert len(result['result']['rank']) == result['result']['cards']
    assert len(result['result']['equals']) == result['result']['cards']
    assert len(result['result']['score']) == result['result']['cards']
    assert result['nodes_first'] >= 0
    assert result['nodes_second'] >= 0

# Node counts are instrumentation and are intentionally excluded from the semantic
# reproducibility contract; actual cache hits must never be inferred from timing alone.
semantic_keys = [
    'gate_version', 'deal_id', 'dds_upstream_commit', 'solver_api',
    'trump', 'first', 'target', 'solutions', 'mode',
    'tt_lazy_before_solve', 'tt_created_by_solve',
    'same_context_tt_instance', 'sibling_context_same_thread_shares_tt',
    'repeated_solve_result_equal', 'result',
]
semantic1 = {k: r1[k] for k in semantic_keys}
semantic2 = {k: r2[k] for k in semantic_keys}
assert semantic1 == semantic2

canonical = json.dumps(semantic1, sort_keys=True, separators=(',', ':')).encode()
digest = hashlib.sha256(canonical).hexdigest()
print(
    'DDS_STAGE2_CONTEXT_GATE: PASS '
    f'deal_id={expected_deal_id} semantic_sha256={digest} '
    f'nodes_first={r1["nodes_first"]} nodes_second={r1["nodes_second"]}'
)
PY
