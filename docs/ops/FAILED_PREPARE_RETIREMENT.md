# Explicit failed-prepare retirement

This protocol retires an intent-only, contained failed prepare without pretending
that execution completed. It is not an ACK repair, retry or automatic incident
closure. It creates no task and never changes admission, services or DB rows.

`retire-prepare` is a version-1 controller action with the exact keys in
`light_native_retirement_live.WRITE_KEYS`: current source/controller/runtime
pins, base64 exact proposal and its accepted SHA-256, the eleven historical
journal content hashes, and an independently accepted operation-bound Agreement.
The action has one possible durable effect: create-only
`issuers/<policy-sha256>/<index>/retire-prepare-v1.json`, root:root 0600.
It uses existing issuer/cycle locks, no-follow/no-atime reads, fixed inventories,
before/after primary verification and protected-byte/inode checks. It does not
provide distributed atomicity or rule out an unseen ABA event.

An identical retained proposal can only be reconciled read-only. Partial,
different or uncertain output remains in place and blocks; never truncate,
overwrite, unlink or complete it automatically. A successful writer result is
`PROPOSAL_RETAINED_UNACCEPTED` or `PROPOSAL_PRESENT_UNACCEPTED`, never execution
success or admission. Observation labels the proposal separately with live
verification false. Old readers fail closed on the additional entry filename.

An independently accepted finite issuer policy version 2 extends version 1 with:

* `retirements`: nonempty explicit references containing exactly old policy
  SHA-256, integer index and proposal SHA-256;
* `retirement_evidence`: map from those old policy hashes to the eleven approved
  original journal hashes. These are content pins, not invented/preapproved inodes.

The new policy cannot reference itself or reintroduce the canceled plan/work.
Its derived prepare version 3 carries an exact issuer policy/index binding.
Direct cycle/standalone prepare cannot bypass unresolved historical issuance or
mint that binding. Before admission and again before preparation, the bounded
consumer rechecks original root records, controls, source/runtime, actual retained
predecessor intake/goal/work/outbox/provider/accepted-terminal evidence. Its DB
control/activity/terminal checks repeat after provider readback. Provider `_collect`
is read-only; no provider submission, caching `collect` or acceptance-file writer
is used by retirement verification.

The anchor sequence remains immutable. Completed later cycles must form the
actual authorized chain, explicitly carry the same retirement reference/content
pins, have all six phase records and match service terminal history. Later normal
admission uses the actual latest predecessor. Unknown descendants, gaps, changed
terminals, canceled work or missing references refuse admission. The old policy's
unissued remainder is canceled, never treated as successfully exhausted.

The dedicated operation supervisor has a 55-second callback budget and bounded
parent cleanup; interruption/uncertain cleanup means UNKNOWN. A guardian owns a
new process group. A worker-only inherited seccomp filter prevents changing the
process group/session and creating/joining namespaces, rejects alternate syscall
ABIs, and returns ENOSYS for clone3 to permit inspected clone fallback. It adds
NO_NEW_PRIVILEGES, never disables it. Unknown architectures or filter installation
failure refuse the action. Syscall constants follow Linux's
[x86-64 table](https://github.com/torvalds/linux/blob/master/arch/x86/entry/syscalls/syscall_64.tbl)
and [generic UAPI table](https://github.com/torvalds/linux/blob/master/include/uapi/asm-generic/unistd.h).
The filter supports x86_64 and aarch64; actual platform acceptance still requires
the corresponding isolated Linux tests. Cleanup certification requires no live
owned group members and confirmed reaping of the guardian. Loss of DB transport
does not prove that an already dispatched read finished; the SQL checks also use
server-side statement/idle timeouts.

## Promotion and rollback

This source is a test candidate, not activation authority. Before promotion,
review the exact tested commit/package, CI evidence and independent I2 report.
Source activation, retirement creation and a genuinely new finite task are
separate decisions. Incident-specific evidence/configuration belongs in a private
review package; public fixtures are synthetic.

Before any retirement creation, refused guards mean no write. After possible
partial/full creation, preserve all bytes and original journals, retain blocked
admission, stop and reconcile read-only. A source revert must be preceded by fresh
reconciliation and must fail closed on a retained proposal; reverting code cannot
undo DB/provider effects or revive canceled work. No universal writer lock,
service stop, table SHARE lock, credential creation or DB migration is required
by this protocol.
