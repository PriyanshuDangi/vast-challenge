# DataEngine components (vss2)

Build and operate the VSS **ingest pipeline** on VAST DataEngine with the `vastde` CLI + manifests. Everything here needs `vastde` configured (`~/.vast/config.toml`) and authenticated; if `vastde` isn't installed/available, ask the user.

## Run order

| Step | Skill | Purpose |
|------|-------|---------|
| 1 | [edit-function](edit-function/SKILL.md) | Write/modify function code (`init`/`handler`, secrets, deps) |
| 2 | [build-function](build-function/SKILL.md) | `vastde build` + `docker push` the images |
| 3 | [functions](functions/SKILL.md) | Register (create/edit) functions + `function_deployments` |
| 4 | [triggers](triggers/SKILL.md) | Create/edit S3 + scheduled triggers |
| 5 | [secret-manifest](secret-manifest/SKILL.md) | Fill / read `vss2-secret` from `team-configs/secrets/vss-cli-secret.yaml` |
| 6 | [pipeline-manifest](pipeline-manifest/SKILL.md) | Wire triggers→functions, deploy the pipeline |

## Golden rules (all vastde ops)

- **Login (hackathon teams):** put **your team username, password, and tenant** in `~/.vast/config.toml` (or `vastde config init` / `vastde config set`). Do **not** use empty-tenant / SUPER_ADMIN login — that is for cluster admins only and will fail for team users.
- **Operations:** pass `--tenant <your-tenant>` on every `triggers`/`functions`/`pipelines` command (else `400: No tenant was provided`). Use the same tenant name as in the config / team config.
- **Broker:** never hardcode — discover the broker (a View with `DATABASE`/`KAFKA` protocols).
- If `vastde` isn't installed or auth fails, **ask the user** for the CLI / VMS URL / credentials — don't invent them.

## vss2 facts

- Secret name: `vss2-secret` (filled file: `team-configs/secrets/vss-cli-secret.yaml`); buckets `vss-chunks` → `vss-chunks-segments`; VastDB `vss-db` / `vss-schema` / `vss-collection` (+ `vss-prompts-events`).
- Models: Cosmos-Reason2 `:8001`, YOLO11 `:8002`, Cosmos-Embed1 `:8003` (256-dim), Canary `:8004` (optional) — see `team-configs/gpu-endpoints.config`.
