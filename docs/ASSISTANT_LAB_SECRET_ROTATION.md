# Assistant Lab credential rotation

## Scope

This policy covers the two long-lived credentials used by the Oracle Assistant Lab control path:

- the dedicated Neon login principal used by Assistant Lab workers;
- the localhost Control API bearer token.

Administrative database credentials and OCI account credentials must not be installed into Assistant Lab services.

## Storage rules

- Keep one canonical Neon credential source on the Oracle host: `/opt/bridge-school/assistant-lab/assistant-lab.env`.
- Do not copy the Neon connection string into Observer or Control Bridge directories.
- Keep the Control API token only in `/opt/bridge-school/assistant-lab-observer/control.env`.
- Credential files must be root-owned and mode `0600` or `0640`.
- Never print credential values to CI, journal, smoke-test, or experiment logs.

## Rotation procedure

1. Create/rotate the narrow dedicated credential at its provider.
2. Replace only the canonical host credential file.
3. Restart the services that consume that credential.
4. Run a bounded health/no-op verification.
5. Verify the old credential no longer authenticates.
6. Record only rotation metadata (time, principal, verifier, outcome), never the secret value.

## Emergency rotation

Rotate immediately after suspected disclosure, accidental logging, unexpected principal use, or a privilege-boundary failure. Disable the affected control path until the bounded principal and service health checks pass again.
