# Independent review of the finite execution session

2026-10-19 UTC. Governance: ASSURED. Completed assurance: I2 for the frozen
source and synthetic-port scope below. Actual PID1/CI and production acceptance
are separate evidence.

Reviewer task `/root/review2193_i2_record` was launched with model `gpt-5.6-sol`,
reasoning `high`, without inherited conversation; the author/root executor is
GPT-6. The same independent task reviewed this new scope after the PR2193 claim
fixes. It did not author these execution/controller changes. This different-model
launch is the independence basis, rather than the implementation's tests.

Root identified and checked the base as main
`6c4b8387d0cdd47805f9ceab5b375c151c6b1f45`. The reviewer independently measured
the file hashes, but did not independently Git-verify that base object.

| Artifact | SHA-256 |
|---|---|
| ops/light_native_lane_execution.py | `d2c1553ca229d894db0cad6500c3927f779d26282dae0499c0ee2a952fe20362` |
| ops/light_native_lane_controller.py | `4e79d2809d35198129a1aee2597bf4acc2ed482a8fa8e87fa85d38d4dcbd05a0` |
| tests/test_light_native_execution_session.py | `4217ce52d7379af0c92fc1e108a327701bff4094cd37f7619c68a6a89a96fcab` |
| unchanged ops/light_native_service_unit.py | `2d1422303a0649445d0b68790f13b61ea9698964d64e0cdc10f4d9172dd5e231` |
| adapted tests/test_light_native_lane_execution.py | `a66c04bf50bf4e60a282a0ddc863e2ce3d7e0f62a7df5cab6d2583db85cf92d8` |
| adapted tests/test_light_native_lane_controller.py | `d2fdb3a72b4c4302a40b6ce0eb3224f7a1a826c1dca61aa7bb4ee8a41f44f00f` |
| unchanged ops/light_native_service_authority.py | `b9932d99ac790fbfc98e42a98da5eb65534acd170564360d061871e310cbcb78` |

The reviewer found no defect in the reviewed scope. It traced the original
boot/wall/monotonic clock and GHA deadline, exact v3 execution and v1/v2
restore-only parsing, protected request/context/script/owner-pointer binding,
durable create-only intent and first record, two unit/process observations,
immutable PID1 timer, backward clocks and sticky closure. It also checked that
GHA remains mandatory before workload effects, cleanup is independent of live
authority, and the existing HostAuthority descendant check remains unchanged.
All six pinned historical helper-set name digests were independently recomputed.

Three additional focused counterexamples executed against actual definitions
with synthetic filesystem, clock, systemctl, process and legacy ports:

| Counterexample | Observed outcome |
|---|---|
| Owner execution pointer changes during initial identity collection | LANE_EXEC_SESSION_POINTER; intent retained, no first binding |
| Coordinated unit/process invocation and start change after first binding | LANE_EXEC_SESSION_CLOSED; failed=True |
| Wall expiry is earlier than duration and original GHA ceilings | Exact monotonic end 141000000000 ns, bounded by wall expiry |

The exact already-executed script is retained at
[light_native_execution_session_i2_counterexamples.py](../../tests/fixtures/light_native_execution_session_i2_counterexamples.py),
SHA-256 `b4cc01a2b83ed99bc3c3e729ef8391c3ef4bde61dca2d19f3f7797dd4d841b80`.
It was saved without rerunning it. Its final generic REFUSED output label is a
manual printer convention after the numeric ceiling assertion; it is not a
product refusal code. The script records source pins in comments, without
enforcing them in code. It is a review artifact, not an added CI proof.

The reviewer did not run existing suites, PID1/systemd services, GitHub Actions,
database, provider, host, IBM or deployment operations. This acceptance does
not establish installation, actual GHA authentication, full local/remote drain,
production admission, production restore or HOST delegation. The adapted legacy
pytest fixtures use explicitly synthetic session/clock ports; the new actual
session contracts and disposable PID1 fixture are separate evidence.

See [finite execution session](native-execution-session.md) for the operational
boundary and remaining production obligations. A changed pinned source requires
a fresh assessment of this review's applicability.
