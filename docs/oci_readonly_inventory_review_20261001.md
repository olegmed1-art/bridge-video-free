# OCI inventory review probe — 2026-10-01

Status: PREPARED, NOT LIVE-EXECUTED. Governance: ASSURED / independent I2 review.
Base main: `1440920191e1778fb9a9ba24e6701937a1a7459c`.
Review branch: `review/oci-readonly-inventory-20261001`.

## Purpose and limits

Read metadata through the existing `OCI_READONLY_CLI_*` principal, without changing
cloud resources, IAM, credentials, objects, services, queues, or databases.
The probe checks the fixed tenancy identity and its home-region key, then namespace,
bucket list and exact bucket metadata in **the tenancy root compartment only, in
OCI region eu-frankfurt-1**. No inference about other compartments.
The fixed tenancy is sourced from existing `ops/oci_light_access_audit.py`.
Historical instance IDs are deliberately not used to guess a compartment.

The allowlist is `get_tenancy`, `get_namespace`, `list_buckets`, `get_bucket`.
Bucket metadata includes privacy, tier, approximate bytes and object count; object
names and contents are never requested. Missing privacy is UNKNOWN. Metadata names
are validated before output; bucket hashes permit comparison when a name is redacted.
No IAM-policy inspection, account-type determination, quota/billing API or free-tier
calculation is included; those fields remain UNKNOWN / NOT_QUERIED.

Any 401/403 ends the entire probe with ACCESS_DENIED, including an initial tenancy
inspection denial. A 404 is NOT_FOUND_OR_NOT_VISIBLE, not proof of absence or lack of
permissions. No broad-credential fallback or permission repair exists. Three pages,
20 buckets, 180-second process budget; SDK retries disabled. Incomplete inventory
is explicit. Credentials are parsed only from the five dedicated environment names,
the PEM stays in memory, and raw responses/exceptions are never printed.

## Existing manual harness, no main change

Only the review-branch copy of `.github/workflows/oracle-epoch-readonly-probe.yml`
is replaced. This workflow already has workflow_dispatch on main, so GitHub's
existing Run workflow UI / API can select the review branch without merging.
The existing single string input `source_run_id` is retained for schema compatibility;
its review meaning is the exact literal marker plus reviewed commit SHA.
There are no old epoch jobs, comment triggers, power/backup operations, artifact
uploads, or issue write permissions in this branch copy.

**Future parameters, not authorization to execute:**

- workflow: `oracle-epoch-readonly-probe.yml`
- ref: `review/oci-readonly-inventory-20261001`
- actor and triggering actor: `olegmed1-art`
- input `source_run_id`: `oci-readonly-inventory-v1:<reviewed full commit SHA>`

Before any future dispatch, verify the remote branch head equals that reviewed SHA.
The job gate checks event, repository, owner actor, exact branch and SHA input. The
first step additionally checks workflow SHA/ref before checkout or credentials.
Checkout uses immutable event SHA and does not persist credentials. A single job
has a five-minute ceiling. Offline tests and a bounded pinned SDK install run before
the step that receives the five readonly secrets. Output is sanitized JSON in job
stdout and step summary, never an uploaded raw JSON artifact.

## Evidence and remaining validation

- 17 offline tests PASS: denial stops, exact target, missing metadata, pagination
  bounds, time budget, malformed credentials, redaction, and workflow safety gates.
- YAML parsed with PyYAML 6.0.3; manual-only event, one job, permissions and timeout
  checked structurally.
- OCI SDK 2.187.1 wheel SHA256:
  `0c20492d852430ccc8e25ab12fec55c631e73c5be155067328f6b8322c6c6e3b`.
  Signer/client method signatures and optional approximate-size/count fields
  inspected offline in this exact package. This is not a live API validation.
- Independent different-model I2 review: PASS, no blocking finding. Reviewed script
  SHA256 `0e3f41443dc42b41973ef300fcd0d5a6a5166bba26ad457b901f58459965d853`.
- Other push workflows were inspected: branch filters exclude this branch, or path
  filters exclude these changed files; no operational publication trigger identified.

Live IAM rights, bucket existence/usage, home region and free balance remain
unverified. A successful empty root inventory does not establish no buckets elsewhere.
No live run, IAM change, main merge, resource creation or upload is authorized by
this preparation. Rollback is to leave the review branch undispatched; main is intact.

References:
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
- https://docs.oracle.com/en-us/iaas/tools/python/latest/api/object_storage/client/oci.object_storage.ObjectStorageClient.html
