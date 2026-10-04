# Machine queue proof channel

This proposed main-only owner workflow adds a fixed read-only queue observer and a bounded machine responder. Existing canon and maintenance workflows are unchanged.

Only the final guarded owner step receives the existing production credential reference. The existing SSH key reference is injected only for bounded-relay mode. Permissions remain contents:read; public run/jobs endpoints are read without an Actions token grant and fail closed if unavailable. No dispatch, IBM power operation, key installation, grant or listener is implemented.

queue-qualify checks the exact existing owner connection, effective verify-full TLS, session identity, provider GUC metadata, regular heap queue tables, RLS and fixed global zero-count SQL. It does not open Oracle or IBM SSH, approve a repair, establish writer exclusion or reserve jobs.

bounded-relay keeps DB credentials and GitHub token on the runner. It launches only a fixed source-verified Oracle adapter through the existing route. Private bindings, host addresses and approval payloads remain in a private local package on Oracle; dispatch supplies only an opaque independently reviewed package digest. No raw protocol frames, remote results, credentials or bindings are printed to workflow logs.

The local package must contain exactly the reviewed relay and guest bundle, with a private manifest. The adapter waits at most 60 seconds for a separately reviewed local per-run approval and digest. It makes no IBM connection before a matching main/power/run/jobs primary-source check and CHANNEL_ACCEPT. The relay may execute only the reviewed guest allowlist; there is no arbitrary command, URL, SQL or file-path input.

PRE_STOP and POST_STOP retain their original nonce, host, boot, run, scope, fixed SQL digest and server observation timestamp. Queue/source subprocesses are read through bounded real pipes. Each paired proof has a five-second responder budget; both observations must be at most ten seconds old. The guest independently enforces its own monotonic challenge age. A successful proof is a fresh observation and does not block later enqueue.

Runner hard cap160seconds, remote adapter135seconds including approval wait, relay65seconds, guest55seconds remain separate. Actual mutation/rollback and admission/reserve guards live in the immutable reviewed guest package and are not weakened by the responder. Parent must coordinate preparation, Start, operation admission and independent safeStop. The queue responder cannot start IBM, extend a power window or substitute for Stop. On any refusal, disconnect or unknown result, parent Stop is required.

The old private repair package is preserved. Its old main pin cannot authorize execution after this PR merges. The offline successor is a distinct package pinned to the qualification base only; before any live dispatch, independently requalify and pin the merged source and actual host baseline. Never silently substitute the new main hash in an old authorization.

Required before live use: parent review/merge, exact installed private package/approval provenance, actual read-only Assistant Lab visibility, current SSH route qualification, current public run/jobs API availability, synthetic duplex timing and independently armed Stop. Synthetic tests never certify production permissions or network latency.

The permission diff is a new workflow using the existing protected environment and existing credential references in one guarded step; no token permission, environment policy or host access policy is expanded. The new step-specific SSH secret use is explicit and requires review. No private package, host IP, claim or live binding is committed.
