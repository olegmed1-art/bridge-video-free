# Gambler pixel scale: isolated Diana20 integration

Change `GAMBLER-PIXEL-SCALE-20261022`, ASSURED. This is a local, tested
source candidate for the current Diana20 worker/caller interface. Publication,
cloud CI, production installation and live readback have not been performed.

## Scope decision and protected baseline

The public patch adds eight files. It leaves the existing r26.3 production
workflow, entrypoint, runtime, recognizer dependencies and META guard unchanged.
The former combined r26/Diana20 candidate is superseded for publication.

The META contract binds the declared revision to one entrypoint, adapter,
runtime and exact recognizer dependency hashes. Its history establishes the
intended procedure: `7d1acc0b` introduced the exact runtime binding; `e258b216`
updated the revision, production route, recognizer contract and tests together;
`2acdddc1` restored a pinned dependency and isolated successor behavior. Merely
changing expected hashes would not establish a new verified release contract.

Diana20 does not require the old r26 integration edits. The smaller solution is
to keep every protected byte, including `gambler_classic_reference.py`, at main.
The META guard is still checked locally even though this narrower PR does not
match its path trigger. Application-specific helpers live under `tools`,
preserving the existing student-agnostic core guard. Neither guard is relaxed.

## Authenticated source chain

The saved installed Margin worker has source SHA
`d907fc1fbbe2b1ee3f061b81b4fd60180d58ee14b1478c94ea53bfb9a674e56a` and release
manifest SHA `af7b1e91f30e34943e3ae61e8d8e2c8ed8a6ee00929406862b735b538217f21c`.
The saved factor-TIE candidate has worker SHA
`cad226f6279788ef0dad96350c78157bfa3104ee37b1cdb04e665f8bacca1b92` and manifest
SHA `a85c419ec6ca58f47b6fdb266d15280d9f471c8e0aefa5b07e345ce14764dc83`.
These authenticate saved source identity, not current live deployment state.

The builder accepts only these two exact bases and creates a new bundle. The
native reference loader/decoder remains byte-identical to its saved base.
Bounded binary asset I/O is implemented only in the new application helper;
the original SHA allowlist and byte validation remain authoritative. Test
fixtures independently normalize 16-bit PNG cells into 8-bit video pixels.
No global monkeypatch or change to the pinned legacy decoder is needed.

## Recognition behavior

Each frame is measured before discovery or identity matching. At least two
qualifying complete pixel boundaries are required. Width and height are
reported as measured or unknown; occluded width is never silently inferred.
Conflicting scales return TIE. Missing or nonnative geometry returns UNKNOWN.
The selected available sprite must match both native dimensions within one
pixel. The native observer receives one sprite and is invoked at most once.
No card labels, desired count, rank-score comparison between sizes or previous
frame scale enters selection. Geometry90 remains OFF.

The existing source/PTS, profile, UI/compass, snapshot and ROI-hash gates remain.
Only v5 uses the existing Margin/factor processing. Other variants preserve
whole-corner observations and UNKNOWNs without Margin additions. Original
`cards`, `unknown`, raw candidates, ROI receipts and effective observations are
preserved. Scale metadata is additive. A shared sealed response validator
requires measured dimensions, canonical native pins, runtime agreement, strict
false authority flags, no cards without matching and no non-v5 additions.

The fixed 1920x1010 layout is unchanged. Large bottom cards at variants 6-8 may
cross its table boundary and be withheld. AC/KD and 2S/JD pairs do not expose
two qualifying faces and abstain at every native size. These coverage limits
remain explicit; thresholds are not fitted to card labels.

## Routes and verification

| Route | Source integration | Qualification still required |
|---|---|---|
| Installed Margin base, direct worker JSONL | Eight native variants; original factor flag false | Selected release installation/readback |
| Factor-TIE candidate base, direct worker JSONL | Eight native variants; original factor fix retained | Candidate approval and installation/readback |
| Matching `GeometryClient` IPC | Worker pins, asset-root argument and response validation | Owning application selection |
| Existing generic ACTIVE worker entry | Forwards arguments to the proposed versioned bundle; source unchanged | Proposed pointer not applied |
| Prepared owner-local caller and command ABI | Matching component, binary assets, command and response ABI | Actual POSIX owner preflight and existing binding requirements |
| r26.3/r26.4/r26.5/auction candidate and historical replay | Unchanged; no new scale integration claimed | Separate scope |

