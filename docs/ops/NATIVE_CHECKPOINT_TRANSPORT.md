# Synchronous private-pipe checkpoint transport

2026-09-26, ASSURED / I2, tracking #1946. Callable transport plus a fixed synthetic
SSH probe; no production permission assembly, operator approval, grants or pilot.

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

## Fixed supervised live probe

The separate manual owner-only exact-main workflow holds both existing Oracle
workload and backup mutation groups. It passes verified source into an interactive
SSH child under the existing independent PID1 supervisor. The bootstrap reads one
bounded frame, not EOF, leaving stdin/stdout exclusively available for store RPC.
OCI credentials stay on the runner; its SSH environment contains only PATH.

The child checks actual HOLD before and after. Under the already prepared private
host store's existing lock and VERSION it creates one digest-named synthetic
scope with two persistent private journals. It publishes a pristine pair, appends
a SYNTHETIC_INTENT, publishes the successor, downloads it and reconstructs a
separate private copy with byte comparison. All host evidence is retained, even
on failure; a scope directory can never be overwritten. No production operation
journal, database credential or permission session is used.

The runner dispatches only store requests for the precomputed synthetic scope,
with fresh source guards enclosing each request. A terminal frame must bind that
scope/source/session, exact request count, unchanged HOLD and bounded elapsed time.
The runner closes stdin, requires successful SSH/supervisor exit, then independently
downloads the recorded latest head and archive. Public output is fixed status,
hashes, timings and counts. Private request/response bytes and exceptions are not
logged. A failure stops the SSH group; PID1 remains the independent host bound.

Reported transport duration includes SSH startup through the final host frame;
inventory/setup and final independent runner readback are outside that interval.
This deliberately does not claim a complete production RunBinding/pause/SQL
rehearsal. Actual run/job binding, scoped operator/drain implementations, accepted
source/manifests and the full executor's network timing remain deployment work.
Live success must be recorded separately at its exact merged source SHA.
