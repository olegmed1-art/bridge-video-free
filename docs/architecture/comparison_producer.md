# Comparison producer and qualified executor

Comparison replay remains disabled in the resident. The producer's direct
runner/Git and replay entrypoints refuse unconditionally until an independently
reviewed executor integration exists. Setting a producer registry environment
variable cannot enable direct resident execution. Existing valid typed packages
are revalidated and reused before review through the normal retained-package
path; ordinary jobs do not enter comparison orchestration.

The separate comparison_sandbox.run_qualified_comparison API is source only.
It first validates the exact producer entry fields and scalar bindings and requires
replay_authorized is True, before qualification reads, OS probes, runner imports
or durable reservations. False, missing and non-boolean authorizations always refuse;
an OS qualification bound to a denied entry cannot grant replay authority.
Its default qualification_file=None refuses before runner imports, host probes
or subprocess launch. A caller must already run as a separately authorized,
non-root host identity and in qualified coordinator mount/network/PID namespaces
different from the resident. The coordinator must be in the independently verified
initial host user namespace: its actual user namespace inode and parsed uid_map
and gid_map must exactly match the protected qualification, with full identity maps
0 -> 0 for 4294967295 IDs. Shifted, split, nested or ambiguous mapping refuses.
The independent OS receipt must verify that inode denotes the initial host user
namespace; numeric namespace-local UID differences alone grant no authority.
executor_uid/executor_gid/resident_uid denote host IDs under this constraint.
This code does not install dependencies, create a
service/identity, grant access, set up cgroups, mount a server workspace or
provide an alternate privileged launch path.

Environment clearing and Python -I do not deny reading files. The current
container service exposes resident secrets at /run/secrets read-only; that
still allows reads. Its CPU/memory/PID limits do not establish a total quota for
raw comparison captures/logs under the ordinary bind-mounted spool. The new API
does not treat those resident boundaries as comparison qualification.

## Reused contract

The executor imports protected_read, private_directory, source_boundaries,
_filesystem and cgroup_boundaries from the published book_runner. These are
pure file/source/OS measurement helpers. No book dispatch, PDF source bindings,
book-runtime authority, observer identity grant or privileged provisioning is
borrowed. The book source checker verifies root-owned immutable source and the
actually loaded source-defined Python code. The executor must start with -I -B.

The published observer wrapper binds host / and shares the network, so it is
not used. Worker sandbox arguments follow the book sandbox's namespace,
capability, read-only mount and process-containment pattern.

## Qualification and actual checks

A root-protected qualification JSON uses schema
comparison-executor-qualification-v1 and exactly the fields in qualify().
It binds the canonical digest of the complete protected producer configuration,
a maximum one-hour freshness interval, distinct host identities, observed
coordinator namespaces/mountinfo, exclusive cgroup path, finite resource limits,
bounded workspace, persistent attempt ledger, pinned bwrap and immutable
runtime/source inventories. A separate root-protected OS-control receipt must
bind the entire execution profile and attest credential/gold/seal/sibling
canary denial, OS network denial, aggregate limits, timeout descendant absence
parent-death containment and denial of all cgroup migration/delegation writes.
The latter must cover coordinator Git helpers and workers, including alternate
cgroupfs aliases and ancestor/sibling destinations; directory snapshots alone
are not an atomic proof when migration is permitted. This implementation never generates such a PASS.

The executor independently re-observes identity, namespaces, mountinfo,
NoNewPrivs, capabilities and existing finite cgroup limits. It requires the
exclusive cgroup to contain only the coordinator before launch. Qualification,
immutable files and OS observations are checked again before returning captures.
Admission pins exact qualification bytes, decoded qualification, evidence bytes and
profile digest. Final qualification must reproduce every pin; even another valid
root-protected profile/evidence replacement refuses before CAPTURED_UNSCORED or
return. This API has no renewal protocol.
Missing/expired/conflicting qualification always refuses; no config boolean is
an OS-isolation proof.

## Worker filesystem, network and resources

Each worker gets an empty mount namespace plus explicit read-only mounts of
curated, complete-inventory runtime trees at /usr,/lib,/lib64; the source-pinned
runner file; that worker's frozen variant checkout and control JSON; and only
video/reference/profile/sprite. Broad host runtime roots, mutable/unlisted files,
symlinks, hardlinks, overlapping mount roles and trees containing gold, seal,
clip receipts, qualification, ledger or the other variant
are refused. The worker receives neither gold, seal, outer manifest, sibling
config/results, resident credential directories nor coordinator ledger.

Only that variant's output and private temp directory are writable. Both reside
on the same already mounted bounded tmpfs as coordinator captures and logs.
Capacity AND total inode bounds are observed, so PNG counters and a later
packager cap are not the aggregate resource boundary. Finite existing cgroup
CPU/memory/swap/PID limits cover the coordinator and all descendants. The code
does not create or enlarge any limit.

Bubblewrap unshares namespaces without --share-net, clears env, creates fresh
/proc and /dev, drops capabilities, disables nested user namespaces and remounts
root/dev read-only. Subprocess stdin is /dev/null; inherited descriptors close;
stdout/stderr must be the exact bounded variant log. Gold omission from a config
alone is not claimed as holdout isolation. The canary controls must exercise
guessed paths, symlink and /proc PID/FD routes against the actual qualified view.

## Attempt budget, termination and retention

A worker-private persistent ext4/xfs ledger reserves one job/config attempt
before runner import or Git/replay subprocesses. Exclusive reservation and
file/directory fsync survive loss of temporary captures; an unknown or failed
attempt never permits automatic repeat. Bounded raw/log/temp evidence is retained
in workspace on failure. Power loss can remove tmpfs captures, while the durable
reservation still blocks replay and requires explicit reconciliation.

The unchanged existing comparison runner prepares seals/configs and captures.
Its worker launch hook is replaced by the qualified sandbox adapter. Git probes
use pinned Git with cleared env/global config and optional locks disabled. Both
Git probes also write stdout/stderr to the same bounded tmpfs and prove subtree
absence after every exit/timeout. Coordinator and worker Git receive a safe.directory allowlist for the exact selected
immutable checkout; no wildcard ownership bypass is permitted.
On exit/timeout the wrapper signals its process group and reaps the CLI, then
independently observes every directory in the exclusive cgroup subtree until only the coordinator
remains. Remaining descendants block continuation; a compact termination receipt
records the observed process set with CGROUP_SUBTREE scope. Unreadable or
changing subtree topology refuses continuation. Namespaces/capability/mount controls prevent
workers from moving themselves outside that cgroup. Runtime qualification must
separately prove parent-death containment for the actual pinned bwrap/runtime.

Successful return means CAPTURED_UNSCORED only. Existing typed package validation,
source/clip/actor provenance, review and publication gates remain separate.
No score, teacher verification, canon promotion or publication authority is
created. Default resident launch is still refused even if this module exists.

## Validation status

Synthetic tests check command/mount construction, scope substitutions, default
and malformed qualification refusal, byte/inode ceilings and cgroup descendant
observation. They do not prove OS credential/gold/network isolation.
All tests are prepared, NOT RUN. Real runtime qualification requires an existing
authorized disposable Linux context with synthetic canaries, finite fixture
budgets and independent measured receipts. No real video job is authorized by
source review, and no service or rights change is part of this package.
