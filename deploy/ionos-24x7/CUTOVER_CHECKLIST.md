# IONOS Cube XL 24/7 cutover checklist

Status: **not activated**.

## A. Account / infrastructure

- [ ] IONOS CLOUD full account activated.
- [ ] Basic Cube XL created in Frankfurt.
- [ ] Actual instance resources verified by `host-preflight.sh`.
- [ ] Static DNS name assigned to compute gateway.
- [ ] Firewall default-deny; only 80/443 and tightly controlled administration allowed.
- [ ] Key-only administration configured.
- [ ] Automatic security updates and time synchronization enabled.

## B. Runtime provenance

- [ ] Gateway image pinned by immutable digest.
- [ ] DDS3 image pinned by immutable digest and provenance recorded.
- [ ] BEN image pinned by immutable digest and provenance recorded.
- [ ] Bridge AI worker image pinned by immutable digest and provenance recorded.
- [ ] No `latest` tag in the production environment file.
- [ ] Runtime secrets installed only in `/srv/bridge/env/production.env`, mode 0600.

## C. Cold deployment

- [ ] `docker compose config` succeeds.
- [ ] DDS3 reaches real `/readyz` with `engine=DDS3` and `fallback_used=false`.
- [ ] BEN starts privately with no public host port.
- [ ] AI worker starts but does not become a second production queue consumer before authorization.
- [ ] Gateway obtains valid HTTPS certificate.
- [ ] Raw DDS3 and BEN ports are unreachable from the public Internet.

## D. Performance acceptance

- [ ] `acceptance.sh` passes golden DDS3 result.
- [ ] Same-position second call proves live TT reuse.
- [ ] Record single DD-table p50/p95.
- [ ] Record `position_all_moves` p50/p95.
- [ ] Record 2,000-position batch throughput.
- [ ] Record BEN p50/p95.
- [ ] Record one-video transcription throughput.
- [ ] Record two-video transcription throughput.
- [ ] Record DDS3/BEN p95 while transcription is saturated.
- [ ] Record CPU steal, RAM pressure and NVMe saturation.

## E. Reliability acceptance

- [ ] Kill DDS3 container; automatic recovery verified.
- [ ] Kill BEN container; worker fails closed rather than fabricating evidence.
- [ ] Kill AI worker; automatic recovery verified.
- [ ] Reboot Cube; systemd/Docker restores all approved services automatically.
- [ ] Neon queue remains intact across reboot.
- [ ] No duplicate completion / incorrect finalization after worker restart.
- [ ] Disk warning/staging thresholds tested.

## F. Production queue cutover

- [ ] IONOS worker authorized as the only production queue consumer.
- [ ] Observe canary queue drain and result persistence.
- [ ] Disable scheduled GitHub production queue worker only after IONOS canary PASS.
- [ ] Keep GitHub workflow available as rollback during observation window.
- [ ] CI/regression/evidence/security workflows remain unchanged.

## G. Video cutover

Do not perform until the provider-neutral durable video claim/receipt contract exists.

- [ ] Explicit opaque request contract preserved.
- [ ] No Drive auto-discovery introduced.
- [ ] One representative video produces parity with existing worker.
- [ ] Checkpoints/receipts survive restart.
- [ ] Scale concurrency only after interactive DDS/BEN isolation is proven.

## Rollback

Stop the IONOS production consumer and re-enable the known GitHub production queue path. Neon remains the unchanged durable state boundary, so compute-host rollback should not require a database schema rollback.
