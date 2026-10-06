# Generic Linux scorer-r2 qualification — push-only review v4, NOT_RUN

Publication and execution await coordinator clearance and bounded review of this revision.
This package contains code and synthetic controls only. It contains no real
source identifiers, video, audio, manual real gold, cloud credentials or profiles.
The r2 scorer bytes are unchanged. The public test copy changes only its first
module docstring to remove a private preparation detail; it is NOT byte-identical
to private r2. All remaining test bytes/logic are identical. Its 33 test functions
are expected to collect 68 cases; execution remains NOT_RUN.
Four separate unittest harness guard regressions are added; they do not change
the required 68 scorer cases, their accounting or their JUnit.

Files:
offline_score.py; test_offline_score.py; qualify.py; requirements.lock;
pytest.ini; README.md; MANIFEST.json; test_harness_guards.py; api/ plus eleven pinned
API source files (19 source-root files plus one separate proposed workflow).
A separate proposed GitHub Actions workflow is included in the review bundle;
it must later be placed at .github/workflows/scorer-r2-synthetic.yml.
The entire source root should later be qualification/offline-scorer-r2/.
No active workflow, branch, PR, main or runtime change is made by this preparation.

Required executor:
Linux, CPython 3.12.10, unprivileged execution after entering a new empty network
namespace. The proposed hosted job uses ubuntu-24.04 and exact Python setup.
Dependencies (wheel SHA256 in requirements.lock):
pytest 8.4.1, iniconfig 2.1.0, packaging 25.0, pluggy 1.6.0, pygments 2.20.0.
PyPI metadata/hashes were read; wheels were not installed or imported.
No extras, resolver-selected upgrades, visual backend, FFmpeg, ASR, DDS,
database client, connector or production secret is required.

The eleven API files are byte-identical snapshots from the r2 reviewed dependency
closure at its exact published API pin. They are not replacement/fake implementations.
Runtime must compile all .py files, import each closure module from api/ and verify
the reviewed scorer hash and separately pinned generic public-test-copy hash. This is qualification of that scoring closure;
it is not full repository/package startup or the scorer CLI/git checkout integration.

Resource limits enforced by qualify.py:
address space 512 MiB; CPU 120 seconds; wall alarm 180 seconds; maximum file
16 MiB; 128 open descriptors; 64 processes per UID; zero core dumps.
The outer command adds TERM at 190 seconds and KILL five seconds later.
The proposed entire job is limited to ten minutes. Package installation has a
180-second timeout plus a five-second kill grace; the combined verification,
installation and test step is limited to seven minutes.
Temporary bytes must be <=128 MiB before cleanup. This aggregate disk threshold is
a post-run fail criterion, not a kernel aggregate disk quota; individual files are
capped during execution. No heavy fixture/media is present.
Network is isolated with Linux unshare. setpriv explicitly applies no_new_privs,
drops bounding/inheritable/ambient capabilities and clears supplementary groups
before executing Python under the unprivileged UID/GID. qualify.py checks all
four UID/GID values, empty groups, NoNewPrivs=1 and zero CapInh/CapPrm/CapEff/
CapBnd/CapAmb, both before test imports and before PASS. Missing/unsupported or
nonzero state fails; there is no fallback. See the setpriv and proc status manuals.
Python audit hooks reject sockets/process launch/fork/exec. Every denial increments
a sticky count before raising, retaining at most 16 event names without arguments.
Any nonzero count prevents PASS even if tested code catches the RuntimeError.
Environment is cleared; pytest external plugin auto-loading is disabled.
Pinned installation may fetch only package wheels before entering isolation.

Commands (proposed; not executed here):
0. Trusted inline verifier (embedded in proposed workflow, not loaded from this
   source root) authenticates the out-of-band manifest pin and EVERY listed file,
   including requirements.lock and qualify.py; reject extras/missing/symlink/path
   escapes. Run it before pip and repeat it immediately before harness execution.
1. Create a new venv using exact CPython 3.12.10.
2. venv/bin/python -I -B -m pip --isolated install --disable-pip-version-check
   --no-cache-dir --only-binary=:all: --no-deps --require-hashes -r requirements.lock
