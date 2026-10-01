# Narrow resumable protocol query compatibility

ASSURED. Code preparation only; no new live session or upload authorized. Supersedes the query-schema portion and branch/operation of the same-ID recovery plan; retains its source, ID, destination, budget and mutation bounds.

## Established evidence

Owner browser summary for recovery run36942195657 (SHA4c25373433fa58327ea3de7d4ff5c44667c4221e) says: initiation accepted HTTP200; create_stage SESSION_VALIDATION; GUARD_REFUSED / QUERY_KEYS; session_validated=false; put_attempted=false; second_copy_verified=false. This establishes local rejection of the returned Location query-key set before ciphertext PUT. It does NOT identify its actual query names: those were not recorded. It also does not retroactively establish the first copy run's cause or eliminate an uncompleted session.

Read-only public research, no credential/session calls:

- Google [Drive Discovery](https://www.googleapis.com/discovery/v1/apis/drive/v3/rest), top-level parameters, defines `upload_protocol` as the media upload protocol and `uploadType` as the legacy protocol parameter. Google's [published client discovery copy](https://raw.githubusercontent.com/googleapis/google-api-python-client/main/googleapiclient/discovery_cache/documents/drive.v3.json) agrees.
- The official [Drive upload guide](https://developers.google.com/workspace/drive/api/guides/manage-uploads) shows legacy `uploadType=resumable` plus `upload_id` in Location; initiation HTTP200 and final200/201.
- Google's [Python client](https://raw.githubusercontent.com/googleapis/google-api-python-client/main/googleapiclient/http.py) accepts Location after initiation200 and sends its subsequent PUT there; it does not require the legacy query-key spelling. This is supporting compatibility evidence, not authorization to accept arbitrary query keys.

The official sources establish the additional protocol parameter name, not the exact Location issued in our run. Applying the resumable value to that protocol selector is the bounded compatibility inference here. No claim is made that the historical response used it or that this patch is proven to resolve that particular response. Further actual incompatible forms must fail closed; no speculative repeated runs.

## Exact change and security assessment

Accept exactly three query-key sets: `{uploadType, upload_id}`, `{upload_protocol, upload_id}`, or `{uploadType, upload_protocol, upload_id}`. Each protocol parameter present must occur exactly once and equal `resumable`; dual forms must agree. Mandatory upload_id keeps its existing length/character restriction. No protocol-less, unknown, extra, duplicate, blank, conflicting or credential query parameters are accepted.

The accepted URL language is deliberately broadened by one official protocol selector; it is not literally an unchanged allowlist. Origin HTTPS/www.googleapis.com, exact /upload/drive/v3/files path, no userinfo/port/fragment/redirect/proxy, and raw control/space refusal remain unchanged. The supported extra key selects the same resumable operation and cannot name a destination, file, account or redirect. It adds compatibility without expanding allowed network destinations or mutation types. Unknown keys such as credentials, redirect_uri or fields are still refused.

Return the validated server URL unchanged; never rewrite/drop its query parameters or expose it. Safe receipt adds only booleans for three predefined query names and a capped unknown-key count. No unknown key names, values or session identifiers are logged. This distinguishes future query schema mismatch safely, but cannot recover unrecorded historical keys.

## Preserved execution boundaries

Branch `test/drive-session-query-compat-20261001`; operation `DRIVE_SAME_ID_QUERY_COMPAT_RECOVERY_V1`, new approval `DRIVE_SAME_ID_QUERY_COMPAT_RECOVERY_V1:<review SHA>:one-create-approved`. Exact reviewed SHA, baseline main1440920191e1778fb9a9ba24e6701937a1a7459c, owner actor/triggering actor, attempt1, <=5min and fresh Unix deadline. Previous approvals do not match.

Same Drive ID1hrg64wNgiRuxF4tCRj52YDLVWHSrhQKF; no new ID, broad listing, new auth, new dump, overwrite/delete/retry. Existing file -> verify only. Exact file404 checks plus fixed OCI ciphertext hash, fresh identity/ACL/main gates, durable UNKNOWN intent, at most one POST+one PUT,409 stop, independent Drive readback/hash/privacy checks remain unchanged. No mutation is performed during preparation or tests.

## Offline validation

Existing tests plus protocol-alias and dual-form cases with final200/201, opaque URL preservation, duplicate/conflicting/blank protocol rejection, forbidden extra keys, hostile origins/paths/ports/fragments/control characters and no query-value/unknown-name leakage. Independent I2 review before publication. Current verified OCI backup remains intact; Drive copy remains unverified until actual readback proves it. Publication is not a live approval.
