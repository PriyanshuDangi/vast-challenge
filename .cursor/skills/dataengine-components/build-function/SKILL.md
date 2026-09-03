---
name: dataengine-build-function
description: >-
  Build and push VSS DataEngine function images with vastde build + docker push
  (video-segmenter, video-detector, video-reasoner, video-embedder,
  video-vastdb-writer, prompt-suggester). Use when building/pushing a function
  image or preparing images before vastde functions create.
---

# Build & push DataEngine function images (vss2)

Turns function source dirs into images in your registry. After this, register them with `dataengine-functions`.

**Requires:**
- **`vastde` CLI** — configured (`~/.vast/config.toml`) and authenticated; `vastde build` / `vastde functions build` do the image build. If it's not installed/available, ask the user.
- **Team config** — the single `/config/*.config` file on the VM. Use it when configuring `vastde`; never search the repo's `team-configs/`.
- **Builder image** — `builder_image_url` from `~/.vast/config.toml` must be present locally (`docker images`); `vastde build` won't run without it.
- **Docker** — for `docker tag` / `docker push` to your registry.

## Function → image map

Source of truth: `source-code/scripts/build-vastde-functions.sh`.

| Function | Source dir | Image |
|----------|-----------|-------|
| `video-segmenter` | `source-code/ingest/video-segmenter` | `vss-video-segmenter` |
| `video-detector` | `source-code/ingest/video-detector` | `vss-video-detector` |
| `video-reasoner` | `source-code/ingest/video-reasoner` | `vss-video-reasoner` |
| `video-embedder` | `source-code/ingest/video-embedder` | `vss-video-embedder` |
| `video-vastdb-writer` | `source-code/ingest/vastdb-writer` | `vss-video-vastdb` |
| `prompt-suggester` (enrichment) | `source-code/enrichment/prompt-suggester` | `vss-video-events` |

DataEngine workloads target `linux/amd64`.

## Preferred — helper script (all images)

```bash
ECR=your.registry/vss TAG=v2 source-code/scripts/build-vastde-functions.sh
```

It runs `vastde functions build <name>` then `docker tag` + `docker push` for every image above.

> The env var is named `ECR` in the script, but it's just the **registry base** — use any container registry (Docker Hub, GHCR, Harbor, a VAST-hosted/insecure registry, etc.), not necessarily AWS ECR. Its default is an example AWS ECR URL; override `ECR=` (and `TAG=`) to point at yours.

## Manual (single function)

```bash
cd source-code/ingest/video-detector
vastde build -t your.registry/vss-video-detector:v2 . --platform linux/amd64
docker push your.registry/vss-video-detector:v2
```

For HTTP registries, add the host to Docker `insecure-registries` before push.

## Gotchas

- Rebuild + push a **new tag** for any code change — don't silently reuse a tag (registers get confusing).
- Build cache error `volume is in use` → remove stale build containers/volumes and retry.
- The writer's image is `vss-video-vastdb` (function name is `video-vastdb-writer`).

## Agent instructions

1. Verify `vastde` is available first (e.g. `vastde --version` / `command -v vastde`). **If it's not installed or not on PATH, stop and ask the user** for the CLI location or to install/configure it — do not guess a path or skip the build.
2. Confirm the builder image is present locally and Docker is running before `vastde build`.
3. Rebuild + push a new `TAG` for any code change.

## Next

Register/refresh the function with `dataengine-functions` (`vastde functions create/update …`; tenant comes from the CLI config), then wire it in `dataengine-pipeline-manifest`.
