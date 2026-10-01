# Exact backup bucket metadata probe

2026-10-01. ASSURED / I2. Preparation only; live dispatch belongs to the owner session.

## Authorization and scope

At 16:38:39 UTC the owner explicitly approved using the existing broader OCI API-key
connection only to read settings of `bridge-light-autopilot-backups`. This is a
separate authorized route, not automatic fallback from the dedicated readonly
principal. No credential is extracted, published, or saved to a key/config file.
Only existing `OCI_CLI_USER/TENANCY/FINGERPRINT/KEY_CONTENT/REGION` GitHub secrets
are supplied to the final job step. No new IAM identity, key or grant is created.

The code pins the existing tenancy literal, `eu-frankfurt-1`, and exact bucket name.
Namespace is obtained from the API, never transcribed from the screenshot. The
bucket response must match name, namespace and root compartment before metadata
is accepted. API allowlist (three GETs, no retries):

1. `get_namespace(compartment_id=TENANCY)` for addressing.
2. `get_bucket(namespace_name=namespace, bucket_name=BUCKET)` with approximate size,
   object count and auto-tiering fields.
3. `get_object_lifecycle_policy(namespace_name=namespace, bucket_name=BUCKET)`.

No IAM queries, other bucket list, object list, object contents, uploads, create,
delete, overwrite, PAR creation, or provider configuration changes. The old candidate
writer is never invoked. Helpers imported from the previous readonly script perform
only local parsing/formatting; that script's inventory and auth route are not run.

Safe output: fixed identity-match booleans, namespace, fixed bucket/region, privacy,
tier, encryption class (no KMS key OCID), versioning, auto-tiering, events, approximate
bytes/count, lifecycle rule presence/count. No rule bodies/prefixes or raw API JSON.
Missing fields stay UNKNOWN. Any 401/403 stops with ACCESS_DENIED. A lifecycle 404
stops with NOT_FOUND_OR_NOT_VISIBLE and lifecycle UNKNOWN, while preserving already
verified bucket metadata. No claim about free quota, account tier, or upload rights.

## Review-only manual harness

The existing `oracle-epoch-readonly-probe.yml` is changed only in
`review/oci-readonly-inventory-20261001`. Main and its original jobs are unchanged.
This review revision replaces the previous readonly mode; it cannot dispatch that
identity again. Only one manual job, maximum five minutes, script cap 120 seconds.

Future dispatch parameters (not executed during preparation):

```text
workflow: oracle-epoch-readonly-probe.yml
ref: review/oci-readonly-inventory-20261001
source_run_id: oci-backup-bucket-metadata-v1:<exact reviewed commit SHA>
```

Repository, owner actor and triggering actor, exact branch, input/SHA and workflow
ref/SHA are checked before credentials. Checkout is pinned to event SHA. Offline
tests run before SDK installation; a real OCI 2.187.1 synthetic-key constructor test
runs after installation and before secrets. The explicit key_content SDK fix is
included. Output is sanitized stdout/step summary, no uploaded artifact or comment.

## Existing evidence, not a new API reading

Owner screenshot reports Standard/Private, Oracle-managed encryption, approximately
183 objects / 11.48 MiB, and disabled versioning/auto-tiering/events. These values
are not hardcoded as expected results, do not prove backup completeness, and do not
establish remaining free quota. The screenshot namespace has not been guessed.

Rollback: do not dispatch this review mode. No live operation, main merge, resource
mutation or IAM change is part of preparation.

## Validation

34 offline tests PASS, no skips, including real OCI SDK 2.187.1 with an ephemeral
synthetic key, blocked socket/HTTP connections and intercepted SDK transport.
The actual SDK emits GET for the three expected resource paths `/n`,
`/n/{namespaceName}/b/{bucketName}`, `/n/{namespaceName}/b/{bucketName}/l`.
Tests cover exact identity, call allowlist, denial stop, lifecycle404 unknown,
metadata unknowns, redaction, deadlines, key_content, setup failures and workflow gates.
YAML structure validation PASS. Independent different-model I2 review repeated all
34 tests: PASS, no blockers. Script SHA256:
`6884460b303ed5fab0645a80bc8928786ac1b6315a335286831849d906322fa9`.
No live result is claimed by these tests.

## Credential-format compatibility correction

Owner-dispatched run 36894699163 / job 110478646297 at commit
`a6db9ff2c01f0092387880c8a8c45c997cfdcf28` stopped with INVALID_CREDENTIAL_INPUT,
failed_stage credential_input at 2026-10-01T16:48:45Z. SDK synthetic checks passed;
the runtime client and OCI API reads were not reached. This does not establish that
the owner key is invalid, or identify which field failed.

Narrow source comparison with oracle-light-candidate-backup.yml and its writer:

- Five individual OCI_CLI_* secrets are used, not OCI_CLI_CONFIG. Historical env
  aliases were OCI_USER, OCI_TENANCY, OCI_FINGERPRINT, OCI_KEY, OCI_REGION. The new
  workflow uses the same source secrets under their original names.
- Both implementations pin the identical tenancy and eu-frankfurt-1.
- Historical scalar accepts a raw scalar, key=value, or multiline config-like text
  with exactly one matching nonempty key=value line, ignoring other keys. It strips
  CR and surrounding line whitespace, and refuses ambiguity/duplicate target keys.
- Our earlier strict scalar rejected internal newlines. This is a proven code
  compatibility difference; whether it caused this run is unknown without inspecting
  values, which was not done. The exact-bucket parser now matches the historical
  selection behavior, with unchanged target restrictions and field validation.
- PEM literal backslash-r/backslash-n and CRLF normalization is retained; outer
  whitespace is accepted. No key decoding, hashing, length disclosure or persistence.

Safe next-run diagnostics identify only a fixed field/reason, e.g.
CREDENTIAL_USER_MISSING, CREDENTIAL_REGION_FORMAT_INVALID,
CREDENTIAL_TENANCY_TARGET_MISMATCH, CREDENTIAL_FINGERPRINT_FORMAT_INVALID,
CREDENTIAL_KEY_CONTENT_FORMAT_UNSUPPORTED. A format code is not a cryptographic
validity verdict. Secret contents remain unread during preparation; no new live
attempt is performed. Once separately approved, the next existing exact-bucket run
will either pass parsing or name the first blocked field before SDK/API access.
Compatibility-fix verification: 38 tests PASS without skips, including the real SDK
synthetic-key/network-blocked test. Independent I2 review repeated all 38 tests:
PASS, no blockers. Runtime cause remains unproven; no new live attempt.
