# Oracle Light native CLI installation gate

Date: 2026-09-25. Status: preparation only; no CLI usable at the two configured paths; authentication unverified.

## Live evidence

- Current main: `4b0fd7398b185957bf9d20048183cffbeb00c1d3`.
- Manual read-only run [36138879102](https://github.com/olegmed1-art/bridge-video-free/actions/runs/36138879102) checked the actual Light host while its service was active with admission `HOLD`. The probe reported `CLI_ABSENT` at the two pinned paths for `ubuntu` and `school-autopilot`; it does not rule out another installation or session elsewhere. It did not install software, change credentials, or dispatch work.
- PR #1938 is draft; its first SQL permit is strictly one-use `READ_ONLY` and remains unissued. The production migration has not been applied.

## Bounded installation contract

1. Recheck exact current main, host identity, active HOLD, service credentials already recovered, no nonterminal queue items, intended UNIX identity, installation directory owner and permissions, Node/runtime availability, network path to the official Codex package, disk space, and rollback path. Stop on mismatch.
2. Select one profile before installation after reviewing its UNIX identity, directory ownership, and access to service secrets. The `school-autopilot` profile shares the production Light service UID and therefore needs a specific credential-exposure review. The current bridge expects its executable at `/opt/bridge-school/school-autopilot/runtime-bin/codex`, HOME at `/opt/bridge-school/school-autopilot/runtime`, and CODEX_HOME at `/opt/bridge-school/school-autopilot/runtime/codex-home`. Do not copy `ubuntu` login material into that profile.
3. Review an exact official Codex CLI package version and integrity against its distribution source. Stage only that package under a restricted new directory owned by the selected profile. Verify the binary identity and version in place; perform an atomic activation of the single executable path only after a separate host/permissions review. Do not run an arbitrary install script through a shell on Light.
4. Run the existing `oracle-light-native-cli-readiness.yml` workflow on the exact current main. `CLI_AUTH_REQUIRED` means an executable exists at the pinned path but the probe could not confirm ChatGPT authentication; it does not prove correct package version, origin, integrity, or a successful installation. Verify those separately. No CLI task, queue reservation, Neon migration, service restart, or admission change is part of the installation step.
5. ChatGPT account login must happen under the same UNIX identity through the official CLI authentication flow. Handle device codes and session files only in the profile's restricted credential storage; never put tokens or codes in GitHub, chat, arguments, or logs. Re-run readiness and require `CLI_AUTH_READY` before any one-shot READ_ONLY proposal.

The official Codex CLI documentation currently describes both standalone and npm installation and separate ChatGPT sign-in: https://learn.chatgpt.com/docs/codex/cli . The production installer, exact version, and live activation require an independently reviewed implementation; this document alone grants no installation or execution authority.

## Rollback and remaining gate

Retain HOLD. If staged installation fails, remove only the new staged package; do not touch the Light service, Neon, or existing login files. If an activated executable fails health, atomically restore the previously observed executable path and re-run readiness. After CLI readiness, the first READ_ONLY E2E still requires the authentic broker dispatch, migration/capability review, exact one-shot permit, and fresh authority/queue checks. REPAIR on PR #1939 requires a distinct authorization and patch-publication gate.
