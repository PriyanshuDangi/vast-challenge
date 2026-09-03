# VSS ingest secret — all keys

Filled file for Cursor: `/config/vss-cli-secret.yaml` (ask the user to place it there if missing). Blueprint templates are under `deployments/dataengine-vss-ingest-pipeline/`. Secret **name:** `vss2-secret`.

## Sourcing values

- **Filled secret YAML** — read/write `/config/vss-cli-secret.yaml` first; never look under the repo's `team-configs/` or invent credentials.
- **Model endpoints** — shared host `166.19.38.112`; ports Reason2 **8001**, YOLO **8002**, Embed1 **8003**; bearer token from `/config/<team>.config`.
- **Anything you don't know** (S3/VastDB endpoints + keys, credentials, bucket/schema/collection names if non-default) — **ask the user**. Do not guess credentials, endpoints, or hosts.
- Defaults shown below are safe fallbacks only for non-secret, non-endpoint fields (timeouts, durations, dims already fixed at 256).

## S3 (video-segmenter, video-reasoner, video-embedder)

| Key | Required | Default / notes |
|-----|----------|-----------------|
| `s3accesskey` | Yes | |
| `s3secretkey` | Yes | |
| `s3endpoint` | Yes | `http://` or `https://` tenant S3 VIP |

## Reasoning — Cosmos-Reason2 (video-reasoner)

vss2 uses **Cosmos-Reason2** as the VLM (no nemotron provider key in the current secret).

| Key | Required | Default |
|-----|----------|---------|
| `cosmos_host` | Yes | IP/hostname reachable from DataEngine workers (or host/path prefix for routed APIs) |
| `cosmos_port` | Yes | `8001` |
| `cosmoshttpscheme` | No | `http` (local) / `https` (routed) |
| `cosmos_model` | No | `./Cosmos-Reason2-8B` |
| `cosmos_max_tokens` | No | `6000` |
| `cosmos_temperature` | No | `0.2` |
| `cosmos_authorization` | No | optional Bearer for hosted/routed Cosmos APIs |

## Object detection — YOLO11 (video-detector)

| Key | Required | Default |
|-----|----------|---------|
| `yolo_infer_host` | Yes | detector inference server |
| `yolo_infer_port` | No | `8022` |
| `yolo_conf` | No | `0.4` |
| `yolo_model` | No | `yolo11s.pt` |
| `yolo_presign_ttl` | No | `3600` |
| `detection_sidecar_prefix` | No | `detections/` |
| `detection_store_frames` | No | `true` |
| `detector_authorization` | No | optional Bearer for hosted detector APIs |

## Embedding — Cosmos-Embed1 (video-embedder)

| Key | Required | Default |
|-----|----------|---------|
| `embedding_local_nim` | Yes | `true` |
| `embeddinghost` | Yes | `127.0.0.1` (or host/path prefix for routed API) |
| `embeddingport` | Yes | `8002` |
| `embeddinghttpscheme` | Yes | `http` |
| `embedding_authorization` | No | Bearer for routed/gateway API; empty for local NIM |
| `embeddingmodel` | Yes | `nvidia/cosmos-embed1` |
| `embeddingdimensions` | Yes | `256` — must match VastDB vector column |
| `visual_embedding_enabled` | No | `true` |
| `visual_embedding_model` | No | `nvidia/cosmos-embed1` |
| `visual_embedding_dimensions` | No | `256` |
| `nvidia_api_key` | If cloud NIM | required when `embedding_local_nim` is `false` |

## Video reasoner (video-reasoner)

| Key | Required | Default |
|-----|----------|---------|
| `max_video_size_mb` | No | `100` |
| `scenario` | No | `general` — see `source-code/ingest/video-reasoner/README.md` |

## VastDB (video-vastdb-writer)

| Key | Required | Default |
|-----|----------|---------|
| `vdbendpoint` | Yes | **regular (data) VIP** — the writer writes via the data path (the retrieval backend reads via a different, Query Engine VIP) |
| `vdbbucket` | Yes | `vss-db` (must match backend `vdb_bucket`) |
| `vdbschema` | Yes | `vss-schema` |
| `vdbaccesskey` | Yes | |
| `vdbsecretkey` | Yes | |
| `vdbcollection` | Yes | `vss-collection` |

## Prompt suggester (enrichment — same `vss2-secret`)

| Key | Required | Default |
|-----|----------|---------|
| `vdbpromptscollection` | Yes (enrichment) | `vss-prompts-events` |
| `suggestions_max_segments` | No | `48` |
| `suggestions_search_count` | No | `10` |
| `suggestions_events_count` | No | `30` |
| `suggestions_max_events_per_video` | No | `3` |
| `suggestions_lookback_hours` | No | `168` |

## Segmenter (video-segmenter)

| Key | Required | Default |
|-----|----------|---------|
| `segment_duration` | No | `5` (seconds) |
| `output_codec` | No | `libx264` |
| `output_format` | No | `mp4` |
| `output_bucket_suffix` | No | `-segments` (segments bucket = chunks bucket + suffix) |
