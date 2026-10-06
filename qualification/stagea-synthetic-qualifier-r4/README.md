# Synthetic-only actual supervisor qualifier

This standalone candidate contains no production dispatch, owner route, provider connector, credential input, production endpoint or operational receipt. Its only worker modes are the exact strings payload and hang with one fixed synthetic wire and runtime marker. None or any other mode is refused before alarm or process creation. The generic definition engine is retained for published schema constants and validation; no database connection or engine collection occurs in this qualifier.

## Actual candidate lifecycle and refactoring

The real supervise function remains the qualification entry. Its streaming exchange, 50-second worker lifetime, READY syscall, live worker identity, activation-based budget, payload validation, independent cleanup and 60-second kernel timer remain in the same body. Only safe-core imports and synthetic-only admission/dispatch differ from the prior mixed adapter. The production dispatch and host/service bindings are removed completely. Control and capture import only the new synthetic core. This candidate does not prove production adapter equivalence or qualify a frozen earlier version.

The runtime manifest is an exact nine-file allowlist: synthetic core, watchdog, control, fixture, capture, generic definition engine, standalone lifetime and two package initializers. Suite, bootstrap and the two newly evaluated test files are additional pinned qualification sources. No driver wheels, SQL migration, incident journals, private test suite, templates, operational receipts or patches belong in this source package.

## Four planned real Linux/PID1 scenarios

1. Fixed synthetic payload; exact returned byte count and SHA256 match the fixture payload.
2. SIGKILL of verified actual supervisor MainPID while the fixed hang worker and setsid descendant are live.
3. SIGSTOP of the actual worker-identity systemctl child in its verified candidate control cgroup, then supervisor SIGKILL.
4. SIGSTOP of the actual systemd-run child after the actual READY syscall, then supervisor SIGKILL.

The suite invokes actual supervise; it does not mock lifecycle outcomes. Fault hooks wrap original Popen/read calls, record PID/start-time/cgroup and use pidfds. Each stopped client's real T state and control cgroup must be proven before supervisor death. Candidate PASS requires natural control/worker drain and absence of recorded clients by control activation+62 before external stop/reset/kill. External containment, runner disposal or emergency cleanup can never grant candidate PASS. Failed arming, late activation, unknown identity or unproven cleanup fails rather than skips.

## Exact proposed privileged scope

Current status: PRIVATE DISABLED DRAFT, no source push, workflow publication, root execution or CI launch. Separate privileged-scope review and owner authorization must precede those actions.

Runner: standard GitHub-hosted ubuntu-24.04, public repository, contents:read, job cap20 minutes. Visibility and free standard-runner eligibility must be read afresh before dispatch. No package installation, paid runner, Docker, cache, artifacts upload, secrets or third-party actions. Workflow dispatch needs registration; this preparation does not authorize default-branch or other publication changes.

Source: fixed separately reviewed source commit, with all source bytes checked against a fixed manifest before selected Python imports. Portable tests run only verified file copies. Root runs a trusted inline stdlib verifier through sudo -n env -i PATH=/usr/bin:/bin GITHUB_ACTIONS=true timeout --signal=KILL900s python3 -I -B -S -. It checks the full source manifest again and executes only verified bootstrap bytes in memory. No checkout script executes through sudo before verification. The bootstrap pins and copies only nine runtime files plus suite into root-private staging.

Filesystem: create only NEW root-owned /run/bridge-stagea-ci-random directories0700, source files0444 and evidence directory0700. Synthetic bounded metadata is written only beneath evidence. No chmod/chown or permission changes to existing files; no account, SSH or persistent unit changes. Read-only source mounts, with evidence-only writable exception.

PID1: one new random outer containment service RuntimeMax300s; four new random candidate control services RuntimeMax60s; four new random workers RuntimeMax50s. Outer/control use Typeexec and ExitTypecgroup. Worker uses the reviewed standalone ExitTypemain profile. All use KillModecontrol-group, SendSIGKILLyes, finalSIGKILL, Stop2s, no restart, NNPyes and protected cgroups. Outer/control and workers have PrivateNetworkyes, AF_UNIX restriction, empty capability sets, ProtectSystemstrict and read-only source mounts. RuntimeRandomizedExtraSec0 and empty stop hooks are checked for control/outer. Workers use the exact standalone pinned lifetime implementation.

Administration: read /proc identities, PID1/version, cgroupv2 and unit profiles; systemctl show/stop/reset-failed only exact new registered fixture names. Pre-launch control and worker intents are recorded. Emergency cleanup stops producers first, then uses final validated intents and exact source/run/random prefixes. No broad cleanup or journal clearing. pidfd SIGSTOP only verified own administrative children and SIGKILL only verified actual supervisor MainPID. The outer suite enables child subreaping through prctl36 for this new test harness only.

Network: public anonymous exact-source GitHub fetch before root isolation. No DB, provider or other remote transport. The AF_INET socket-constructor rejection proof sends no packet; an allowed socket fails the test. Existing host AF_UNIX D-Bus is used only for registered transient fixture lifecycle.

Bounds: outer300 and root timeout900/job1200 are external harness containment and are not candidate qualification evidence. Candidate natural drain must meet its separate activation+62 deadline before containment.

## Validation and remaining gates

New portable suite:34 unique PASS, zero skips. These results were produced from this synthetic-only source; no previous adapter test count is carried forward. Portable tests use boundary mocks and do not claim kernel, PID1, timing, network namespace or privilege qualification. Root/PID1 scenarios are NOT_RUN. Same-model independent static review does not establish I2 assurance.

A future synthetic PASS does not prove production owner integration, driver/runtime acceptance, definition-capture deadline or memory bounds. The generic engine output cap bounds returned bytes; it does not bound fetchall/JSON memory. Catalog snapshots do not exclude concurrent DDL/ABA. No production readiness or incident reconciliation is claimed.

Rollback: omit this candidate. No production state has changed. For a later approved test, preserve failure metadata, stop/reset only validated registered new fixture units, reap only owned identities and dispose the isolated runner. Do not remove operational journals or invent acknowledgements.
