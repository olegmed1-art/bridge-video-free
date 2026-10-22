# Continuation student envelope: not wired to learner delivery

`make_continuation_task(..., information_mode="human")` returns a **private**
continuation. It retains the remaining `deal`, complete `original_deal`, lineage
and internal annotations. `audit_information_mask()` checks the cached hand mask;
passing that audit is not permission to serialize the whole task for a student.

## Consumer trace

At main following PR #2152 (`1489af80e9a10b8de8726f845aaf1ea07a700731`):

| Consumer | Role |
| --- | --- |
| `v23_core.make_continuation_task` | Builds the private continuation |
| `v23_runtime.make_continuation_task` | Installs the partial-position parser and audits the mask |
| `v23_runtime_selftest.main` | Existing human-mode consumer |
| `run_stage_v23` | Imports readiness/version helpers, not the human constructor |
| `algorithm_review_v23` | Imports the version, not the human constructor |

No learner-facing route or serialization consumer of this v23 object was found.
`human_view.build_human_view` and `continuation_tasks_from_line` use separate
contracts and are unchanged. The teacher routing and site are unchanged.
The explicit projection follows the pattern of `ShapeExercise.student_view`.

**Integration status: not-wired.** The existing runtime selftest contains
`check_student_serialization_boundary`, an in-memory integration fixture. It
exercises the private runtime task, projection, final validation and UTF-8 JSON
bytes. It does not implement an endpoint or establish end-to-end delivery privacy.

## Contract for a future adapter

Use the runtime exports so partial positions retain their existing parser:

```python
import json
from v23_runtime import (
    continuation_student_view,
    validate_continuation_student_view,
)

envelope = continuation_student_view(private_task)
validate_continuation_student_view(envelope, source_task=private_task)
wire = json.dumps(envelope).encode("utf-8")
```

The adapter must bind the correct trusted private task to the student on the
server. Validate the **final complete object** immediately before serialization
and send exactly those bytes. Do not decorate the checked result with metadata,
explanations, internal IDs, wrappers or DDS answers. A separate delivery channel
or subsequently generated explanation needs its own contract.

The projection rebuilds visible hands and legal cards from the private position,
omits internal IDs and free text, and reveals dummy only after public play.
Auction data accepts only canonical call tokens. The validator checks exact
keys/types recursively and equality to a fresh projection of the trusted source;
this also rejects a schema-valid substituted card. It does not authenticate the
source task or decide which student is entitled to it.

## Evidence boundary

The existing `v23-runtime` fast selftest checks that:

- the whole internal human task fails delivery despite passing its mask audit;
- safe projection bytes round-trip through JSON and pass final validation;
- late root/nested metadata, explanation wrappers and substituted cards fail
  before the fixture sink receives any bytes;
- hidden-card changes preserve the complete public envelope;
- the private task stays intact and works with the internal prediction consumer.

The constructor and internal DDS consumers remain compatible. Tests use synthetic
positions and local card logic; they do not call a DDS engine, database or live
service. No production leak, deployed fix or learner-route integration is claimed.
