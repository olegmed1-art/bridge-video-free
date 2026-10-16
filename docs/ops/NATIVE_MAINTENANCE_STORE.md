# Native maintenance journal store preparation

2026-09-26. ASSURED, independent I2 review before merge and host execution.

The executor needs journals that survive an Actions runner or controller exit.
The dedicated dispatch prepares only `/var/lib/bridge-native-maintenance` on
Light, under unchanged live HOLD observations before and after. It does not
change the worker service, DB permissions, credentials, workflow states or tasks.
The existing HOLD attestation performs read-only database queries.

The root-owned parent chain, directory0700, files0600, no-link checks, exclusive
store lock and fsynced version marker are verified. Only ext4/xfs/btrfs mounted
read-write on the same device as `/var/lib` are accepted. Existing state is never
chmodded, overwritten or swept. Operation directories, when present, are retained.

A separate process writes and closes an actual Journal; preparation reopens it,
copies the record bytes into a second private journal, verifies restoration and
independent append, then removes only that invocation's successful synthetic
probe. A failure retains remnants and blocks retry pending reconciliation.
This proves local journal mechanics, not an independent backup or survival of
volume/instance loss. Storage attachment and independent backup remain deployment
requirements before live permission operations. No operation scope is approved
and no production operation journal is synthesized by preparation.

The separate runner uses the exact Git source bundle, a fresh current-main check,
pinned Light SSH host key and the existing PID1 supervisor with a100-second cap.
No DB owner credential is transported. It does not expand the read-only transport
command surface. Only fixed success fields leave the host; exceptions and private
HOLD identity are not published. Supervisor expiry/cancellation can leave partial
storage; there is no automatic rollback or deletion on an uncertain result.

Rollback: revert the code to prevent future preparation. Preserve any live store
until its version, entries and absence of operational journals are independently
confirmed. Never delete it merely because a workflow failed or a client timed out.
