# r25.15 production operational evidence — Diana 2 — 2026-08-19

Status: OPERATIONAL

Validated production revision: `3.1-free-r25.15`

Field generation:
- job id: `41daa4ca6e09d13e366c578b7c53ae31`
- production run: `32260611908`
- isolated output folder: `10t2NTMPJ1u3gYKJ31DPj2kt83BODLJxQ`
- isolated work folder: `1n6_RkoMn9ES1CFe4KUdcjHQdl7YPIiqR`
- master PDF Drive id: `1LjT7t-fBmeGXn7UwDqftYbh-xKDGKC3i`
- master PDF SHA-256: `f429f69a53938923f4c5bbca37fc1fc815dc8eded8eb9e6b4d080d0368ea9ac9`
- embedded `master_analysis.json`: present; SHA-256 `fb7c6292f7386aa04ed9d91a101adbd13470d637c2eae1b71c7a37e871f20987`

Production receipts:
- `AI_DONE`: revision `3.1-free-r25.15`
- `METHODOLOGY_READY`: present
- `CLEANUP_ACK`: present
- `LONGITUDINAL_V2_DONE`: present
- monitor receipts: worker started and run finished

Speaker-separation QC from embedded `master_analysis.json`:
- diarization revision: `bridge-sherpa-onnx-diarization-v3`
- final status: `DIARIZED_ROLE_MAPPED`
- selected hypothesis: `3dspeaker-segment-recluster`
- repair validation: passed
- cluster collapse: `false`; collapse reasons: none
- transcript segments: 980
- speaker-labeled segments: 934; labeled ratio 0.9531
- accepted embedding segments: 932 / 954; ambiguous embeddings: 22
- cluster counts: 308 / 646
- speaker turn counts: 302 / 630
- minor turn ratio: 0.3240
- minor duration ratio: 0.3285
- mean assignment confidence: 0.9794
- speaker transitions: 257
- anonymous role map: cluster 0 -> student; cluster 1 -> teacher

ASR / visual / semantic / methodology:
- speech segments: 980; unreliable transcript segments: 0
- visual pass 1 and pass 2: complete; gap check passed; visual evidence items: 278
- semantic QC: PASS
- technical readiness: TECHNICAL_READY
- content readiness: CONTENT_EXTRACTED
- methodology readiness: METHODOLOGY_READY
- r24 gate: passed with no issues
- canon activation: denied by design
- curriculum activation: denied by design
- student profile write: denied by design

Privacy / cost guards:
- named real-person identity claimed by r25.15: false
- raw audio persisted: false
- voice embeddings persisted: false
- cross-lesson voice profile persisted: false
- paid API calls: 0
- paid cloud: 0
- gated model token required: false

Conclusion: r25.15 passed a fresh isolated production field generation with the collapse-repair path selected, no final cluster collapse, strong assignment confidence, preserved privacy/cost/authority guards, and no ASR/visual/semantic/methodology regression. r25.15 is therefore recorded as OPERATIONAL for the production speaker-separation layer. Named participant identity remains outside r25.15 and is controlled by the separate r29 Evidence Gate.
