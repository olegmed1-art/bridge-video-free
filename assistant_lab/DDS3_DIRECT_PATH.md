# DDS3 direct Neon path

## Decision

Interactive and bounded DDS3 compute uses the resident data path:

`ChatGPT -> Neon assistant_lab.job -> Oracle assistant-lab.service -> hot localhost DDS3 -> Neon result -> ChatGPT`

DDS3 jobs MUST NOT be routed through the Assistant Lab Control API or Observer. The Control API remains a separate control/experiment plane for allow-listed Oracle tools; it is not part of the DDS3 compute data path.

## Why

- Neon owns durable queue state, idempotency, priority, retries, lease/heartbeat state, result metadata and provenance.
- The resident Oracle worker claims work directly from Neon using `LISTEN/NOTIFY` plus bounded recovery polling.
- DDS3 stays hot and localhost-only on `127.0.0.1:8080/v1/compute`.
- The database stores a logical DDS3 job, never shell/argv/env execution instructions.
- DDS3 result acceptance remains fail-closed: `engine=DDS3`, `fallback_used=false`, and operation provenance must match.

## Separation of planes

### DDS3 compute plane

`Neon -> resident Oracle worker -> localhost DDS3 -> Neon`

This is the preferred path for normal DDS3 position/table work.

### Assistant Lab control plane

`Neon control_command -> resident control bridge -> localhost Control API -> allow-listed observer/tool action`

This path is for bounded tool control and experiments. It must not become a mandatory hop for DDS3 compute.

## Regression guard

Assistant Lab CI verifies that:

1. `assistant_lab.worker` claims `assistant_lab.job` directly;
2. the worker calls the configured localhost DDS3 endpoint directly;
3. `assistant_lab.worker` does not import or reference `control_api` or `control_bridge`;
4. the production systemd worker starts `python -m assistant_lab.worker`;
5. DDS3 endpoint validation remains localhost-only.

Any future architecture change that intentionally alters this path must update this document and the corresponding CI guard explicitly.