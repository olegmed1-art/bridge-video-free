# r29 identity mapping operational evidence — Diana 2 — 2026-08-19

Status: OPERATIONAL

Validated identity overlay revision: `3.1-free-r29`

Validated source speaker-separation revision: `3.1-free-r25.15`

Field generation:
- job id: `41daa4ca6e09d13e366c578b7c53ae31`
- triggering request commit: `7b7d96ed532a19a95edde60266adcae67475ee9b`
- source master PDF Drive id: `1LjT7t-fBmeGXn7UwDqftYbh-xKDGKC3i`
- isolated output folder: `17lGQwZOeTodZ1m42OO92mLMr4EvoGNiz`
- isolated work folder: `1-mu2neOaNCvR7sGucGk7u2-0dM_vJJ5p`
- private speaker-map Drive id: `1nbfD9rzNdl7wcJMvYFW2SqqSl_tqR27R`
- private receipt Drive id: `1Mwf_zPIPcdM59cznh34Zp7TrVpO2GN66`
- speaker-map digest: `1c3d2fd18248a3b8`

Evidence Gate:
- receipt status: `SPEAKER_MAPPING_OPERATIONAL`
- operational gate: passed
- failure codes: none
- acoustic clusters: 2
- active participant identities confirmed: 2 / 2
- identity anchors checked: 16
- anchors per cluster: 8 / 8
- evidence types for each confirmed cluster: independent visual + acoustic
- mapping confidence: 0.9851 / 0.9851
- visual/acoustic agreement: 1.0
- mapping conflicts: none
- alternatives: none
- participant coverage by speech duration: 0.9378
- speaker coverage by speech duration: 0.9378
- conflict duration: 0.0 s
- unknown duration: 442.3 s; this remains outside participant attribution and does not force an identity

Validated r25.15 source state:
- source cluster collapse detected: false
- source speaker-labeled segments: 934
- source speaker-labeled ratio: 0.9531
- source mean assignment confidence: 0.9794
- source selected acoustic hypothesis: `3dspeaker-segment-recluster`
- source master remained read-only and untouched

Identity boundary:
- public evidence intentionally contains no real-person names
- named identity was produced only from private independent anchors; semantic role, filename, speaking duration, and invitation membership were not used as identity evidence
- anonymous acoustic cluster id, private participant reference, and semantic role remain separate fields
- overlapping/unknown speech is not forced onto a participant

Privacy / cost / authority:
- heavy video reprocessing: false
- ASR reprocessing: false
- visual reprocessing: false
- paid API calls: 0
- paid cloud: 0
- real names logged publicly: false
- speaker embeddings persisted: false
- temporary audio anchors persisted: false
- canon write performed: false
- curriculum write performed: false
- student-profile write performed: false
- Neon write performed: false

Implementation repair evidence:
- the real field route initially exposed a Drive text-decoding defect for UTF-8 JSON with a BOM;
- PR #173 added BOM handling and a regression, but the next real field run proved that decoding `response.text` could still preserve BOM mojibake when Drive omitted a charset;
- PR #174 changed the parser to decode raw Drive response bytes with `utf-8-sig` and added raw-byte BOM/no-BOM regressions;
- the r29 Identity Evidence Contract passed after that repair, and the fresh isolated field generation above then produced the operational speaker map and receipt.

Conclusion: r29 passed the real Diana 2 private identity Evidence Gate on top of the validated r25.15 speaker-separation master. Both active participants are confirmed by independent private visual and acoustic evidence, while the public repository preserves the identity boundary and contains no real-person names. No source media, raw audio anchors, or speaker embeddings were persisted, no paid AI/cloud route was used, and no canon/curriculum/profile/database authority write was performed by the field validation. `3.1-free-r29` is therefore recorded as OPERATIONAL for the identity-mapping overlay.
