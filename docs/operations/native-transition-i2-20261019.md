# Independent review of the finite transition kernel

Review date: 2026-10-19 UTC. Governance class: ASSURED. Completed assurance:
I2 for the exact software/kernel scope below; no production acceptance.

Root launch metadata records task `/root/review2193_i2_record`,
model `gpt-5.6-sol`, reasoning `high`, with no inherited conversation. The
implementing root agent is GPT-6. This different-model launch is the independence
basis under AGENTS.md; the implementation's unit tests and systemd fixture are
not themselves the basis for I2.

The review task identified the base as
`b0afa600f10b5a53c6bd7be14e513161c4a57b28`. The reviewer independently measured
the file hashes below, but did not independently verify the base Git object.
Root checked the checkout base before launching the review.

| Artifact | SHA-256 |
|---|---|
| ops/light_native_legacy_transition.py | `3d854a6344503f99afc54ddcd641504513bb578cbbe74ba4022051a89aba4083` |
| tests/test_light_native_legacy_transition.py | `bf2715e6d03cf6e4bf21f4907b7f92b8b0b915534e4864c50b5594797fe94605` |
| tests/fixtures/light_native_legacy_transition_systemd.py | `96875c478a0eddb54ada7d55e8f0bbe3498318f0243fce4fb86d22458185d436` |
| ops/light_native_service_authority.py | `b9932d99ac790fbfc98e42a98da5eb65534acd170564360d061871e310cbcb78` |
| AGENTS.md | `9a27f2bb554925b0972c6e2d5d09794b83f1ad5d157dab092d9dcde81b867d9e` |

The reviewer traced canonical request parsing and pins, finite forward/recovery
agreements, cleanup ownership, create-only journals, effect intents, fresh
snapshot and Native checks, drain binding, same-boot recovery deadlines,
lineage reconstruction, lost acknowledgements, containment and ACTIVE replay
prevention. No safety defect was found in that reviewed kernel scope.

Three new focused counterexamples used synthetic ports:

| Counterexample | Reported outcome |
|---|---|
| Native HOLD drifts immediately after a valid drain read | UNKNOWN; drop effects 0 |
| Canonical journal tamper extends the retained recovery anchor | UNKNOWN; ACTIVE starts 0 |
| Forged late active-started receipt after one valid restoration | UNKNOWN; ACTIVE starts remain 1 |

The exact previously executed script is retained at
[light_native_transition_i2_counterexamples.py](../../tests/fixtures/light_native_transition_i2_counterexamples.py),
SHA-256 `d241564890bf7175c6042faabe87ff24f9ad68bd1093933416e8fc50dee2258c`.
It was saved without rerunning it or changing the kernel, existing tests or
systemd fixture. It has no embedded kernel-hash assertion; the source hashes
above were measured separately. Its numeric output in the first case is the
drop count, despite the original script's generic output label.

For optional reproduction from this repository's root, first verify the exact
source hashes above, then use the following stdlib invocation. This command was
not an additional execution during preparation of this record:

```sh
python -I -B -S -c "import pathlib,runpy,sys;sys.path.insert(0,str(pathlib.Path.cwd()));runpy.run_path('tests/fixtures/light_native_transition_i2_counterexamples.py',run_name='__main__')"
```

No existing 165/12/119 suite, PID1 scenario, CI, database, provider, service or
production action was launched for this review. The prior CI rehearsal remains
[run 39309009304](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39309009304);
it is separate evidence, not a substitute for independent review.

This result does not establish installation acceptance, actual database,
provider or lease drain, service admission, or production execution. The
reviewed kernel has no production transition adapter. The systemd fixture's
database/provider evidence is synthetic; its recovery guard checks request and
source binding but does not rehearse production STOP semantics. Staging
service_authority.py in that fixture does not wire it as transition authority.

The earlier ASSURED/I2 wording in PR2193 had no repository-bound independent
review artifact. This is a new contemporaneous review record. It is not evidence
that publicly existed at the earlier merge, and the newer HOST PID1 tests do
not close the older review claims. A change to the pinned kernel or its
authority boundaries requires a fresh assessment of this result's applicability.

## Separate source review of the PR2193 claim fixes

The same independently launched model subsequently reviewed the frozen codec
and workflow-selector fixes. It found no source defect in that narrow scope.
It did not run tests, CI or Git for this second review, and does not independently
attest root's reported test execution or the comparison against the base Git
tree. CI and root's exact-base comparison are separate evidence.

Independently measured reviewed source hashes:

| Artifact | SHA-256 |
|---|---|
| ops/light_native_lane_execution.py | `ac8fb7b14e1673f47c1f055ed9555aa8a985c6e064fa8844592cce1a5166ea42` |
| ops/light_native_lane_controller.py | `038d30633c9acb8629f99b957e9eb613583ca8a729ac96412476c4f840e41f6a` |
| ops/light_native_lane_issuer.py | `43550749d5e98742ae983435edf06d55801b35e85540698f18a6459b78f68dd3` |
| tests/test_light_native_plan_codec.py | `fda9236cf58f3f0051e78cb56f8b6ba942e9999e23a8d740f42c80b431f7a71c` |
| .github/workflows/native-host-preflight.yml | `bb20351c66e1050fcb9fd9d43fb98466f6dd77a30be34193a0c0583a95394f2b` |
| tests/test_native_host_preflight_scope.py | `ba293f5acca50c83bca6ba600a91f8f91b24a30dbb7344c50dc10313bc8f0144` |

The review checked the dedicated historical ASCII Plan decoder, fixed refusal
boundary, unchanged generic UTF-8 codec, accepted-hash verification before Plan
decoding, unchanged Plan bytes into full intake.Plan/Agreement validation, and
the targeted actual-code test harnesses. It also checked trigger/selector
coverage of all 14 source paths derived from the loader calls and the portable
codec job's separation from legacy, transition and HOST proofs.

This is I2 source review of those exact fixes, not production, installation,
database, provider, admission or PID1 acceptance. The AST harnesses use synthetic
package and observation ports. Their scope is the pure codecs, validators and
selector paths they actually execute.

The first Linux codec run [39320287118](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39320287118)
ran 22 tests and failed two deep-input subcases. Python 3.12 accepted inputs for
which the local runtime raised RecursionError. The different-model reviewer then
reviewed the bounded byte scanner and expanded test source without running them.
It accepted the deterministic depth-64 boundary, quoted-bracket and escape
handling, and unchanged historical byte codec. The two hashes above supersede
the earlier execution/test hashes; the other four reviewed hashes are unchanged.
The subsequent execution-file change only clarifies the docstring's bound.

Depth 64 is a deliberate new acceptance limit. Host validate_policy and downstream
intake.Plan do not currently impose that same explicit depth limit. Plans within
the bound retain their original accepted bytes and hashes; deeper Plans fail
closed before issuer effects. This is an availability/compatibility restriction,
not evidence that every theoretically host-accepted Plan is issuer-admissible.
The failed CI is discovery evidence, not a successful check.
