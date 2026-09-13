# Bridge AI Hybrid Cloud Worker

The worker is provider-neutral. It polls the protected Bridge School API for queued heavy-search jobs and closes every claimed job explicitly.

Required environment:

- `BRIDGE_API_BASE_URL` — production Bridge School API base URL.
- `BRIDGE_API_TOKEN` — bearer token accepted by the Bridge School API.

Optional engine endpoints:

- `BEN_API_URL` — BEN REST endpoint. The first worker version uses BEN only when configured.
- `PONS_API_URL` — reserved for the Pons adapter; the worker must fail closed until an adapter is implemented and verified.
- `BRIDGE_WORKER_POLL_SECONDS` — idle polling interval, default 5 seconds.
- `BRIDGE_WORKER_ONCE` — process at most one claim attempt and exit.

Build:

```sh
docker build -f Dockerfile.ai-worker -t bridge-ai-worker .
```

Run:

```sh
docker run --rm \
  -e BRIDGE_API_BASE_URL \
  -e BRIDGE_API_TOKEN \
  -e BEN_API_URL \
  bridge-ai-worker
```

Safety rule: absence of a verified engine is an error. The worker never fabricates bidding scores, search EV, DDS values, samples, or candidate evaluations.
