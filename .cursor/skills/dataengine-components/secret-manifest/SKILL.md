---
name: dataengine-secret-manifest
description: >-
  Create and edit the VSS DataEngine ingest secret (vss2-secret) using
  /config/vss-cli-secret.yaml. Align with the pipeline manifest before
  vastde pipelines create. Use when filling S3, Cosmos-Reason2, Cosmos-Embed1, YOLO,
  VastDB, or prompt-suggester keys, or aligning the secret name.
---

# DataEngine secret manifest (vss2)

The pipeline functions all read one shared secret named **`vss2-secret`**.

## Where the filled secret lives (VM)

**First look here:**

| Path | Role |
|------|------|
| `/config/vss-cli-secret.yaml` | Filled CLI secret (`name: vss2-secret`, `entries[]`) |
| `/config/vss-gui-secret.yaml` | Optional GUI-shaped copy |
| `/config/<team>.config` | Team credentials and GPU bearer token |

If `vss-cli-secret.yaml` (or the GUI file you need) is **missing**, **stop** and tell the user to place the filled secret under `/config/` using those names. Do **not** look under the repository's `team-configs/`, and do not invent S3/VastDB/API keys or hosts.

Blueprint templates (reference only — copy from `vss-blueprint` if the user is authoring a new file):

| File | Format | Used by |
|------|--------|---------|
| `deployments/dataengine-vss-ingest-pipeline/vss-cli-secret-file-template.yaml` | CLI secret (`entries[]`) | `vastde pipelines create --secret-file` |
| `deployments/dataengine-vss-ingest-pipeline/vss-gui-secret-file-template.yaml` | nested dict under `vss2-secret:` | DataEngine UI |

Full key reference: [config-fields.md](config-fields.md).

**Requires:**
- **`vastde` CLI** — only for the optional dry-run validation / `pipelines create --secret-file`. If you'll validate and it's not installed or on PATH, stop and ask the user. (Authoring the YAML itself needs no CLI.)

## Fill workflow

```
- [ ] Prefer editing /config/vss-cli-secret.yaml (create from template if user asks)
- [ ] Collect user values for any still-empty placeholders
- [ ] Align secret name vss2-secret ↔ manifest.config.secrets
- [ ] Align kubernetes_cluster_vrn + namespace (secret + pipeline)
- [ ] Sync model auth with /config/<team>.config; use the documented shared model host/ports/dims
```

## Key groups (vss2)

- **S3** (segmenter/reasoner/embedder): `s3accesskey`, `s3secretkey`, `s3endpoint`.
- **Reasoning — Cosmos-Reason2** (VLM, reasoner): `cosmos_host`, `cosmos_port` (**8001**), `cosmoshttpscheme`, `cosmos_model` (`./Cosmos-Reason2-8B`), `cosmos_max_tokens`, `cosmos_temperature`, optional `cosmos_authorization` (from `GPU_BEARER_TOKEN` in `/config/<team>.config`).
- **Detector — YOLO11** (detector): `yolo_infer_host`, `yolo_infer_port` (**8002**), `yolo_conf`, `yolo_model` (`yolo11s.pt`), `yolo_presign_ttl`, `detection_sidecar_prefix`, `detection_store_frames`, optional `detector_authorization`.
- **Embedding — Cosmos-Embed1** (embedder): `embedding_local_nim`, `embeddinghost`, `embeddingport` (**8003**), `embeddinghttpscheme`, `embeddingmodel` (`nvidia/cosmos-embed1`), `embeddingdimensions` (**256** — must match VastDB vector column), `visual_embedding_enabled/model/dimensions`, `embedding_authorization`, `nvidia_api_key` (cloud only).
- **VastDB** (writer): `vdbendpoint`, `vdbbucket` (`vss-db`), `vdbschema` (`vss-schema`), `vdbcollection` (`vss-collection`), `vdbaccesskey`, `vdbsecretkey`.
- **Prompt suggester** (enrichment, same secret): `vdbpromptscollection` (`vss-prompts-events`), `suggestions_*`.
- **Segmenter**: `segment_duration` (5), `output_codec`, `output_format`, `output_bucket_suffix` (`-segments`).

## Format rules (CLI secret)

- Top-level: `name: vss2-secret`, `kubernetes_cluster_vrn`, `namespace`, `entries`.
- Each entry: `- key:` / `value:` — value must be a **string**.
- YAML: space after colon.

## Align with the pipeline

Ensure `manifest.config.secrets` includes `vss2-secret` (matches the CLI secret `name:`). Cross-check S3/model/dims/collection against `/config/backend-secret.yaml` when present (backend underscore keys). **One difference:** ingest `vdbendpoint` = **regular (data) VIP** (writes); backend `vdb_endpoint` = **Query Engine VIP** (reads). See `deploy-build-yamls`.

## Agent instructions

**Before any `vastde` command** (e.g. the dry-run validation below): verify `vastde` is available (`vastde --version` / `command -v vastde`). If it's not installed or not on PATH, **stop and ask the user** for its location or to install/configure it — don't guess a path or skip the step.

1. **Resolve secret file:** use `/config/vss-cli-secret.yaml`. If missing, ask the user to place it there. Do not search the repo's `team-configs/` or invent credentials.
2. Read that file (or GUI twin); list unfilled placeholders (`""`, `<your-...>`).
3. **Model-endpoint keys** — use shared host `166.19.38.112`, ports Reason2 `8001`, YOLO `8002`, Embed1 `8003`, dims `256`; get `GPU_BEARER_TOKEN` from the single `/config/*.config` team file.
4. **Anything you don't know** (S3/VastDB endpoints, access/secret keys) — **ask the user**; never guess.
5. Never copy `/config/` credentials into the repo or commit them; warn before git ops.
6. `embeddingdimensions` must equal the VastDB vector column (256); recreate the collection after changing it.
7. Validate with `vastde pipelines create … --secret-file /config/vss-cli-secret.yaml --dry-run` (when `vastde` is available; tenant comes from its config).
