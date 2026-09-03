---
name: deploy-build-yamls
description: >-
  Prepare the VSS retrieval Kubernetes manifests — set image tags and prepare
  YAMLs. Prefer filling backend-secret from team-configs/secrets/backend-secret.yaml
  when the user has it; if missing, ask once, then continue without it. Align
  underscore keys with ingest vss2-secret when both are available. Use before
  QUICK_DEPLOY.sh.
---

# Deployment: build YAMLs (vss2)

Get `deployments/vss-k8s-application/` ready to apply. Nothing here talks to the cluster — that's `deploy-deploy`.

## Backend secret (optional for this skill)

**Look first:** [`team-configs/secrets/backend-secret.yaml`](../../../team-configs/secrets/backend-secret.yaml)

| Path | Role |
|------|------|
| `team-configs/secrets/backend-secret.yaml` | Filled retrieval backend secret (use if present) |
| `team-configs/secrets/vss-cli-secret.yaml` | Ingest `vss2-secret` — cross-check when present |
| `team-configs/secrets/README.md` | Naming |

If `backend-secret.yaml` is **missing**:

1. **Ask the user once** whether they have the backend secret (or can put it under `team-configs/secrets/backend-secret.yaml`).
2. If they provide it / place the file → fill/edit as below.
3. If they **don't** have it (or decline) → **continue without the secret**. Still do image tags and any other non-secret YAML prep. Do **not** invent JWT/S3/VastDB keys. Note that `QUICK_DEPLOY.sh` will fail at apply until a secret exists in the deploy dir.

When applying from the Blueprint tree, you may copy/symlink a filled secret to `deployments/vss-k8s-application/backend-secret.yaml` (gitignored there).

## 1. Backend secret (when available)

`config.yaml` is mounted at `/etc/secrets/config.yaml`. Fill:

| Group | Keys | Notes |
|-------|------|-------|
| VastDB | `vdb_endpoint`, `vdb_access_key`, `vdb_secret_key` | buckets already `vss-db`/`vss-schema`/`vss-collection`/`vss-prompts-events` |
| S3 | `s3_endpoint`, `s3_access_key`, `s3_secret_key` | buckets `vss-chunks`, `vss-chunks-segments` |
| Embed1 | `embedding_host`/`embedding_port` (**8003**), `embedding_dimensions: 256`, `embedding_local_nim`, `embedding_authorization` | **from `team-configs/gpu-endpoints.config`**; same server + dims as ingest |
| Reason2 | `cosmos_host`/`cosmos_port` (**8001**), `cosmos_model`, `cosmos_authorization` | **from `team-configs/gpu-endpoints.config`**; synthesis (text-only) |
| Cloud | `nvidia_api_key` | only if `embedding_local_nim: false` |
| VMS | `vast_host`, `tenant_name` (`default`) | user login validated via `/api/token/` |
| Auth | `jwt_secret` | **required**, long random (`openssl rand -hex 32`); backend won't start if empty; reuse on upgrades |
| Misc | `max_upload_size_mb`, `display_timezone` | |

> **Model hosts/ports/auth/dims come from [`team-configs/gpu-endpoints.config`](../../../team-configs/gpu-endpoints.config).** Map `COSMOS_REASON2_*`→`cosmos_*` and `COSMOS_EMBED1_*`→`embedding_*` exactly; don't invent them. The backend does **not** call YOLO, so skip `YOLO_*` here.

`NAMESPACE` in the secret is substituted by `QUICK_DEPLOY.sh` at apply time — leave the literal `NAMESPACE`.

## 2. Image tags (always)

Set real images (default placeholder `your.registry/...:v1`) in `backend-deployment.yaml`, `frontend-deployment.yaml`, `video-batch-sync-deployment.yaml`. `NAMESPACE`/`CLUSTER_NAME` are substituted by the script — don't hardcode.

## 3. Align with ingest (when both secrets exist)

Backend uses **underscore** keys; ingest `vss2-secret` uses **no-underscore** keys (`s3endpoint`, `vdbendpoint`, `embeddinghost`, `embeddingdimensions`). Compare `team-configs/secrets/backend-secret.yaml` ↔ `team-configs/secrets/vss-cli-secret.yaml` when both are present. S3, embedding/Reason2 hosts, embedding **dims (`256`)**, and bucket/schema/collection names must match, or search returns nothing.

**Exception — the VastDB endpoint differs by design:** the backend queries VastDB through the **Query Engine VIP**, while ingest writes through the **regular (data) VIP**. So `vdb_endpoint` (backend) and `vdbendpoint` (ingest) point at *different* VIPs of the same cluster — don't force them equal.

| Concept | Backend | Ingest (`vss2-secret`) | Match? |
|---------|---------|------------------------|--------|
| S3 endpoint | `s3_endpoint` | `s3endpoint` | same |
| VastDB endpoint | `vdb_endpoint` = **Query Engine VIP** | `vdbendpoint` = **regular (data) VIP** | **different** |
| Bucket / schema / collection | `vdb_bucket`/`vdb_schema`/`vdb_collection: vss-collection` | `vdbbucket`/`vdbschema`/`vdbcollection` | same |
| Embed host/dims | `embedding_host` / `embedding_dimensions: 256` | `embeddinghost` / `embeddingdimensions` | same |
| Reason2 host | `cosmos_host` | `cosmos_host` | same |

## Agent instructions

1. Look for `team-configs/secrets/backend-secret.yaml`.
2. If **missing** → ask the user once if they have it (or can place it there). If yes → use it. If no → **continue without the secret**; do image tags and other YAML prep; do not invent credentials; briefly note deploy will need the secret later.
3. If the file **is** present → fill/edit; every placeholder (`""`, `http://`, `nvapi-xxx`) must be resolved or asked.
4. **Model fields** (`embedding_*`, `cosmos_*`) — from [`team-configs/gpu-endpoints.config`](../../../team-configs/gpu-endpoints.config); skip YOLO.
5. **Anything you don't know** (S3/VastDB endpoints, keys, VMS host) — ask the user; don't guess.
6. Generate `jwt_secret` with `openssl rand -hex 32` if empty (only when editing a secret the user provided).
7. Cross-check against `team-configs/secrets/vss-cli-secret.yaml` **when present**; skip the cross-check if it's missing (don't block).
8. Set image tags; leave `NAMESPACE`/`CLUSTER_NAME` literal. Then `deploy-deploy` when ready.
