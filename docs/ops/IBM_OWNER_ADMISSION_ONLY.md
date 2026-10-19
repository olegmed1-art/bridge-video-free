# IBM owner admission before ARM

The authenticated owner flow needs the actual retained ACK and the SHA-256 of
the actual handoff request. Returning only the proof hash loses that linkage.
The public receipt helper and driver support a separately reviewed private
owner overlay which preserves those values and exits before token acquisition,
client creation, ARM or provider power actions in `admit_only` mode.

The exact target remains `bridge-school-compute-ibm`, instance
`02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2`, zone `eu-de-2`.

## Receipt contract

1. Create and fsync an intent for the full instance/run/nonce/source/runtime
   binding and request/proof/window hashes. Existing attempts are refused.
2. Match the actual request and full typed binding to that intent before
   committing the single dispatch marker and calling the existing transport.
3. Validate the actual bounded SSH ACK. Retain its original bytes, wire hash,
   parsed fields and authenticated request hash; fsync and read them back.
4. Use a separate authenticated SSH read to inspect the retained proof/window
   records and reapply their pinned validators. This read cannot call receive.
5. Commit the readback, reopen all receipt files through the pinned directory,
   check exact bytes and metadata, and check freshness before success.

Directory replacement, ownership or permission drift, unsafe leaf metadata,
receipt loss, stale evidence and ambiguous I/O fail closed. No receive retry or
overwrite follows an unknown outcome. A lost runner can leave an unknown
admission; the helper does not perform power cleanup or establish a watchdog.

The three execution modes are mutually exclusive. Collect-only keeps its
verify-only behavior. A dedicated admit-only manifest must have
`admit_only=true`, `source_only=true`, `final_binding=false`, `collect_only=false`.
It is refused by ordinary reviewed runtime. The supervisor rejects ARM status
in admit-only mode and rejects an admit-only terminal frame in other modes.

Only intent, dispatch, ACK and readback metadata are retained as a seven-day
workflow artifact. A successful terminal frame has `armed=false`,
`power_post_attempted=false`, `live_go=false`. It does not prove historical
completion, genuine capture authenticity or autonomous production readiness.

## Integration and qualification

The private overlay, private source diff and its independent review remain in
the authorized local review workspace. They are not part of this public PR.
The old archive cannot qualify the changed owner code pins. A full reviewed
successor archive must reconcile root and runtime owner identities, runtime
manifest, installed entry, readback expectations, disabled workflow and outer
manifest without weakening their validators. No package is deployed by this
change, and no historical UNKNOWN record is rewritten.

Before a live admit-only invocation, the owner must have a fresh authentic
closed-window capture, held Start sources, a fresh successful independent Stop
contract and original primary history. Synthetic fixtures, saved heartbeat
metadata and a recomputed ACK are not substitutes. The local handoff report
lists the exact missing package closure and real capture sources.

After source integration and full private package qualification, the command
shape is:

```sh
gh workflow run ibm-diagnostic-trial.yml \
  --repo olegmed1-art/bridge-video-free --ref main \
  -f expected_main_sha='<fresh-reviewed-main-sha>' \
  -f reviewed_archive_sha='<qualified-successor-archive-sha256>' \
  -f reviewed_manifest_sha='<qualified-successor-manifest-sha256>' \
  -f run_reviewed=false -f collect_only=false -f admit_only=true \
  -f approve_closed_window=true -f approve_stop_route_hold=true \
  -f window_policy_sha256='<reviewed-owner-policy-sha256>' \
  -f independent_stop_run_id='<fresh-successful-contract-run-id>' \
  -f independent_stop_contract_job_id='<that-contract-job-id>'
```

This template has not been executed. Placeholders must come from actual
reviewed sources. The workflow supplies its real run ID; the reviewed package
supplies its nonce.

## Offline checks and rollback

`tests.test_ibm_owner_admission` checks the strict receipt protocol and real
POSIX file persistence. `tests.test_ibm_owner_admission_driver` checks CLI,
owner context, source pin, public fieldset and a local fork/pipe supervisor.
The PR-only Ubuntu workflow runs both without secrets or cloud entrypoints.
Windows skips the POSIX cases; it cannot qualify their filesystem behavior.

Rollback is a source revert of the public change and withdrawal of the unused
private overlay. Retain receipt artifacts and existing unknown records. Do not
remove a partial admission or rerun it as a cleanup action.
