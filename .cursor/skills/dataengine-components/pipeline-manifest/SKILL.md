---
name: dataengine-pipeline-manifest
description: >-
  Create, edit, and deploy VSS DataEngine pipeline manifests (function_deployments,
  links, triggers, secrets) with vastde pipelines create/update/deploy. Use when
  adding/reordering stages, wiring triggers to functions, changing the secret, or
  deploying the ingest/enrichment pipelines.
---

# DataEngine pipeline manifest (vss2)

Binds **triggers → functions** into a runnable graph and attaches the shared secret.

**Requires:**
- **`vastde` CLI** — configured (`~/.vast/config.toml`) + authenticated. If it's not installed or on PATH, stop and ask the user.
- **VM config** — source the single `/config/*.config` team file; secret is `/config/vss-cli-secret.yaml`. Never search the repo's `team-configs/`.
- **Triggers created** (`dataengine-triggers`), **functions registered** (`dataengine-functions`), **secret filled** (`dataengine-secret-manifest`).

## Manifests

| Pipeline | File | Shape |
|----------|------|-------|
| Ingest | `deployments/dataengine-vss-ingest-pipeline/vss-ingest-pipeline-file.yaml` | fields nested under top-level `manifest:` |
| Enrichment (scheduled) | `deployments/dataengine-vss-enrichment-pipeline/vss-enrichment-pipeline-file.yaml` | fields at top level (no `manifest:` wrapper) |

⚠️ The two files differ in nesting — match the file you're editing.

## Ingest flow (vss2)

```
video-chunk-land-trigger-1   → video-segmenter-2
video-segment-land-trigger-6 → video-detector-7 → video-reasoner-3 → video-embedder-4 → video-vastdb-writer-5
```

Pipeline name: defaults to `video-realtime-processing-pipeline` — confirm with the client, who may use their own name; fall back to this default if they don't specify.

## Structure

```yaml
kubernetes_cluster_vrn: <your-kubernetes-cluster-vrn>   # vastde compute-clusters list
namespace: <your-namespace>
manifest:                                # ingest only; enrichment omits this wrapper
  config:
    secrets: [vss2-secret]
  function_deployments:                  # see dataengine-functions
    - function_vrn: vast:dataengine:functions:video-segmenter
      name: video-segmenter-2
      revision: 1
      config: { log_level: INFO }
      resources: { min_cpu: 1000m, max_cpu: 5000m, min_memory: 1280Mi, max_memory: 2560Mi, min_concurrency: 1, max_concurrency: 10, timeout: 120 }
  links:                                 # trigger→function and function→function edges
    - source: [video-chunk-land-trigger-1]
      destination: [video-segmenter-2]
      topic: vast:dataengine:topics:<broker-name>/<topic>   # only on trigger-sourced links
      config: { events_order: unordered, retries: 3 }
  triggers:                              # see dataengine-triggers
    - name: video-chunk-land-trigger-1
      vrn: vast:dataengine:triggers:video-chunk-land-trigger
```

## Wiring rules

- `links.source`/`destination` reference **instance names** (`name` under `function_deployments` / `triggers`), not VRNs.
- First link out of a trigger carries `topic:`; function→function links omit it.
- Each stage swallows errors (`{"status":"error"|"skipped"}`), so `retries`/dead-letter fire only on hard failures (crash/OOM/timeout).

## Deploy

Pipeline commands use the tenant from `~/.vast/config.toml`. Use `--dry-run` to validate first.

```bash
# Filled secret is mounted on the VM at:
#   /config/vss-cli-secret.yaml
# If missing, ask the user to put it there.
cd deployments/dataengine-vss-ingest-pipeline
vastde pipelines create \
  --name video-realtime-processing-pipeline \
  --config @vss-ingest-pipeline-file.yaml \
  --secret-file /config/vss-cli-secret.yaml \
  --deploy
```

Before deploying fill: `kubernetes_cluster_vrn`, `namespace`, every `topic:` VRN, and confirm `secrets: [vss2-secret]`. If `/config/vss-cli-secret.yaml` is missing, stop and ask the user to add it.

## Verify / update

```bash
vastde pipelines list
vastde pipelines get video-realtime-processing-pipeline -o json

vastde pipelines update video-realtime-processing-pipeline --config @vss-ingest-pipeline-file.yaml
vastde pipelines deploy video-realtime-processing-pipeline
```

Then upload a test `.mp4` to `vss-chunks` and confirm executions through segmenter → detector → reasoner → embedder → writer.

## Agent instructions

**Before any `vastde` command:** verify `vastde` is available (`vastde --version` / `command -v vastde`). If it's not installed or not on PATH, **stop and ask the user** for its location or to install/configure it — don't guess a path or skip the step. Confirm the tenant with `vastde config view` and validate with `--dry-run` first.

1. Confirm `/config/vss-cli-secret.yaml` exists; if not, ask the user to add it. Do not search the repo's `team-configs/`.
2. Use `--secret-file` pointing at that path (not the empty Blueprint template).
3. Keep `manifest.config.secrets: [vss2-secret]`.

## Editing checklist

1. Prereqs exist (triggers, functions, secret at `/config/vss-cli-secret.yaml`).
2. Add/adjust the `function_deployments` entry (resources, timeout, revision).
3. Wire `links` (instance names; topic only on trigger links).
4. Add the `triggers` entry if new.
5. Keep the secret name `vss2-secret`.
6. Redeploy using the tenant already configured in `vastde`.
