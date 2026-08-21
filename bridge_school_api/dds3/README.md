# Bridge School DDS3 module

Canonical double-dummy computation module for Bridge School.

## Contract

- Engine: DDS 3.x only.
- Pinned build: DDS `v3.0.0` plus fix commit `cdd13cf5b700788ac8c1391501b42445b3129b45`.
- Existing canonical C++ entry point: `dds/dds_pbn_cli.cpp`.
- Python entry point: `bridge_school_api.dds3.solve_table`.
- No heuristic, model, web solver, or alternate double-dummy fallback is allowed.
- Any execution/build/parse failure must surface as `DDS_UNAVAILABLE`.
- Successful Python results explicitly contain `engine: DDS3` and `fallback_used: false`.

## Input

`solve_table(pbn=..., dealer="N", vulnerability="None")`

PBN uses the same format accepted by the canonical CLI.

## Output

The canonical CLI returns hand order N/E/S/W, strain order S/H/D/C/NT, the 5x4 DD table, par score and par contracts. The Python wrapper adds engine provenance fields only.

## Container

`Dockerfile.dds3` builds the pinned DDS source and the existing canonical wrapper once. This prevents a chat/session from needing to download and compile DDS at request time.

## Architectural rule

Image/PBN reconstruction and teaching explanations are outside this module. Numerical double-dummy values used by Bridge School must originate from this module. If the module cannot run, callers must report DDS unavailable rather than substitute another calculation.
