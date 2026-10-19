# Independent review of the finite execution session

2026-10-19 UTC. Governance: ASSURED. The first section records the initial I2
assessment of the frozen source below. Automated review subsequently exposed
four defects, independently reproduced by the reviewer. That initial assessment
is withdrawn as final approval; it remains historical evidence. Actual PID1/CI
and production acceptance are separate evidence.

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
pytest fixtures use explicitly synthetic session/clock ports. The implementation's
session contracts are separate checks; the disposable PID1 fixture is outside
this review and its actual run remains pending.

See [finite execution session](native-execution-session.md) for the operational
boundary and remaining production obligations. A changed pinned source requires
a fresh assessment of this review's applicability.

## PR2199 review follow-up

Automated review [5577854829](https://github.com/olegmed1-art/bridge-video-free/pull/2199#pullrequestreview-5577854829)
examined head `80cae0765de71ed6f1cb5d4544d2e0fefd1bb4b2`. The different-model
reviewer independently confirmed all four claims; the first assessment above
did not catch them and is not approval of this revision.

| Claim | Historical reproduction | Corrected boundary |
|---|---|---|
| Orphan first record silently gains an intent | FileExistsError after creating intent; first bytes unchanged | Either existing file blocks before readiness and again before exclusive intent creation |
| Type=exec startup race consumes intent | activating/start refuses after creating intent | Read-only readiness before intent, at most three seconds and the original end; fresh clocks and protected inputs throughout |
| Unavailable ExecStart PID/start accepted | Session accepted MainPID 4242 while ExecStart reported PID zero | Positive expected ExecStart PID and available timestamp; zero form reserved for unrun ExecStopPost |
| Deferred guard dependencies omitted from CI | Recursive closure 36; trigger missed 18, session selector missed 26 | Recursive ast.walk includes function-local imports; all 36 trigger/select session proof |

The historical source counterexample body and outputs are retained without a
rerun in [review2199 counterexamples](../../tests/fixtures/light_native_execution_session_review2199_counterexamples.py),
SHA-256 `b0cb22e77dbdc5a46c4f26e81eab5067c06a38ea15640b52d954f706d12058fe`.
Those three bodies are verbatim; the separately labelled selector extractor is
a subsequent, unexecuted reconstruction of the recorded method. Historical
reproduction requires the frozen 80ca checkout. That manual artifact does not
enforce source pins and is not a CI proof.

The corrected source requires identical full active identity before and after
intent, then exclusively creates first binding. A crash before intent means no
session was accepted; a crash after intent retains its reconciliation barrier.
No preflight reads credentials, imports the database driver or admits effects.
The disposable PID1 fixture now calls enter immediately. An explicitly CI-only
ExecStartPost gate checks actual activating/no-intent state, both released and
unreleased; an orphan-first case preserves bytes and full ns metadata. Its 11
guard AST nodes match the production source. It defines 8 groups / 14 isolated
units; local syntax/AST checks do not establish a Linux/PID1 run.

Four existing compatibility fixtures were corrected separately: an exact
pre-session helper-set name digest replaces a new/old hybrid; packed import
tests require the current 36 helpers; sequence tests explicitly use synthetic
v3 session and origin-clock ports. No product validator was relaxed. Local
26 session tests and 11 selector tests passed without skips. The existing
Linux recovery/driver cases require CI; their earlier failures are not closed
by these portable checks.

| Updated artifact | SHA-256 |
|---|---|
| ops/light_native_lane_execution.py | `930780e00a43454ae3df41a57d7aad77a708909788aa8c05989d643b0f8668e7` |
| tests/test_light_native_execution_session.py | `b1e04e5b5c39d68287c322e4b7bbd2d0d17d36025bd750ac8e5a5e2ed23cb6af` |
| tests/fixtures/light_native_execution_session_systemd.py | `6e307b473fae28b93919d854239ee0d2070c0461dbd6f648645fef5257feaacd` |
| .github/workflows/native-host-preflight.yml | `41616d470ace805be92b4fbd0b5ace65b5f68e092be1148365809e1c91f51a89` |
| tests/test_native_host_preflight_scope.py | `292336379dd3e76a7cecd8e507f773525c990ab4b30d6a630a726f3229da76ad` |

Controller, process collector and HostAuthority pins above are unchanged.
The same different-model reviewer independently measured all six updated and
controller pins, traced the corrected boundary, and accepted this revision for
source/synthetic-port and selector scope, with no unresolved source finding in
that scope. It executed these additional focused checks without existing suites:

| Fresh counterexample | Observed outcome |
|---|---|
| Orphan first record | REENTRY; writes empty; orphan preserved |
| Pending activation reaches its bound | READINESS; writes empty |
| Protected input drifts while pending | Refusal; writes empty |
| Pending becomes active | Accepted after one 0.05 s wait; original end unchanged |
| ExecStart PID zero and unavailable start | SESSION_UNIT; writes empty |
| Timer identity drifts after intent | INPUT_CHANGED; only intent retained |
| Recursive source selector closure | 36 paths; zero trigger or session omissions |

These table labels abbreviate the full LANE_EXEC_SESSION error codes. The exact
already-executed script was saved without rerun as
[final review counterexamples](../../tests/fixtures/light_native_execution_session_review2199_final_counterexamples.py),
SHA-256 `a123dbca6820bc1ec115043fcf1b6f4e9dd7ffe63af65f4606a91562710befbc`.
The additional importlib escape scan found only private-wheel imports of
psycopg, psycopg_binary and typing_extensions, not another repository source.

The reviewer also checked the unchanged guard AST and startup fixture design.
This I2 acceptance does not establish a new actual Linux/PID1 run, actual GHA
authentication, production host/delegation, database/provider, workload/admission,
production cleanup/restore, installation or service acceptance. CI remains a
separate requirement. No IBM or service operation was performed by the reviewer.
