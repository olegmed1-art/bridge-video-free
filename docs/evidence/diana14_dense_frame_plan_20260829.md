# Diana 14 dense-frame SHADOW plan

Date: 2026-08-29  
Change ID: `UV-3.1-TEST-DENSE-FRAMES-20260829`

## Decision

The `bridge_lesson_3_1_test` r5 path removes the two barriers that produced the
59-frame Diana 14 artifact:

- the minimum fixed interval is reduced from 15 seconds to 1 second;
- technical result conformance has no implicit frame-count ceiling (formerly
  300 frames).

The isolated Diana 14 repeat is pinned to a 3-second interval. For the attested
duration of 6,950.8 seconds the independent schedule is:

- time zero;
- every 3 seconds while the timestamp is below the media duration;
- the final frame at 6,950.3 seconds.

Expected total: **2,318 frames**.

## Preserved boundaries

- `result_scope=SHADOW_ONLY`;
- `canonical_promotion_allowed=false`;
- `production_activation_allowed=false`;
- no automatic next-video launch;
- no raw media publication;
- result size remains bounded (2 GiB for technical conformance);
- every frame is ordered by media time and hash-bound with SHA-256;
- a missing/empty frame or a schedule/count mismatch fails the run.

The Drive compact-publication contour keeps its separate publication limits;
this change permits dense server-side test evidence and does not activate a
production publication route.

## Verification and rollback

- Unit schedule: 6,950.8 seconds / 3-second interval => 2,318 frames.
- Regression proves that 301 frames pass default technical conformance while an
  explicitly requested `max_frames=300` check still fails closed.
- Independent verifier reconstructs the expected timestamps from duration and
  interval instead of trusting the runner's declared count.
- Rollback: revert `UV-3.1-TEST-DENSE-FRAMES-20260829` and restore the isolated
  Diana 14 job to its preceding revision. Existing r4 evidence remains immutable.