Generated workers run through their actual IPC clients in fresh subprocesses.
A persistent process visits v5, v1-v4, v6-v8, then v5 again, followed by blank,
mixed-scale and nonnative frames. Positive fixtures require a matcher call,
pixel receipts and actual corner candidates/UNKNOWNs. Three-ace construction
oracles return AC/AH/AS at v1-v5, and AH with two uncertain corners at v6-v8.
This is a bounded synthetic test, not a real-video accuracy estimate.

Six hash-frozen real frames are compared against each exact base. Cards,
UNKNOWNs, raw candidates, hand-row receipts, UI/seat mapping and effective
Margin results must remain equal. All six real frames are v5; three were
boundary-development frames and three later holdouts. No hand labels are used.
Public CI fixture skips do not qualify real behavior at eight scales.

The complete new private suite passed 83 tests without skips; the existing
candidate regression passed 26. Independent I2 evidence comprises 52 focused
tests and 24 actual-worker observations using a separate construction decoder.
These overlapping counts are not added together or treated as accuracy data.

The exact existing META step, student-agnostic/native-default guards, host
script/systemd/retention boundaries, dependency audit, committed-secret gate,
Issue 881 static guards and module compilation passed locally on an LF Git
snapshot. The native writer inventory report passed its exact assertions.

The public/legacy suite on Windows recorded 128 passed, 42 skipped and one
legacy PNG-loader failure; the same failure was reproduced on clean main.
Additional existing Linux-only checks could not pass on Windows because of
fcntl/getuid, POSIX metadata/symlinks, shell and text-encoding assumptions.
They remain pending on their intended runners. No frozen code, expected hash,
test or gate was weakened to hide these platform limits. Hosted Linux,
Docker/Postgres and scanner jobs are a distinct CI stage, not a claimed pass.

## Exact public delta

1. `.github/workflows/gambler-pixel-scale-ci.yml`
2. `bridge_vision/gambler_card_scale.py`
3. `tools/diana20_gambler_scale.py`
4. `tools/diana20_scale_contract.py`
5. `scripts/build_diana20_scale_bundle.py`
6. `tests/test_gambler_card_scale.py`
7. `tests/test_diana20_gambler_scale.py`
8. `docs/DIANA20_PIXEL_SCALE_INTEGRATION_20261022.md`

Each private generated bundle changes six payload paths: the worker, vendor
pin manifest, layout adapter, two new scale helpers and matching IPC client.
SOURCE/CODE manifests seal the complete payload. A separate caller proposal
contains the recognizer adapter, prepared-frame command, component JSON,
proposed pointer and manifest. Owner/lifecycle/dispatch and PBN writer logic
are unchanged. Assets, frames and private generated bundles stay outside Git.

## Publication and installation plan

1. Reconcile fresh main and the PBN scope through the parent. Recheck the exact
   source head, protected byte hashes, guards and independent review. Publish
   only this eight-file source delta after its scope is approved.
2. Open one PR and run one bounded automatic CI cycle. The scale workflow uses
   exact-head checkout, read-only permissions and pinned dependencies. Existing
   candidate tests run in a fresh process. The eight-file delta matches nine
   PR workflows / twelve jobs with 123 aggregate timeout minutes; the feature
   branch push matches no workflow. This is a timeout ceiling, not a runtime
   or cost estimate. META and partial-shadow no longer match the narrowed
   paths. Recheck triggers/settings at publication; do not disable guards.
3. On an existing authorized private runner, provide `DIANA20_SAVED_SOURCE_ROOT`,
   `GAMBLER_CLASSIC_FIXTURE_ROOT` and `DIANA20_SCALE_FRAME_MANIFEST` and run
   `tests/test_gambler_card_scale.py` plus `tests/test_diana20_gambler_scale.py`.
   Require native-asset and source integration tests to run, not skip. Verify
   saved-frame parity and deterministic source/caller seals. No new paid runner
   or production service is needed for this qualification.
4. Default release target is the new versioned code-bundle directory
   `diana20-pixel-scale-source-v1-installed-margin`. Use the corresponding
   tie-candidate bundle only when that candidate is also approved. Exact paths
   and pointer bytes remain in the private proposal. Verify live current
   pointer/manifest and actual caller ownership before requesting installation.
5. After separate private target/risk approval, stage the chosen bundle and
   pinned assets, verify hashes, and install its matching caller/component and
   pointer as one release. Preserve the previous matching set. Do not change
   services, identities, permissions, keys, lifecycle, PBN or Geometry OFF.
6. Require real readback: entry verification, payload/asset pins, bounded
   authorized frame execution through the actual caller, scale and ROI receipts,
   and the existing output receipt where supported. Source installation alone
   is not verified deployment. Roll back by restoring the preserved matching
   pointer/caller/component set.
