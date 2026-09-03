---
name: dataengine-triggers
description: >-
  Create and edit VSS DataEngine triggers (S3 Element triggers for vss-chunks and
  vss-chunks-segments, and scheduled triggers) with the vastde CLI plus their
  pipeline-manifest references. Use when adding, editing, or wiring ingest/enrichment
  triggers.
---

# DataEngine triggers (vss2)

Triggers are created with `vastde` and then referenced by name in the pipeline manifest.

**Requires:**
- **`vastde` CLI** — configured (`~/.vast/config.toml`) + authenticated. If it's not installed or on PATH, stop and ask the user.
- **Event broker + topic** — discovered, not assumed (broker = a View with `DATABASE`/`KAFKA` protocols; list topics with `vastde topics list --database-name <broker>`).

## VSS trigger set

| Trigger name | Bucket / type | Notes |
|--------------|---------------|-------|
| `video-chunk-land-trigger` | Element, bucket `vss-chunks` | fires on uploaded/streamed chunks |
| `video-segment-land-trigger` | Element, bucket `vss-chunks-segments` | fires on segmenter output |
| `vss-prompt-suggester-scheduled-trigger` | Scheduled (cron) | enrichment prompt-suggester |

Segments land in a **separate** bucket (`vss-chunks-segments` = chunks bucket + `output_bucket_suffix`), so the segment trigger watches the whole bucket — no prefix/suffix filter needed.

VRNs used by the manifest: `vast:dataengine:triggers:<trigger-name>`.

## Create — Element (S3)

`--broker-name`/`--topic` are **cluster-specific — discover, don't assume** (broker = View with `DATABASE`/`KAFKA`). Every op needs `--tenant`. Preview with `--dry-run`.

```bash
vastde triggers create \
  --name video-chunk-land-trigger \
  --type Element \
  --source-bucket vss-chunks \
  --events "ObjectCreated:*" \
  --broker-type Internal \
  --broker-name <broker-name> \
  --topic <topic> \
  --tenant default

vastde triggers create \
  --name video-segment-land-trigger \
  --type Element \
  --source-bucket vss-chunks-segments \
  --events "ObjectCreated:*" \
  --broker-type Internal \
  --broker-name <broker-name> \
  --topic <topic> \
  --tenant default
```

## Create — Scheduled (enrichment)

```bash
vastde triggers create \
  --name vss-prompt-suggester-scheduled-trigger \
  --type Schedule \
  --schedule "*/15 * * * *" \
  --tenant default
```

(Schedule can also be set in the DataEngine UI trigger config. Used by the enrichment pipeline.)

## Verify / edit

```bash
vastde triggers list --tenant default
vastde triggers get video-chunk-land-trigger --tenant default -o json
```

To edit, recreate with the same name or adjust in the DataEngine UI; keep the exact names so the pipeline manifest VRNs still match.

## In the pipeline manifest

```yaml
triggers:
  - name: video-chunk-land-trigger-1                      # instance name (referenced by links)
    vrn: vast:dataengine:triggers:video-chunk-land-trigger
links:
  - source: [video-chunk-land-trigger-1]
    destination: [video-segmenter-2]
    topic: vast:dataengine:topics:<broker-name>/<topic>   # topic ONLY on trigger-sourced links
    config: { events_order: unordered, retries: 3 }
```

Deploy via `dataengine-pipeline-manifest`.

## Agent instructions

**Before any `vastde` command:** verify `vastde` is available (`vastde --version` / `command -v vastde`). If it's not installed or not on PATH, **stop and ask the user** for its location or to install/configure it — don't guess a path or skip the step.

1. Confirm `vastde` config (team username/password/**tenant** in `~/.vast/config.toml`; `--tenant` on ops). Do not use empty-tenant SUPER_ADMIN login for hackathon teams.
2. Discover broker + topic (don't assume); topic VRN: `vast:dataengine:topics:<broker>/<topic>`.
3. Pass `--tenant` on create/list/get; preview with `--dry-run`.
4. Keep exact trigger names so pipeline VRNs match.
