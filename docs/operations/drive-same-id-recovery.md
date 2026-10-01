# One bounded same-ID recovery attempt

ASSURED; preparation only, new owner approval required. No live mutation is authorized by this document or publication.

## Evidence and unknown cause

Original copy run `36939347618` (`cbadb3a10c1f608ca3f6f54e8b58049a20c7ae92`) failed at create with outcome UNKNOWN, source ciphertext verified. Parent read the original-OAuth reconciliation run `36940525598` (`f7cc3f9a9e2b16b712cc876b989c6bd20b02bbb6`) summary: OBSERVED / NOT_FOUND_OR_NOT_VISIBLE, HTTP404, identity verified, second_copy_verified=false. This does not prove absence or expiry of an upload session. Historical cause remains unknown: logs cannot distinguish POST refusal, refused Location, PUT failure or rejected final reply.

Only allocated Drive ID `1hrg64wNgiRuxF4tCRj52YDLVWHSrhQKF` may be used. No generateIds, new ID, file-list search, update, overwrite, delete, retry, resume/status PUT or new authorization. Existing OCI source remains fixed at the original key, 8,238,960 bytes and SHA-256 `5878d3eef5f63753b39be3b247057c7694e1b381b3b5854832fbf14c085a48af`; source is read-only. No new database dump, connection, decryption or passphrase.

## Documented API compatibility

Google's official [resumable upload guide](https://developers.google.com/workspace/drive/api/guides/manage-uploads#resumable) specifies initiation HTTP200 with a Location such as `https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=xa298sd_sdlkj2`. Its single-request flow uses one full-content PUT; completion accepts HTTP200 or HTTP201. Its [pre-generated ID guidance](https://developers.google.com/workspace/drive/api/guides/manage-uploads#pre-generated-id) allows retry using the same ID and explains that an already-created file returns 409 instead of creating a duplicate.

The documented Location form and reordered query parameters already pass our validator; no incompatibility with a documented form was found. Origin remains exact HTTPS `www.googleapis.com`, path exact `/upload/drive/v3/files`, exactly one uploadType=resumable and one bounded upload_id containing only letters/digits/underscore/hyphen. Unknown query keys, alternative origins/ports, fragments, duplicate parameters, wrong paths or formats stop. Control/space characters are additionally refused before Python URL normalization. We have not broadened hosts, paths or opaque token formats based on a hypothesis.

Tests verify initiation200 and final200/201, reject initiation201 and missing/invalid Location, check query ordering/duplicates and adversarial origin/path forms. This is protocol contract evidence, not proof of a specific response received during the historical failure.

## Execution contract

Branch `test/drive-copy-same-id-recovery-20261001`; manual dispatch only, reviewed SHA/checkout/workflow identity, owner actor and triggering actor, attempt1, baseline main `1440920191e1778fb9a9ba24e6701937a1a7459c`, new operation `DRIVE_SAME_ID_CIPHERTEXT_RECOVERY_V1` and approval `DRIVE_SAME_ID_CIPHERTEXT_RECOVERY_V1:<review SHA>:one-create-approved`. Fresh deadline (typically dispatch time plus 240 seconds) never later than job start plus300. Job five minutes including SDK setup, inherited runtime deadline/caps. Existing OAuth plus five OCI credentials only; no environment or new grants. Neither prior copy approval nor readonly approval matches this gate.

1. Refresh existing OAuth without scopes, verify identity, write scope and exact owner-only destination/parent. GET the exact allocated ID. If it exists, perform metadata/ACL/readback/hash verification only and return EXISTING_FILE_READONLY; never modify an existing object.
2. Only exact files.get404 permits continued consideration. Other HTTP errors or a mismatched ID stop. Read and verify fixed OCI ciphertext, recheck main/destination privacy, then repeat exact ID GET to catch a newly appeared file.
3. Flush/fsync UNKNOWN intent for the SAME ID before mutation. One initiation POST, at most one full-content PUT; counters prevent repeating either transport call. A 409 at either stage stops as CONFLICT_STOP, with no overwrite, automatic retry, or new ID. It does not retroactively prove the old upload's outcome.
4. Require exact file metadata, owner-only ACL, independent bounded Drive download and matching hash; repeat privacy/main checks. Only then RECOVERED_VERIFIED / second_copy_verified=true.

Privacy reads are not atomic locks against external changes. Same-ID API conflict behavior bounds duplicate creation. Hard runner loss can still leave outcome unknown; no implementation can guarantee a terminal remote outcome after lost transport. Ciphertext stays in process memory, no artifacts/files/core dumps; process exit is not a secure memory-erasure promise.

## Safe diagnostics and stop rules

Fixed create_stage (LOCAL_CHECK, INIT_POST, SESSION_VALIDATION, CONTENT_PUT, FINAL_REPLY, COMPLETE), numeric HTTP status, fixed failure_class, initiation_accepted/session_validated/put_attempted/final_response_accepted booleans, and fixed session_validation_failure distinguish failure boundaries. No session URL, raw headers/bodies/exception text, credential values or hashes are output. put_attempted indicates entering that request path, not evidence bytes reached Google. create_outcome remains conservatively UNKNOWN on ambiguous transport/reply failures. The final receipt retains exact file ID/source hash for reconciliation.

On any conflict/failure/unknown, stop and report the sanitized receipt. Further mutations need a new assessment and approval; no loop of attempts is authorized. Existing OCI copy is preserved. No PR/main/schedule/secret changes; publication is not execution. Independent I2 and offline tests precede publication.
