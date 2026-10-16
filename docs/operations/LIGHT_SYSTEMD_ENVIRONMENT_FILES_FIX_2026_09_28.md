# Light systemd EnvironmentFiles parsing correction

Status: code reviewed; operational rollout pending. Governance: ASSURED, I2 review accepted.

On 2026-09-28, pilot control run 36420034170 dispatched the supervisor, but both its execution attestation and automatic HOLD restoration refused. Oracle's `systemctl show` returned one `EnvironmentFiles=` row per file. The parser incorrectly treated the second row as an invalid duplicate; existing tests mocked a pre-normalized dictionary and missed the real transport format.

The pilot gate was not released and no native/provider submission was recorded. Incident cleanup 36420710072 restored HOLD using an independently reviewed normalization and the original restore routine. Run 36421847731 restored the exact pre-intake database controls after proving zero submission. These incident actions are historical evidence, not authorization to replay the retained pilot request.

## Change

`light_native_service_switch.show` joins repeated EnvironmentFiles rows in their original order. Duplicate scalar fields still fail. The existing execution attestation remains unchanged: only the exact ordered two-file list, strict ignore-errors flags, command, environment and bounded duration pass. Missing, additional, reordered or altered paths fail before admission or owned-pilot cleanup.

Regression tests feed raw systemctl output through the real parser and attestation, including an owned-pilot restoration path. The existing stage workflow already selects these files and runs the affected suite.

## Validation and limits

- Focused switch/controller/plan/control suite: 90 passed.
- Complete existing stage contract suite: 115 passed locally, including root-only cases.
- Independent I2 review accepted the exact source/test diff; no guard relaxation found.
- Real-host incident cleanup demonstrated the normalization, but this source change is not yet staged or deployed.

A new source commit changes the immutable helper package digest. Existing accepted packages, expired permits, claims and requests must not be modified or replayed. Promotion requires the normal fresh source/package review and reconciliation of the retained task. Rollback is a source revert before staging; already restored host and database controls are unaffected by this code-only change.
