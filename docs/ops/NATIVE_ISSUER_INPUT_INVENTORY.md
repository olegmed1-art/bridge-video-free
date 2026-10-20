# Read-only inventory of retained Native issuer inputs

This action observes an original failed issuer entry when the earlier private
reference pins are unavailable. It does not retire a policy, acknowledge an
execution, issue another task, alter admission, or change any service or DB row.
The existing retirement and production gates remain authoritative.

Use the existing `light-native-retirement-observe.yml` owner workflow with a
canonical `inventory-issuer-reference`. Its exact source, controller/runtime
package hashes, old policy hash/index and independently accepted finite Agreement
must be bound to the specific observation. The Agreement operation is
`READ_ONLY_INVENTORY_RETAINED_ISSUER_INPUTS`; its evidence label is
`OWNER_ACCEPTED_READ_ONLY_ISSUER_INPUT_INVENTORY`. Generating hashes does not
provide that acceptance or renew an expired window.

The inventory branch uses the existing pinned SSH host and authenticated owner
run, but receives no database credential or database driver. The root helper is
temporary, source pinned and bounded by the existing operation supervisor. It
uses existing shared observation locks and root-owned, no-follow/no-atime
Snapshot reads. Historical source is data; it is not imported or executed.

The policy and embedded plan bind the selected fixed paths. Eleven whole-record
content hashes and a proposed-record hash are returned, without raw journals,
work identifiers, environment values or extracted credential fingerprints. A
fresh read of the actual proposal distinguishes ABSENT, EXACT and CONFLICT.
Partial/conflicting/oversized records are preserved; they are never overwritten
or interpreted as absence. Snapshot drift refuses the observation.

`history_verified`, `controls_verified`, `incident_closed`,
`execution_acknowledged` and `new_task_authorized` remain strictly false.
OBSERVED means only that the bounded input inventory was read. These observed
pins require subsequent independent review; they are not accepted retirement
authority. Full historical, live DB/provider, service-continuity and admission
verification remain separate obligations. A refused or unknown run is not retried
or promoted to success.

The result is checked on both sides of SSH for exact keys, types, selector/hash
bindings, proposal-state consistency and size. Only fixed refusal codes cross
the error boundary. The action has no arbitrary command or file-path input.

Targeted stdlib tests cover pin/selector drift, partial proposals, later/gapped
entries, private-data egress, authority confusion and capability separation. The
real protected-root Snapshot integration must run in Linux CI; a platform skip
does not establish a live or production result. Rollback before use is a source
revert. After any observation, retained incident evidence is unchanged.
