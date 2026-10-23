# Limited Operational Pilot — 2026-09-01

- Authorization: school director.
- Scope: up to 3 sequential tasks, one at a time.
- Task set: PR snapshot, CI snapshot, and one draft evidence change.
- Runtime: Oracle resident, `SHADOW_ONLY`.
- Merge and force push: forbidden.
- `main` and production mutation: forbidden.
- Video, TRAIN, routing, and Oracle lifecycle actions: forbidden.
- Model calls and cost cap: 0.
- Stop rule: any non-terminal or failed state prevents the next task.
