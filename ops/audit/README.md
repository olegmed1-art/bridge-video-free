# Repository audit reports

STANDARD: advisory tooling setup, not a production security sign-off.

Inventory at 1f0753fd5f0b1aa930f76524bb795bd9ae4c4363 (2026-09-24):
890 tracked Python files, 308 workflows, no JS/TS source or package.json.
No Ruff, Knip, Trivy, CodeQL workflow or Dependabot configuration was present.
Existing secret-gate.yml checks selected filenames/key/password patterns and
remains unchanged. GitHub Secret Protection and push protection were already on.

Owner-authorized settings changes: dependency graph, Dependabot alerts and
security updates enabled. Grouped and general version updates remain off.
CodeQL default setup requested for Python, C/C++ and GitHub Actions using a
standard GitHub runner; confirm successful initial analysis in Security before
claiming coverage. No duplicate CodeQL YAML is added. No ruleset was changed.
No paid plan, AI Scan, Codex Security scan or auto-merge was enabled.

This workflow runs on relevant PRs, manual dispatch, and weekly. Findings are
advisory; tool failures are explicit failures, never silently clean reports.
One standard Ubuntu runner, 15-minute timeout, cancellation of superseded runs,
seven-day report retention, contents:read, no secrets or persistent checkout
credentials. No application dependencies installed, no application execution,
no production connection. Tools do not use model tokens. Public standard-runner
minutes are free under current GitHub terms; artifact storage remains subject
to the account's allowance. Billing limits and plans are unchanged.

Ruff 0.16.8 runs E4/E7/E9/F without fixes or repository-config suppression.
Trivy 0.74.0 release archive is SHA256-pinned to the upstream GitHub asset digest;
it scans repository dependency/configuration files, not deployed images/hosts.
Only vuln/misconfig scanners run: secret values must not be copied into public
report artifacts; GitHub's private security UI handles secret alerts.
Requirements lacking pinned/resolved transitive dependencies limit coverage.
Knip is explicitly NOT_APPLICABLE until a JS/TS project exists. The inventory
will report CONFIGURATION_REQUIRED if one appears; this is not a clean Knip scan.

Local usage (install the same pinned scanners first):

```sh
python ops/audit/collect.py
```

Initial baseline: Ruff 1306 findings, mostly E701/E702/E402. Nine F821 reports
include injected bundle variables in administrative script generators; validate
the generated program before classifying these as runtime bugs. Trivy found
seven advisory matches in broker uv.lock (cryptography 46.0.0: four HIGH, two
MEDIUM, one LOW), and twelve Dockerfile findings (five HIGH root-user defaults,
one MEDIUM latest tag, six LOW missing HEALTHCHECK). These are scanner findings,
not proof of exploitability or deployed-version identity.

Triage order:
1. P1 dependency review: cryptography advisories, actual broker deployment and
   code-path reachability. Coordinate with the Light dispatcher before changes.
2. P1/P2 container review: determine actual runtime UID/capabilities before
   changing USER; preserve writable volumes and required privileges.
3. P2 Python correctness: validate F821/F405, then unused assignments/imports.
4. P3 formatting/dead-code cleanup after callers, workflow entry points and
   recovery scripts are mapped. No deletion based only on static reports.

Each confirmed finding needs an owner, reproducer/evidence, narrow PR, regression
check, rollback and deployed readback where applicable. Suppressions require a
specific reason; do not blanket-ignore the initial backlog. Keep Light recovery,
database relocation and this tooling PR separate.

Rollback: revert the workflow/collector PR to remove its triggers. Settings are
separate: disable CodeQL default setup / Dependabot security updates if rollback
is required; do not disable pre-existing secret protection. No data restoration
is needed because the audit performs no production writes.
