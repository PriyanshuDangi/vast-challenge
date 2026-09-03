# Secrets (team-local)

Put the secret files you receive here so Cursor skills can read them. If a skill needs a secret and the file is missing, the agent will ask you to drop it in this folder.

## Expected files

| File | What it is | Used by |
|------|------------|---------|
| `vss-cli-secret.yaml` | DataEngine ingest CLI secret (`name: vss2-secret`) | `dataengine-components/secret-manifest`, pipeline `--secret-file` |
| `backend-secret.yaml` | Retrieval K8s backend secret (`video-backend`) | `deployment/build-yamls`, `deploy`, config cross-checks |

Optional:

| File | What it is |
|------|------------|
| `vss-gui-secret.yaml` | DataEngine GUI-shaped secret (`vss2-secret`) |

Save them under this directory with those exact names.

## Rules

- Keep real credentials **only** here — do not paste them into chat or commit them.
- Model hosts/ports/token for GPU calls still come from [`../gpu-endpoints.config`](../gpu-endpoints.config); keep those in sync with both secrets.
- VastDB **endpoint** differs by design: ingest `vdbendpoint` = data VIP (writes); backend `vdb_endpoint` = Query Engine VIP (reads). S3 / collection / embedding dims (256) must still match.
