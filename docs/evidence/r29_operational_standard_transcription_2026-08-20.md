# r29 standard transcription integration evidence — 2026-08-20

Status: OPERATIONAL_STANDARD_PATH

Validated identity overlay revision: `3.1-free-r29`

Validated heavy transcription / speaker-separation revision: `3.1-free-r25.15`

Implementation:
- integration PR: `#177`
- integration merge commit: `13718ee9be478ff07c2f05f24bde945cc238089e`
- standard production workflow remains `bridge-video-3.1-free`
- r29 is invoked automatically by the ordinary output-routing step after a validated r25.15 transcript generation; it is no longer dependent on the separate Diana-2-only field workflow

Standard-path validation:
- job id: `41daa4ca6e09d13e366c578b7c53ae31`
- validation request commit: `cd4996cfe871acbd9450e7e0cf48d6e5da2c09fc`
- GitHub Actions run: `32306408596`
- run conclusion: success
- terminal preflight found the existing validated r25.15 generation, so heavyweight runtime installation, FREE-GUARD, video processing, ASR and fresh visual analysis were all skipped
- `Completed job no-op`: success
- `Route derived outputs away from master media`: success
- final monitor receipt: success
- source master remained read-only

Integrated r29 result:
- receipt status: `SPEAKER_MAPPING_OPERATIONAL`
- r29 receipt Drive id: `15uiBn9vZeysRm6e4c3oeS1daubyVCtHm`
- durable `speaker_map.json` Drive id: `1d0ZvG53XLDVcyVyGhdHjdfhI6WLxaqzp`
- speaker-map digest: `1c3d2fd18248a3b8`
- source master PDF Drive id: `1LjT7t-fBmeGXn7UwDqftYbh-xKDGKC3i`
- acoustic clusters: 2
- private identity anchors checked: 16
- speaker coverage by speech duration: 0.9378
- participant coverage by speech duration: 0.9378
- unknown duration: 442.3 s; it remains unassigned rather than being forced to a participant
- conflict duration: 0.0 s
- failure codes: none

Identity boundary and discovery:
- private evidence documents were resolved only after their contents matched the exact `job_id` and source identity; filename fragments are discovery hints and are not identity evidence
- anonymous acoustic cluster id, private participant reference and semantic role remain separate fields
- semantic role, filename, speaking duration and invitation membership cannot create named identity
- if matching private identity evidence is absent, standard transcription remains valid with anonymous speaker labels and person-specific attribution/writes remain blocked
- partial explicit private configuration fails closed
- a genuine r29 Evidence Gate failure blocks named attribution but does not erase a valid anonymous transcript

Privacy / cost / authority:
- heavy video reprocessing during integration validation: false
- ASR reprocessing during integration validation: false
- visual reprocessing during integration validation: false
- paid API calls: 0
- paid cloud: 0
- real-person names in public evidence/logs: false
- speaker embeddings persisted: false
- temporary audio anchors persisted: false
- canon write performed: false
- curriculum write performed: false
- student-profile write performed: false
- Neon write performed: false

Regression gate:
- PR #177 Production Evidence Contract: success (`32306083555`)
- production r25.15 evidence/policy tests: pass
- speaker diarization v2/v3 regressions: pass
- r29 mapping regressions: pass
- integrated identity-postprocess and standard-pipeline contracts: pass

Conclusion: the ordinary `расшифровка видео` production route now preserves r25.15 as the heavy transcription/speaker-separation layer and automatically applies the operational r29 identity overlay after output routing. When independent private evidence is available and passes the r29 Evidence Gate, a durable speaker map is produced; otherwise the transcript remains safely anonymous. The standard-path validation above completed successfully without heavy reprocessing, privacy weakening, paid fallback or authority writes.