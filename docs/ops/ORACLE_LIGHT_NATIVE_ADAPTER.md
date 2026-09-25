# Dormant Light native composition

2026-09-25; ASSURED preparation for #1946. Baseline main:
`e893375c18ee9d372f712cf48f4c599a8d3b8625`.

`oracle_autopilot.light_native_adapter.LightNativeAdapter` composes the existing
NativeQueue, NativeAuthority and delivery.advance for ONE already reserved
request. It has no executable entrypoint, worker import, scheduler, reserve
method, deployment hook, or default provider. It does not activate production.

The constructor requires the exact request, explicit light profile, immutable
ProviderTarget, and trusted RPC, primary GitHub reader, provider, and pilot gate.
It enforces the READ_ONLY/VERIFY restrictions from migration 0333, including
zero cost and repair bounds. A role's can_repair capability is not permission to
repair this dispatch. Canonical JSON comparison preserves boolean/integer type
differences when binding snapshots to the captured request.

The gate receives a copy of that request plus its immutable target. It must
return exactly True only after checking current admission and the approved
one-item pilot scope. HOLD, missing evidence, expiry, target drift and exceptions
deny. It runs before every RPC, GitHub read and provider operation. These are
cooperative checks, not atomic revocation of an already executing operation;
the production loader still needs serialization and database authority fencing.

Provider lookup/submit/collect MUST accept keyword `target`. The provider must
use the verified exact environment ID, fixed Light paths and service identity;
persist profile/environment/repository binding before creation; and reject a
different target on every replay. The existing global codex_cli_bridge module
does not implement this contract and is intentionally incompatible. Syntactic
target validation is not proof of live cloud access or repository binding.

No production loader is supplied. Its remaining obligations are fresh live
environment evidence, fixed service DB identity, current admission plus exact
pilot authorization, serialized execution and target-bound durable provider.
The adapter trusts these ports; it cannot authenticate their implementations.

Validation uses synthetic ports with the real queue/authority/delivery modules:
HOLD before each effect, interruptions around intent and ACK, unchanged task ID,
terminal replay, strict scope rejection, nested type-confusion rejection, and
caller/gate mutation isolation. Existing bridge/delivery/queue regression tests
remain in the same CI step. This is not a real cloud or production rehearsal.

Rollback before deployment: revert this module, its tests and CI additions.
Production state has no dependency on this dormant code. Do not erase receipts
or retry an uncertain submission when a future integration is activated.

Next gates: implement and review the target-bound Light provider; audit live
owner-level DB definitions/ACL/config (enabled remains unverified); verify cloud
environment binding; compose and rehearse the production loader; review exact
release/rollback/admission changes; obtain the separate pilot launch decision.
HOLD remains mandatory until that decision. No grants, tasks, service changes or
HOLD release are authorized by this document.
