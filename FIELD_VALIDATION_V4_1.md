# Longitudinal v4.1 field validation contract

This candidate is a semantic-only hardening layer over the already completed lesson master.

Required gates before merge:
- v3/v4/v4.1 regression suites pass;
- weak count questions without an actual question mark do not create a decision window;
- short strong bridge questions may survive missing punctuation only when not self-answered;
- task and student action must share bridge context, except compact pure numeric answers to explicit count questions;
- nested prompts sharing the same teacher-intervention/follow-up core are deduplicated;
- every new complete interaction uses explicit acoustic speaker + explicit role evidence and at least four source segment references;
- follow-up never proves bridge correctness by itself;
- source media remains read-only; raw ASR is not mutated; no heavy media rerun is allowed;
- paid AI/cloud remain zero;
- canon, curriculum, methodology activation and production student-profile writes remain denied;
- field validation writes only a bounded aggregate receipt to the existing private work folder; no transcript or names are emitted to public CI artifacts/logs.
