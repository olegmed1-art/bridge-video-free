# Synchronous private-pipe checkpoint transport

2026-09-26, ASSURED / I2, tracking #1946. Callable transport boundary only;
no live SSH assembly, credentials, operator approval, grants or pilot.

The Oracle host owns its private journals and `JournalCheckpoint`. The runner
owns the actual `OCIJournalStore` and OCI credentials. `ProxyStore` exposes only
the existing five checkpoint-store methods over an authenticated private duplex
pipe. `StoreServer` invokes the real store synchronously and acknowledges only
after the operation and its post-operation guard finish. ETags and conditional
head semantics remain unchanged. Neither side retries or repairs an uncertain
operation. The journal protocol, rather than an RPC ACK alone, still requires
archive readback and confirmed head before the host can proceed.

Both endpoints must be assembled by trusted code with the same independently
accepted source/run binding and exact checkpoint scope. Those hashes are not
self-authenticating, and a supplied callback is not operator coordination. The
server guard must verify the actual source, run, lifetime and accepted scope;
the real store retains its own immediate mutation guard. No generic URL, shell,
object path, deletion or workflow mutation can be requested through this API.

Frames are canonical JSON with unique keys, strict base64, fixed identity and
monotone sequence numbers. A four-byte bounded length precedes each frame. The
channel owns exclusive use of the caller's two nonblocking pipe descriptors and
uses one absolute deadline of at most 60 seconds. It never reconnects or resets
the deadline. Pipe timeouts do not supervise an in-flight provider call: the
independent PID1 lifetime and a bounded runner remain assembly requirements.
The caller must close the transport after failure; no exception text/error
payload is sent to the other endpoint. Any malformed reply, uncertain write,
expired deadline or failed guard poisons the endpoint. A host waiting for a lost
reply must stop without SQL replay or automatic workflow restoration.

Tests use actual local duplex OS pipes and explicitly simulated object storage.
They cover complete journal publication/readback, a lost reply after the store
advances its head, retained intent, failed CAS, source/scope/sequence/method
mismatch, corrupt frames, failed post-operation guard and permanent refusal.
They do not establish authenticated SSH/PID1 integration, off-host durability,
production writer exclusion or realistic whole-executor network latency.

Next assembly must retain current source/manifest assets, connect these endpoints
through the reviewed interactive SSH and supervisor, and measure the complete
path within the existing run and route deadlines before any permission change.
Rollback is code revert before activation. Preserve journals and remote objects
after any attempted operation; transport errors cannot classify its outcome.
