---
name: dataengine-functions
description: >-
  Register and edit VSS DataEngine functions with the vastde CLI (functions
  create/update/list/get) and wire them into the pipeline manifest via
  function_deployments + links. Use when registering a built image, bumping a
  revision, changing resources, or adding/removing a function stage.
---

# DataEngine functions — register & wire (vss2)

Two sides, keep in sync:

| Side | Controls | Where |
|------|----------|-------|
| **CLI** (`vastde functions …`) | the registered function + image/revision | here |
| **Manifest** (`function_deployments` + `links`) | how it's deployed & wired | here + `dataengine-pipeline-manifest` to deploy |

**Requires:**
- **`vastde` CLI** — configured (`~/.vast/config.toml`) + authenticated. If it's not installed or on PATH, stop and ask the user.
- **Image built + pushed** (`dataengine-build-function`) and **function code** authored (`dataengine-edit-function`).

## Function VRNs

`vast:dataengine:functions:<function-name>` — `video-segmenter`, `video-detector`, `video-reasoner`, `video-embedder`, `video-vastdb-writer`, `prompt-suggester`.

## Register (create)

`--container-registry` = registry **name** in DataEngine (`vastde container-registries list`), not the URL. `--artifact-source` = image path without tag. Every op needs `--tenant`.

```bash
vastde functions create \
  --name video-detector \
  --container-registry <registry-name> \
  --artifact-source YOUR_ORG/vss-video-detector \
  --artifact-type image \
  --image-tag v2 \
  --tenant default
```

Repeat per function (`vss-video-segmenter`, `vss-video-detector`, `vss-video-reasoner`, `vss-video-embedder`, `vss-video-vastdb`).

## Verify

```bash
vastde functions list --tenant default
vastde functions get video-detector --tenant default -o json
```

## Edit (CLI)

New image build → publish a new revision (rebuild + push a fresh tag first):

```bash
vastde functions update video-detector \
  --image-tag v3 \
  --revision-description "Describe change" \
  --publish \
  --tenant default
```

- Each `--publish` = a new **revision**; if the manifest pins `revision:`, bump it and redeploy.
- Changing registry/artifact-source isn't a tag bump — if `update` can't, deregister and re-`create`.
- Removing a function: delete its `function_deployments` entry + `links` and redeploy (that stops it running). For the exact CLI delete subcommand, check `vastde functions --help`.

## In the pipeline manifest (`function_deployments`)

Registering ≠ deploying. In the ingest file these live under a top-level `manifest:` wrapper.

```yaml
manifest:
  config:
    secrets: [vss2-secret]
  function_deployments:
    - function_vrn: vast:dataengine:functions:video-detector   # registered function
      name: video-detector-7                                    # deployment INSTANCE (referenced by links)
      revision: 1
      config: { log_level: INFO }
      resources:
        min_cpu: 1000m
        max_cpu: 5000m
        min_memory: 1280Mi
        max_memory: 2560Mi
        min_concurrency: 1
        max_concurrency: 5     # segmenter/writer use 10; detector/reasoner/embedder use 5
        timeout: 120           # detector uses 300 (heavier)
  links:
    - source: [video-segment-land-trigger-6]
      destination: [video-detector-7]
      topic: vast:dataengine:topics:<broker-name>/<topic>   # only on trigger links
      config: { events_order: unordered, retries: 3 }
    - source: [video-detector-7]
      destination: [video-reasoner-3]                        # function→function omits topic
      config: { events_order: unordered, retries: 3 }
```

Rules: `function_vrn` = registered function; `name` = instance (what `links` reference). First link out of a trigger carries `topic:`. Handlers swallow errors, so `retries`/dead-letter only fire on hard failures.

## Add a new stage (end to end)

1. Code (`dataengine-edit-function`) → add dir to `source-code/scripts/build-vastde-functions.sh`.
2. Build + push (`dataengine-build-function`).
3. `vastde functions create … --tenant default`.
4. Add a `function_deployments` entry (VRN, unique instance `name`, resources).
5. Wire `links` (insert between neighbors; `topic:` only on trigger links).
6. Redeploy (`dataengine-pipeline-manifest`).

## Agent instructions

**Before any `vastde` command:** verify `vastde` is available (`vastde --version` / `command -v vastde`). If it's not installed or not on PATH, **stop and ask the user** for its location or to install/configure it — don't guess a path or skip the step.

1. Pass `--tenant` on every `functions` op; images pushed before create.
2. Keep in sync: function name (CLI) ↔ `function_vrn` ↔ instance `name` in `links`.
3. Decide if an edit is CLI-only (new revision), manifest-only (resources/wiring), or both — then redeploy the pipeline.
4. After schema-affecting changes (e.g. embedding dims), recreate the VastDB collection.
