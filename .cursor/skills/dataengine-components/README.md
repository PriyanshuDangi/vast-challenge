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

- **Login:** cluster SUPER_ADMIN authenticates with an **empty tenant** in config (else `/api/token/<tenant>/` → 401).
- **Operations:** pass `--tenant <name>` on every `triggers`/`functions`/`pipelines` command (else `400: No tenant was provided`).
- **Broker:** never hardcode — discover the broker (a View with `DATABASE`/`KAFKA` protocols).

## vss2 facts

- Secret name: `vss2-secret` (filled file: `team-configs/secrets/vss-cli-secret.yaml`); buckets `vss-chunks` → `vss-chunks-segments`; VastDB `vss-db` / `vss-schema` / `vss-collection` (+ `vss-prompts-events`).
- Models: Cosmos-Reason2 `:8001`, YOLO11 `:8002`, Cosmos-Embed1 `:8003` (256-dim), Canary `:8004` (optional) — see `team-configs/gpu-endpoints.config`.
