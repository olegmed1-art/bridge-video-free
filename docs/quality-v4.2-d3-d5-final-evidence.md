# quality-v4.2 — D3–D5 final evidence

## Scope

This document records the final public verification of v4.2 report-visual board reconstruction and stable semantic-file idempotency after [PR #230](https://github.com/olegmed1-art/bridge-video-free/pull/230), [PR #231](https://github.com/olegmed1-art/bridge-video-free/pull/231), and [PR #232](https://github.com/olegmed1-art/bridge-video-free/pull/232).

It intentionally omits participant names, private source identifiers, Drive artifact identifiers, private evidence, and lesson content.

## Final field results

| Field lesson | generation_key | Observations | Exact-overlap clusters | Deal candidates | Partial boards | Full boards | Recognized-card union | Preserved v4.1 complete Learning Interactions | Identical repeat reuse |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| D3 | `0b22af04dccd` | 14 | 3 | 3 | 3 | 0 | 30 | 2 | 6/6 semantic artifacts + stable r29 status |
| D4 | `3f9d96c44463` | 13 | 5 | 5 | 5 | 0 | 91 | 9 | 6/6 semantic artifacts + stable r29 status |
| D5 | `a3f64b0271a2` | 21 | 4 | 4 | 4 | 0 | 71 | 3 | 6/6 semantic artifacts + stable r29 status |

All identical repeat passes reused the same six semantic artifact identities and SHA-256 content under exact-name verification. Every semantic artifact returned `already_exists_verified`. No repeat created a new stable r29 status file. Only run-specific monitor/checkpoint audit events were intentionally new.

## Methodology and gates

- Board reconstruction is evidence-only: only visibly supported report-visual cards are parsed. Partial evidence remains partial.
- Clustering uses exact content overlap only. Hidden East/West complement inference is forbidden. Time, topic, and board number are not identity keys.
- A full board still requires the inherited 52-unique-card gate; no field lesson in this verification reached that gate.
- Stable semantic outputs are content-addressed. Stable timestamps are master-derived, and exact-name SHA verification precedes reuse.
- r29 evidence discovery still executes on each run, while an identical stable r29 status artifact is reused.
- Source media remained read-only/untouched. Heavy video and ASR reprocessing remained disabled. Paid AI/API/cloud usage remained zero. Authority/canon/curriculum/student-profile writes remained disabled.
- Historical duplicate/debug artifacts were preserved.

## Production verification

| Stage | GitHub Actions run | Result |
|---|---|---|
| D3 identical repeat after the final timestamp fix | [32480239477](https://github.com/olegmed1-art/bridge-video-free/actions/runs/32480239477) | SUCCESS |
| D4 pass 1 | [32481243826](https://github.com/olegmed1-art/bridge-video-free/actions/runs/32481243826) | SUCCESS |
| D4 identical pass 2 | [32481635219](https://github.com/olegmed1-art/bridge-video-free/actions/runs/32481635219) | SUCCESS |
| D5 pass 1 | [32481952769](https://github.com/olegmed1-art/bridge-video-free/actions/runs/32481952769) | SUCCESS |
| D5 identical pass 2 | [32482271054](https://github.com/olegmed1-art/bridge-video-free/actions/runs/32482271054) | SUCCESS |

The D3–D5 sequence therefore provides terminal production evidence that identical semantic reruns are file-idempotent, including stable r29 status reuse, while retaining intentionally unique per-run audit events.
