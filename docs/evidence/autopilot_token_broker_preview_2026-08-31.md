# Autopilot token broker Preview evidence — 2026-08-31

Draft PR: #991

## Scope

This evidence records the first usable Preview deployment of the Phase 3B
GitHub token broker and its bounded-executor replacement. It does not authorize
or claim a production deployment, merge, Video run, TRAIN run, or Oracle
instance stop.

The initial deployment used the pinned broker source revision
`a204ba9dfbb70542383262026c0c772e04019a6c` and broker source-tree object
`5f227f3885535365d4e9c77815aac8b40d08e9ba`. The GitHub App installation
remained limited to `olegmed1-art/bridge-video-free`. Broker credentials were
configured only for the Vercel Preview environment; no credential value is
included in this evidence.

## Initial verified Preview

| Field | Verified value |
|---|---|
| Vercel project | `bridge-school-autopilot` |
| Project ID | `prj_KvQo3rPnwNs488hyDiMZ9hMU9d5R` |
| Deployment ID | `dpl_AcsA2EbMhCW3Y2iJmrG6keVzRSzH` |
| Deployment URL | `bridge-school-autopilot-8z4i4xnz5-olegmed1-4368s-projects.vercel.app` |
| Source | Vercel CLI |
| Region | `fra1` |
| State | `READY` |
| Target | `null` (Preview) |
| Aliases | none |

The authenticated Preview health check returned the complete expected
contract:

```json
{
  "status": "ok",
  "service": "school-autopilot-github-token-broker",
  "preview_only": true,
  "production_mutations_enabled": false,
  "github_token_broker_enabled": true
}
```

The one-shot job printed `PREVIEW_HEALTH=PASS` at
`2026-08-31T09:39:11Z`.

## Bounded-executor Preview replacement

After the broker stopped returning the GitHub installation token and gained
the server-side bounded draft executor, the replacement source was deployed as
a second protected Preview:

| Field | Verified value |
|---|---|
| Source revision | `2735ecbb4a455bec58007064210913c341795896` |
| Source-tree object | `d4eebbeee2921bd0d9ba01ca68e0006843e1d414` |
| Deployment ID | `dpl_7cqY6DUSdcvWLGTAkZi8jZf8HSXv` |
| Deployment URL | `bridge-school-autopilot-cslfiz83g-olegmed1-4368s-projects.vercel.app` |
| GitHub Actions run / job | `33387671941` / `99473846348` |
| Source | Vercel CLI |
| Region | `fra1` |
| State | `READY` |
| Target | `null` (Preview) |
| Aliases | none |

The authenticated one-shot check accepted the complete versioned health
contract and printed `PREVIEW_HEALTH=PASS`:

```json
{
  "status": "ok",
  "service": "school-autopilot-github-token-broker",
  "service_version": "0.2.0",
  "preview_only": true,
  "production_mutations_enabled": false,
  "github_token_broker_enabled": true,
  "raw_installation_token_exposed": false,
  "bounded_draft_executor_enabled": true
}
```

Independent Vercel readback confirmed the same deployment as `READY`,
`target=null`, `alias=[]`, and `source=cli`. Vercel runtime evidence recorded
`GET /healthz 200`. The project remains `live=false` with no domains. The
initial Preview is retained temporarily as a rollback candidate, so the
project now contains two Preview deployments and zero production deployments
or aliases.

## Production cleanup

Vercel treated the first deployment of the previously empty project as
production despite the Preview-only request. That temporary baseline,
`dpl_85f1uwAnfxdQEjGveGKT3U4mJW3w`, was deleted after the valid second
deployment had reached `READY` and passed `/healthz`.

Independent Vercel API readback immediately after deletion proved:

- deployment count: `1`;
- sole deployment: `dpl_AcsA2EbMhCW3Y2iJmrG6keVzRSzH`;
- sole deployment target: `null`;
- sole deployment aliases: none;
- project `live`: `false`;
- project domains: none.

Production routing is therefore absent. The Preview deployment remains
addressable only through its protected deployment URL.

## One-shot workflow false negative

GitHub Actions run `33365526195`, final job `99445154925`, is red even though
the deployment and health gate passed. After `PREVIEW_HEALTH=PASS`, the
temporary Vercel CLI process remained inside the best-effort `vercel logout`
cleanup. The SSH session later ended with `client_loop: send disconnect:
Broken pipe`, producing exit code `255` at `2026-08-31T09:48:46Z`.

This is a post-success cleanup false negative, not a deployment or health
failure. Re-running the workflow would create another deployment and is not a
safe way to repair historical status. The one-shot workflow is removed in the
same cleanup commit as this evidence so that it cannot be triggered again.

The bounded replacement used the same one-shot shape with an explicit
eight-second logout timeout. Its retained acceptance evidence is the Vercel
deployment readback, the exact authenticated health-contract pass, and the
runtime `200`; a later runner or SSH cleanup outcome cannot reverse those
already completed checks. The replacement one-shot workflow is removed in this
cleanup commit and must not be re-run to repair a historical job status.

The disposable runner used a tmpfs home directory, an ephemeral Docker
container with `--rm`, and an Oracle-side `EXIT/HUP/INT/TERM` cleanup trap. The
job log does not independently prove the final remote cleanup readback after
the SSH disconnect, so that detail is not promoted to verified evidence.

## Boundaries retained

- Oracle was not stopped;
- no production deployment or alias remains;
- no merge was performed;
- no Video or TRAIN workload was started;
- no secret value was printed, committed, or copied into this evidence;
- neither Preview deployment is promoted or a production route.
