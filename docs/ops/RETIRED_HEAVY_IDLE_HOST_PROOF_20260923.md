# Retired heavy Oracle idle host proof — 2026-09-23

Change: retire the pull-request workflow `oracle-idle-host-proof.yml`. It
contacted `158.180.47.161` for every change to the idle classifier or STOP
authorizer, then compared the installed classifier to the candidate. The
address no longer belongs to a running host.

Evidence: Issue #1131 records the heavy instance as `TERMINATED`, its public
IP as absent, and the successful cleanup run 35689962157. PR #1778 merged
the retirement of six heavy-host schedules/controllers. The exact-head run
35899936474 of this workflow failed during SSH host-key discovery, before
it could collect a proof. The Light Oracle is a different host and is not a
drop-in target for this retired workflow.

Decision: remove this PR-triggered host probe. Keep the code-level
`oracle-idle-guard-ci.yml` and historical authorizer, classifier, tests and
issue #627 intact. This change does not authorize STOP on either host or
certify a Light Oracle idle guard. Any future Light lifecycle control needs
its own workload inventory, proof source, exact-host binding and review.

Validation: search of repository references to the workflow; focused idle
guard tests and exact-head CI. Rollback: restore the workflow only after
verifying that its target instance, fingerprint and workloads are current;
never point this historical guard at Light Oracle by changing only an IP.