3. venv/bin/python -I -B -m pip --isolated check
4. Record parent_netns using readlink /proc/self/ns/net, then execute the command
   below as a non-root user in a fresh isolated namespace. The proposed workflow
   supplies exact absolute paths, a new private results directory and the out-of-band
   MANIFEST.json SHA256. Do not put results/venv under the immutable source root.

sudo -n unshare --net -- setpriv --no-new-privs --bounding-set=-all \
  --inh-caps=-all --ambient-caps=-all --reuid=USER_UID --regid=USER_GID --clear-groups \
  VENV/bin/python -I -B SOURCE/qualify.py --manifest-sha256 EXPECTED_SHA256 \
  --parent-netns PARENT_NETNS --results NEW_RESULTS_DIR

Fail closed on wrong OS/Python/dependency versions; changed/extra/missing/symlink
source files; incorrect manifest/scorer/test pin; syntax/import failure; namespace,
privilege or resource failure; collection !=68 unique cases; any failed/error/skip,
xfail/xpass or warning; incomplete/malformed JUnit; source change during execution;
wall/CPU/memory/file/temporary disk limit; any prohibited external Python I/O.
A PASS requires 68 actual scorer call-phase passes and 68 clean JUnit cases,
plus four executed harness guard regressions. Any presence of report.wasxfail,
including an empty string on non-strict XPASS, is a problem; truthiness is not used.
Receipts always say SYNTHETIC_ONLY, real_video_accuracy_evaluated=false and
promotion_allowed=false. No gold/profile/media/real accuracy or I2 recomputation
is supplied or inferred.

Only synthetic.xml and qualification.json should be retained as CI artifacts.
The proposed workflow uses read-only repository permission, no secrets expression,
no environment/production host, no caches, no self-hosted runner, exact action SHAs,
and disabled persistent checkout credentials. This workflow has no deploy step. Repository-wide integrations and other workflows
must be assessed separately; no zero-side-effects guarantee is asserted.
GitHub-hosted runner provisioning and package setup remain network-enabled;
the test process runs only after namespace isolation and privilege drop.

Status at delivery: source intended, hashes prepared; syntax/import/runtime/test
execution NOT_RUN. A static review of scorer r2 does not qualify this new harness.
Independent review of the harness/workflow and public-scope approval remain pending.

Metadata sources:
https://pypi.org/pypi/pytest/8.4.1/json
https://pypi.org/pypi/iniconfig/2.1.0/json
https://pypi.org/pypi/packaging/25.0/json
https://pypi.org/pypi/pluggy/1.6.0/json
https://pypi.org/pypi/pygments/2.20.0/json

Repair provenance: prior intended qualification bundle SHA256
73c1a43964819d8fde5a302ae67b5439cdfec8aedb35b27f57b754c193e46664.
Only qualify.py, README.md, MANIFEST.json, proposed workflow and the first test
module docstring changed; test_harness_guards.py was added. Scorer, API closure,
requirements.lock and pytest.ini retain exact previous bytes. This v2 harness
has not been independently approved or executed.
https://man7.org/linux/man-pages/man1/setpriv.1.html
https://man7.org/linux/man-pages/man5/proc_pid_status.5.html

Launch revision v4: push only to review/scorer-r2-synthetic-73e54010-20261006.
No pull_request, workflow_dispatch, schedule or other branch/tag trigger is present.
The job requires the exact repository, repository owner, owner actor, push event,
non-deleted exact branch ref and checks git HEAD against github.sha. A connector
actor other than the owner fails closed. The source manifest remains pinned.
The job stays limited to ten minutes; runtime isolation controls are unchanged.
Prior v3 source/harness review was STATIC PASS; this trigger revision needs its
own bounded review. Runtime, imports and all tests remain NOT_RUN.
Predecessor v3 bundle SHA256: 73e540103797854c2ee1a97a7869718a9372d18ddf269edbf7aadf0fc0d3eae2.
