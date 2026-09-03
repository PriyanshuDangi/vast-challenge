# DataEngine components (vss2)

Build and operate the VSS **ingest pipeline** on VAST DataEngine with the `vastde` CLI + manifests. Everything here needs `vastde` configured (`~/.vast/config.toml`) and authenticated; if `vastde` isn't installed/available, ask the user.

Before configuring `vastde`, source the single `/config/*.config` team file.
Use its team username/password and tenant/VMS values; if tenant or VMS URL is
not present, ask the user. Never search the repository's `team-configs/` or
copy credentials into it.

## Run order

| Step | Skill | Purpose |
|------|-------|---------|
| 1 | [edit-function](edit-function/SKILL.md) | Write/modify function code (`init`/`handler`, secrets, deps) |
| 2 | [build-function](build-function/SKILL.md) | `vastde build` + `docker push` the images |
| 3 | [functions](functions/SKILL.md) | Register (create/edit) functions + `function_deployments` |
| 4 | [triggers](triggers/SKILL.md) | Create/edit S3 + scheduled triggers |
| 5 | [secret-manifest](secret-manifest/SKILL.md) | Read `vss2-secret` from `/config/vss-cli-secret.yaml` |
| 6 | [pipeline-manifest](pipeline-manifest/SKILL.md) | Wire triggers→functions, deploy the pipeline |

## Golden rules (all vastde ops)

- **Login (hackathon teams):** put **your team username, password, and tenant** in `~/.vast/config.toml` (or `vastde config init` / `vastde config set`). Do **not** use empty-tenant / SUPER_ADMIN login — that is for cluster admins only and will fail for team users.
- **Operations:** this CLI reads tenant from `~/.vast/config.toml`; configure it from `/config/<team>.config`. Do not add `--tenant` to commands on versions where `vastde --help` does not expose that flag.
- **Broker:** never hardcode — discover the broker (a View with `DATABASE`/`KAFKA` protocols).
- If `vastde` isn't installed or auth fails, **ask the user** for the CLI / VMS URL / credentials — don't invent them.

## vss2 facts

- VM config: `/config/<team>.config`, `/config/vss-cli-secret.yaml`, `/config/backend-secret.yaml`, `/config/kubeconfig`. Never search the repo's `team-configs/`.
- Secret name: `vss2-secret`; buckets `vss-chunks` → `vss-chunks-segments`; VastDB `vss-db` / `vss-schema` / `vss-collection` (+ `vss-prompts-events`).
- Models: Cosmos-Reason2 `166.19.38.112:8001`, YOLO11 `:8002`, Cosmos-Embed1 `:8003` (256-dim), Canary `:8004` (optional); bearer token comes from `/config/<team>.config`.
