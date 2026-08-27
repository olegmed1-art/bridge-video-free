# Database App Runtime Smoke Request

Requested at: 2026-08-27T16:34:00Z

Purpose: trigger the existing `Bridge School Neon App Runtime Smoke` workflow to verify the dedicated least-privilege `BRIDGE_APP_DATABASE_URL` credential and app-facing database privileges without changing production data or deployment state.

Expected evidence:
- workflow: `.github/workflows/database-app-runtime-smoke.yml`
- job: `app-runtime`
- script: `database/runtime_app_preflight.py`
- required result: `RUNTIME_DB_APP: PASS`
