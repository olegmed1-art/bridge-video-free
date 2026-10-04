# Pilot source integration checkpoint

This candidate integrates the disposable resident/recovery portion of PR #2088
(head 2c7445aecdfe7e657797fbc0ccc63bf89920a8b4) onto main
f513afc4a1a4cae325b7baeeef5f4848dd29a7a5. It does not merge or promote main.

The qualified owner workflow, owner_probe, resident_preflight, owner tests and
installation manifest/checks remain unchanged. The accepted live owner inventory
(run 37235562804) is not repeated and is not teacher/pilot write evidence.
The old resident checker is not copied over the hardened installed checker.

## Executable evidence

The isolated ten-minute PostgreSQL 18 service applies unchanged migrations.
Only this disposable fixture makes the already existing app principal LOGIN.
The new behavior checker uses an actual app-principal login, never owner SET ROLE.
Owner executes the missing-rule gate; app execution of that internal gate must
be denied under unchanged ACLs. App executes the public absent-school catalog under
READ ONLY, pinned pg_catalog and forced rollback. Privilege inspection confirms
necessary teacher reads and absence of owned activation revoke UPDATE privileges.
These checks are necessary behavior, not activated-rule acceptance.

The staged existing compiler then exercises the authenticated teacher HTTP route:
absent position 404; baseline ABSTAIN; 3H SUPPORTED and 3S CONTRADICTED;
owned revoke ABSTAIN; reactivation with the same original expiry restores both
assessments. Failure after activation commits the exact owned emergency revoke,
then HTTP returns ABSTAIN. Emergency repeats remain at 42 rows. Normal lifecycle
is 40 rows; the extra disposable school is outside that pilot count.
No teacher-output, search-run or final-decision writes occur.

Only two approved shape meanings and TDEC-20261003-002 are included.
Sources, package, SQL compiler, activation/schema gates and L1 remain unchanged.
Points, HCP, bid priority and unresolved decisions13/14 are not inferred.

## Remaining live sequence, before any pilot write

1. Coordinate the exact draft source/CI/I2 with the parent before main promotion.
   Reconcile current main, actual deployed SHA and immutable recovery plan.
2. Qualify existing authenticated app behavior separately from the accepted owner
   inventory. Use the existing application credential contour in place, without
   copying a credential into GitHub or this agent. Review the exact deployed
   routine definitions/dependencies and target identity before executing even the
   fixed absent-ID functions: READ ONLY is not proof against external side effects
   of an unreviewed function. The new inspect_teacher_connection helper accepts
   an already verified, idle dedicated app login; it neither opens nor retargets
   connections and has no CLI/dispatch. Production caller must verify TLS/target
   before calling it. No owner role substitution, new grant, credential or policy.
3. Establish an independently reachable recovery channel using the existing
   qualified main-only owner runtime, exact reviewed source and deterministic
   owned IDs. The installed runtime currently supports inventory, not pilot DML.
   A fixed bounded stage/recovery adapter and its receipt checks still require
   source integration/review before any dispatch. Do not substitute maintenance.
4. Independently correlate each authenticated request ID to the exact deployed
   SHA before the next stage: absent baseline, source/position baseline,
   initial activation, owned revoke, reactivation, and every waiting poll.
   Failures stop progression and require owned-only revoke plus readback.
   Lost recovery reports emergency_revoke_unproven. TTL is not a revoke receipt.
5. Verify exact final SQL plan, absence/collisions, source/schema gates and total
   budget before writes. Keep one school, one synthetic position, two rules,
   40 normal rows / maximum42 emergency, append-only audit, and original 24-hour
   expiry from first activation. Reactivation cannot extend it.

## Expired HTTP validation window

The original build validator deadline remains 2026-10-04T18:00:00Z and has elapsed.
Old SHA/READY pins are historical and are not refreshed in this integration.
The preserved refusal is tested before token lookup, claim creation or HTTP.
The prior pilot authorization is recorded; this checkpoint does not reopen that
expired execution window or request a new permission. Parent must reconcile the
temporal constraint before choosing a live execution plan. No live pilot, build,
dispatch, grant, secret change or promotion is performed by this branch.
