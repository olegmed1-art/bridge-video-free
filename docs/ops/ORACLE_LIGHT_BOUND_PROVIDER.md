# Dormant target-bound Light provider

2026-09-25 UTC; ASSURED preparation for #1946. Depends on the dormant adapter
draft #1959 at `ffa2aa37ca738f573648f4e968d567a1a2da0c5e`.
This document advances the provider prerequisite listed in that adapter draft;
it grants no deployment, cloud execution, or admission authority.

LightProvider requires an immutable ProviderTarget supplied by the eventual
trusted loader, after independent live environment/repository verification.
Every lookup, submit and collect call must supply the same target. It fixes the
Light binary, state directory, HOME and CODEX_HOME, with a scrubbed five-variable
child environment and forced ChatGPT authentication. It never changes the shared
bridge's global profile and never falls back to ubuntu, an API key or a label.

The bridge journal functions accept explicit state, binding and runner parameters
for this provider. The binding has exactly profile/environment_id/repository.
It is fsynced with SUBMISSION_UNKNOWN and the request before any create call.
Replay requires canonical binding and request equality. Bound/unbound journals
are mutually incompatible; an explicit null binding is not a legacy journal.
Collection verifies the creation journal before returning a cached result.
Bound submission passes the exact target ID to --env. Unbound legacy callers
retain their existing environment label and journal serialization.

Existing unbound journals are not migrated or adopted. Any such file in the
Light state directory blocks the corresponding bound dispatch and requires
operator reconciliation. Never delete it, rewrite its target or resubmit to
resolve ambiguity. Unknown submissions remain quarantined and retain intent.

Focused tests use temporary filesystem journals and mocked subprocesses. They
verify pre-create binding persistence, exact argv and scrubbed environment,
timeout/ambiguous-create no-retry, cached-result freezing and binding rejection,
strict request replay, immutable profile state, and legacy byte compatibility.
They establish no live cloud access or target binding. Existing adapter/bridge/
delivery/queue tests run together with these tests in database CI.

The provider itself has no admission controller: it must be used only behind
LightNativeAdapter and a separately reviewed production loader. There is no
entrypoint, scheduler, task allocation, service change, DB grant or activation
in this change. Syntax validation of an ID is not proof of its identity.

Before promotion: verify live environment ID/repository access and production
owner-level DB definitions/ACL/config, integrate a serialized one-item loader,
rehearse it, review exact release/rollback, freshly attest HOLD and empty queue,
and obtain the separate bounded pilot launch decision.

Before use, rollback is reverting these code/CI additions. After a bound journal
exists, legacy code deliberately rejects it; rollback must stop new admission
and retain/reconcile the original provider task, not downgrade or erase its
journal. Production remains untouched during preparation.
