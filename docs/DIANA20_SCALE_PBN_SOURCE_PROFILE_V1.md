# Diana20 scale-to-PBN source profile v1

The scale source bundle and the installed Margin worker have different version
and hash identities. They must not share a runtime profile. This opt-in adapter
accepts `diana20-scale-pbn-source-profile/v1-installed-margin` for the exact
`diana20-pixel-scale-source-v1-installed-margin` source contract. The existing
installed profile and all of its pins remain unchanged. The tie-candidate is
unsupported and fails closed; there is no fallback or wildcard match.

## Trust and scope

The operator supplies a private profile file and its exact SHA256 from an
independent trusted configuration. Neither is read from recognition rows. Its
five required fields are `profile_version`, `bundle_manifest_sha256`,
`caller_proposal_sha256`, `runtime_root`, and `source_sha256`. All keys and hashes
are exact. The root describes the prospective worker path, not proof that any
installation has happened. This repository contains no deployable private
profile, launcher, source bundle, sprite, frame, receipt or server configuration.

The loader reads saved files without importing or executing their Python code.
It hashes the source manifest, identical CODE_BUNDLE manifest, all 24 bundle
payloads, caller manifest, all four caller payloads, and all eight original
sprite byte streams. It checks the worker/helper/vendor graph, caller component,
proposed pointer, IPC worker/vendor constants, embedded native sprite pins and
component bindings. The resulting immutable profile rehashes its entire saved
snapshot before each row is admitted. A changed or missing byte fails closed.
Symlink/junction, traversal, duplicate and malformed identities are rejected.

The existing component also references an external ACTIVE launcher and proposed
CURRENT pointer. Source compatibility does not verify an installed launcher or
live pointer, interpreter, dependencies, service or owning transport. These
remain deployment obligations. All exported profile provenance explicitly says
`runtime_qualified=false`, `installation_verified=false`, `runtime_status=HOLD`.
Hashes establish saved integrity, not execution attestation or accuracy.

## Existing interfaces

`load_scale_pbn_profile(path, expected_sha256=..., bundle_root=...,
caller_root=..., asset_root=...)` returns a verified profile. The asset directory
uses the original `1/all.png` through `8/all.png` layout. The explicit requirement
for all eight assets is a source qualification requirement of this profile;
it does not change the worker's missing-sprite abstention behavior.

Pass the object as `scale_profile=` to the existing `adapt_diana20_row` or
`export_diana20_jsonl`. Without it, both APIs retain the original legacy behavior.
The existing CLI gains five all-or-nothing options: `--scale-source-profile`,
`--expected-scale-profile-sha256`, `--scale-bundle-root`, `--scale-caller-root`,
and `--scale-asset-root`. No new pipeline, serializer or writer is introduced.

Admission reuses `validate_native_scale_response` from PR2206. A selected scale
must be measured at an available native sprite with exact dimensions and hash;
inferred, inconsistent or forged scale claims fail closed. UNKNOWN/TIE preserve
empty results and exact corresponding scale status. Margin additions are allowed
only for v5; the other variants cannot add candidates or remove UNKNOWN entries.
Geometry, factor identity, canonical and human authority remain false.

The original row, baseline SHA, source/frame/time bindings, card history,
UNKNOWN history, addition indices and provenance are validated by the existing
PBN adapter. The exported JSONL copy retains exact input bytes. Scale version,
profile and scale evidence appear as source claims, never as an installed
release claim. The existing PBN serializer emits review output with `Deal "?"`.
Scale-profile input combined with qualified ownership recovery is deliberately
rejected until that separate producer/recovery profile is qualified. Unvalidated
reconstruction claims retain their existing comments-only behavior.

## Validation and integration boundary

Public unit tests use clearly synthetic source/asset bytes and a test-local trust
root. They cover all eight scale contracts, strict version/hash/graph checks,
per-file mutation, UNKNOWN/TIE, wrong aspect/scale, non-v5 additions, authority,
replay bindings, byte-preserving export and legacy admission. They do not execute
workers or assess vision accuracy. Native saved byte authentication and source
comparison receipts belong in the private evidence packet, not this repository.

This narrow patch depends on both PR2206 and PR2207; it changes neither published
branch. Apply only on a reviewed integration of those exact sources, then refresh
the heads and rerun the local tests. Geometry90 diagnostic ABI v2 is a separate
kit and is not a dependency. No CI, publication, merge, installation, service
change or promotion is authorized by this source-only contract.

Rollback before installation is to omit the explicit scale profile and keep the
legacy route. Any later installation needs its own exact target, dependency and
runtime readback approval. Runtime remains HOLD.
