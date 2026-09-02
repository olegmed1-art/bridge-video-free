# META School — Deployment Final Evidence

Date: 2026-08-17

## Completed deployment stages

1. Architecture v1.0 -> v1.6: five hardening audits plus subsequent executable-spec audit.
2. Executable specification v2.0 and static dry validation.
3. Shadow calibration Runs 001–010 covering recommendation, no-change, reject, owner boundary, retest, rebase, unknown external state, cost stop, dependency failure and validator independence.
4. Persistent runtime objects physically implemented and exercised on isolated Neon DR branch `br-weathered-silence-b11nrc37` under schema `meta_lab`.
5. Real orchestrated Shadow Run `META-SHADOW-ORCH-001` persisted Run, Contract, Lease, Evidence, Candidate and Decision; lease released; state COMPLETED.
6. Database-enforced Shadow promotion guard tested: attempted PromotionIntent was rejected by trigger; promotion_intent_count remained 0.
7. GitHub separation branches established for lab/candidate/stable/orchestrator/evidence/regression/validator/recovery roles (where connector confirmation available); production/main was not used as experiment sandbox.
8. Runtime orchestrator core implemented on `meta-orchestrator` branch.
9. Regression test suite + read-only GitHub Actions workflow implemented on `meta-regression`; first workflow run 32052319716 completed SUCCESS.

## Errors found during deployment and corrections

- PostgreSQL CHECK constraint attempted a subquery; PostgreSQL forbids this. Transaction rolled back with no partial meta_lab tables. Replaced with a trigger-based Shadow promotion guard and reran successfully.
- GitHub returned intermittent 502 errors on branch/file operations. Applied reconcile-before-retry. A later `Reference already exists` response was treated as evidence the first request succeeded; no destructive retry was used.
- Static scenarios alone were insufficient to prove enforcement. Added physical Neon persistence + trigger test and real GitHub Actions regression execution.

## Current autonomy level

A1 DEPLOYED:
- autonomous/read-only observation is allowed;
- R1 candidate creation and sandbox experimentation are allowed in isolated META areas;
- evidence append and regression testing are allowed;
- Stable/production promotion remains DENIED.

A2 NOT ENABLED:
- no autonomous Stable/production promotion, even for R1, until owner explicitly authorizes that next maturity step after reviewing this deployment.

R2/R3 continue to require independent validation/authorization under policy. R4 canonical bidding/teaching/methodology changes remain owner-controlled.

## Reliability boundary

Production Neon was not schema-migrated for META persistence. The physical runtime persistence currently exists only on the isolated DR/lab branch. This is deliberate: promoting the new schema into production would be a production database migration and is outside Shadow/A1 authority. The deployment therefore proves the META runtime architecture and enforcement without weakening the production safety boundary.

## Final deployment verdict

META CLOSED LOOP infrastructure: DEPLOYED FOR A1 / SHADOW + ISOLATED R1 SANDBOX.
Regression: PASS.
Physical Shadow promotion guard: PASS.
Production writes by META: 0.
Canonical methodology changes by META: 0.

Next gated maturity transition is A2 and requires explicit owner approval; it is not part of autonomous A1 deployment.