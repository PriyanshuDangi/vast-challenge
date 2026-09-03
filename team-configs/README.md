# Team configs

## Team credentials

Place your organizer-provided file here:

```
team-configs/<your-team>.config
```

Example: `team-configs/team-b.config` — includes `USERNAME`, `PASSWORD`, S3/VastDB, `INGRESS_URL`, and **`GPU_BEARER_TOKEN`**.

## Shared GPU endpoints

All teams share the same model host/URLs in **[`gpu-endpoints.config`](gpu-endpoints.config)**.

`GPU_BEARER_TOKEN` starts as `<fill_me>`. Cursor should copy it from your `team-configs/<team>.config` into `gpu-endpoints.config` before GPU calls. Only ask the user to fill it if it is missing from both files.

GPU skills source `gpu-endpoints.config`. Do not invent hosts or paste tokens into chat logs.

## Secrets (DataEngine + backend)

Filled secret YAMLs go under **[`secrets/`](secrets/README.md)**:

| File | Purpose |
|------|---------|
| `secrets/vss-cli-secret.yaml` | Ingest / DataEngine `vss2-secret` (CLI format) |
| `secrets/backend-secret.yaml` | Retrieval `video-backend` K8s secret |

If a skill needs one of these and the file is missing, put it there (see `secrets/README.md`). Skills must not invent secret values.
