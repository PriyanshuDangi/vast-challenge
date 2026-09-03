# Legacy secret location

Cursor skills no longer read secrets from this repository directory.
On the execution VM, put the provided files at:

```text
/config/vss-cli-secret.yaml
/config/backend-secret.yaml
```

## Expected files

| File | What it is | Used by |
|------|------------|---------|
| `vss-cli-secret.yaml` | DataEngine ingest CLI secret (`name: vss2-secret`) | `dataengine-components/secret-manifest`, pipeline `--secret-file` |
| `backend-secret.yaml` | Retrieval K8s backend secret (`video-backend`) | `deployment/build-yamls`, `deploy`, config cross-checks |

Optional:

| File | What it is |
|------|------------|
| `vss-gui-secret.yaml` | DataEngine GUI-shaped secret (`vss2-secret`) |

Do not save real secrets under this repository directory.

## Rules

- Keep real credentials **only under `/config/`** — do not paste them into chat or commit them.
- The GPU bearer token comes from `/config/<team>.config`; keep it in sync with both secrets.
- VastDB **endpoint** differs by design: ingest `vdbendpoint` = data VIP (writes); backend `vdb_endpoint` = Query Engine VIP (reads). S3 / collection / embedding dims (256) must still match.
