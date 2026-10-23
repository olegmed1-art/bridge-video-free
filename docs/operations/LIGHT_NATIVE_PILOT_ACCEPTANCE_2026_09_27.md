# One native Light pilot: exact-head read-only audit

This document assigns one bounded acceptance audit of the native Light admission gate and adapter. It is the target of one native pilot, not authority to start production or process historical work.

## Scope

Inspect these existing files at the exact head supplied in the authoritative dispatch:

- `oracle_autopilot/light_native_launch_gate.py`
- `oracle_autopilot/light_native_adapter.py`
- `tests/test_oracle_autopilot_light_native_launch_gate.py`
- `tests/test_oracle_autopilot_light_native_adapter.py`

Verify that HOLD prevents loading the executor, unknown or expired admission refuses, exact target/head and read-only flags remain enforced, and one accepted request cannot submit a second Cloud task.

## Bounded verification

First compare `git rev-parse HEAD` to the dispatch's exact expected head. A mismatch is BLOCKED/TARGET_HEAD_CHANGED.

Run once, without installing dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_oracle_autopilot_light_native_launch_gate.py tests/test_oracle_autopilot_light_native_adapter.py
```

Record the actual result and inspect the relevant guards. If a dependency is unavailable or any check fails, report BLOCKED with a truthful concise code. Report SUCCEEDED/AUDIT_PASSED only when the exact head matches, all selected tests pass and the scoped review finds no contradiction.

Preserve every original file. Do not repair, install, commit, push, merge, deploy, access production credentials or databases, call paid services, or expand the task. Only the disposable transport report explicitly required by the trusted native bridge is permitted; it is never published into this target branch.

The controller must independently verify the provider result and matching terminal database records, restore service HOLD and the original controls, and retain the evidence. This target PR stays open and is not auto-merged by the pilot.
