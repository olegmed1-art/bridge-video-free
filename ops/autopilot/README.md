# School Autopilot Controller v1 — implementation map

Status: design only; runtime is not activated.

Canonical design: `docs/architecture/SCHOOL_AUTOPILOT_CONTROLLER_V1.md`.

Planned implementation order:

1. threat model and data classification;
2. Neon `autopilot` schema migration and least-privilege role;
3. isolated Vercel project under `apps/autopilot-controller/`;
4. durable workflow state store and signed callback ingress;
5. synthetic `AUTOPILOT_SMOKE_V1` GitHub workflow;
6. duplicate/retry/redeploy/timeout/budget acceptance;
7. `RECOVERY_SHADOW_V1` real shadow pilot;
8. bounded GitHub writes;
9. auto-merge only after proven `main` protection and observation window.

Safety invariants:

- no arbitrary shell;
- no canon or methodology changes;
- no large media through Vercel;
- no direct OCI credential in Vercel v1;
- no model for deterministic waiting/routing;
- no automatic merge before branch protection;
- every transition and external effect is idempotent and evidence-backed;
- `AUTOPILOT_ENABLED=false` is the primary kill switch.
