# PR2193 review disposition

Source: [review 5576522444](https://github.com/olegmed1-art/bridge-video-free/pull/2193#pullrequestreview-5576522444),
reviewed commit `2b08a1bbbcb5c5b6dbe2452fc1db3d4228942894`.
The claims were checked against current base
`b0afa600f10b5a53c6bd7be14e513161c4a57b28`; none was closed by the later
HOST PID1 tests.

| Claim | Evidence and disposition |
|---|---|
| Unicode Plan accepted by host but rejected by issuer | Confirmed. An original ASCII-escaped Unicode Plan passed actual host.validate_policy, then issuer.validate failed at its raw Plan boundary with LANE_EXEC_RECORD. A dedicated bounded execution.parse_plan now preserves the historical ASCII codec and original bytes/hash. The controller exposes it and the issuer uses it. Generic records and issuer policy remain UTF-8. The actual pure issuer -> cycle -> controller.scope -> Plan/Agreement path is tested. |
| Codec source changes can skip their contract workflow | Confirmed. A regression derived all six source files loaded by HistoricalCodecContracts from its actual AST; every one lacked a trigger. Trigger paths and the selective codec output now cover those files and the integration test's actual loaded dependencies. A separate nonroot stdlib job runs the relevant codec/scope contracts without repeating unchanged HOST/transition proofs. Changes to old executable job bodies still select their respective proofs. |
| ASSURED/I2 designation lacks independently inspectable evidence | Confirmed as a documentation/provenance gap. ASSURED remains the required governance class. The repository did not contain a bound independent-review record, and portable/PID1 tests do not themselves establish I2. A new different-model review of the unchanged kernel and three focused counterexamples is recorded with provenance, hashes and limits in the linked receipt. It is not retroactive evidence of the earlier merge. |

The issuer was the single faulty raw Plan consumer found in this scan. Other
inspected paths use intake.Plan or a decoder that accepts historical ASCII
escaping without UTF-8 canonical re-encoding; retirement decoding therefore
needed no code change. Unicode in nested task data, supplementary-plane
characters, invalid UTF-8 re-encoding, altered accepted hashes, duplicate keys,
nonfinite values, invalid type/size and noncanonical bytes are distinct tests.
Plan JSON has an explicit structural depth limit of 64, independent of Python's
recursion behavior; brackets and escaped quotes inside strings do not count.
This is a deliberate new availability/compatibility bound. Host validate_policy
and downstream intake.Plan do not impose the same explicit limit. Original
bytes and hashes are preserved within it; deeper Plans fail before issuer effects.

The first portable Linux run [39320287118](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39320287118)
ran 22 tests and failed two deep-input subcases: Ubuntu's decoder accepted the
inputs, while the local runtime raised RecursionError. This exposed an implicit
runtime-dependent limit. The explicit scanner and exact 64/65-depth boundaries
replace that assumption. The failed run is retained; it is not a passing proof.

The new Plan decoder grants no admission and does not weaken Plan semantics,
source checks, HOLD, unknown-outcome handling or replay barriers. Full Plan
validation remains in intake.Plan at the existing downstream boundary. No
accepted historical Plan bytes, digest, policy or original baseline is rewritten.

Independent assurance evidence:
[finite transition kernel I2 receipt](native-transition-i2-20261019.md).
The exact executed counterexample script is retained separately from tests
authored with the implementation. The current production readiness, root
launcher, ISSUE transport and PID1 execution delegation remain separate work.
