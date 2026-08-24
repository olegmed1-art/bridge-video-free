# School ResearchJob chain

Canonical execution path for heavy bridge-school research work:

`Chat / Research Lab -> Assistant Lab -> Oracle -> DDS3 or BEN -> DB evidence -> checksummed Artifact -> methodical derivative`

## Safety boundaries

- `ResearchJob` is non-canonical by default and cannot promote curriculum or methodology automatically.
- DDS3 evidence is accepted only with `engine=DDS3` and `fallback_used=false`.
- BEN evidence is `POLICY_ONLY`; it never becomes DDS/search evidence.
- Compute jobs are idempotent and bound to the Assistant Lab queue.
- Oracle resident workers may call only localhost DDS3/BEN runtimes.
- Artifacts are SHA-256 bound to compute result plus provenance.
- Methodical derivatives retain the evidence checksum and require school-approved methodology for pedagogical interpretation.

## Executable kinds

- DDS3 -> `DDS3_COMPUTE`
- BEN -> `BEN_COMPUTE`

VIDEO and COMPOSITE retain the common ResearchJob envelope but are delegated to their dedicated bounded pipelines rather than fabricated as compute queue jobs.
