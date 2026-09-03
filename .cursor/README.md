# VSS2 Cursor Skills

Agent skills for working with the VSS2 video search stack. Each skill lives in its own folder with a `SKILL.md` that has full usage instructions — open that file when you need details.

## Layout

```
.cursor/skills/
├── dataengine-components/  # Build & deploy DataEngine ingest pipeline (vastde)
├── deployment/             # Deploy retrieval K8s apps (backend/frontend/batch-sync)
├── gpu/                    # Health + smoke-test model endpoints
├── ingest/                 # Re-ingest existing indexed video (hackathon path)
└── retrieval/              # Query and explore indexed video
```

Shared config under `team-configs/`:

- `gpu-endpoints.config` — GPU host / bearer / model URLs
- `secrets/` — filled `vss-cli-secret.yaml` + `backend-secret.yaml` (see `team-configs/secrets/README.md`; ask user to place if missing)
## Ingest

For this hackathon, **re-ingest** is the supported ingest path (no direct upload / stream / S3 put).

| Skill | Summary |
|-------|---------|
| [reingest-videos](skills/ingest/reingest-videos/SKILL.md) | Re-ingest a selected indexed video/stream via dashboard API |
| [reingest-chunk](skills/ingest/reingest-chunk/SKILL.md) | Re-ingest one specific Explore card / chunk |

→ [ingest/README.md](skills/ingest/README.md)

## Retrieval

Query the indexed archive through the backend API (`/api/v1`). Most routes need a JWT — start with **login**.

| Skill | Summary |
|-------|---------|
| [login](skills/retrieval/login/SKILL.md) | Authenticate and obtain a bearer token |
| [search](skills/retrieval/search/SKILL.md) | Semantic/hybrid search over the archive |
| [list-metadata](skills/retrieval/list-metadata/SKILL.md) | Discover filterable fields and values |
| [dashboard](skills/retrieval/dashboard/SKILL.md) | Aggregate stats and ingest health |
| [suggest-prompts](skills/retrieval/suggest-prompts/SKILL.md) | AI-generated search prompt suggestions |
| [videos](skills/retrieval/videos/SKILL.md) | Browse, play back, and summarize a video |
| [agent-qa](skills/retrieval/agent-qa/SKILL.md) | Natural-language Q&A over the archive |
| [vastdb-read](skills/retrieval/vastdb-read/SKILL.md) | Raw VastDB inspection (bypasses the API) |

→ [retrieval/README.md](skills/retrieval/README.md)

## DataEngine components

Build and operate the ingest pipeline with `vastde` + manifests.

| Skill | Summary |
|-------|---------|
| [edit-function](skills/dataengine-components/edit-function/SKILL.md) | Write/modify function code |
| [build-function](skills/dataengine-components/build-function/SKILL.md) | `vastde build` + push images |
| [functions](skills/dataengine-components/functions/SKILL.md) | Register functions / deployments |
| [triggers](skills/dataengine-components/triggers/SKILL.md) | S3 + scheduled triggers |
| [secret-manifest](skills/dataengine-components/secret-manifest/SKILL.md) | Fill `vss2-secret` |
| [pipeline-manifest](skills/dataengine-components/pipeline-manifest/SKILL.md) | Wire and deploy the pipeline |

→ [dataengine-components/README.md](skills/dataengine-components/README.md)

## Deployment

Retrieval-side Kubernetes apps (`deployments/vss-k8s-application/` in the blueprint tree).

| Skill | Summary |
|-------|---------|
| [build-yamls](skills/deployment/build-yamls/SKILL.md) | Fill secrets + namespace/cluster/image tags |
| [deploy](skills/deployment/deploy/SKILL.md) | Build/push + quick deploy + ingress |
| [health](skills/deployment/health/SKILL.md) | Pods, `/health`, `/api/v1/config` |

→ [deployment/README.md](skills/deployment/README.md)

## GPU models

Shared endpoints live in [`team-configs/gpu-endpoints.config`](../team-configs/gpu-endpoints.config). Full how-to: [gpu/README.md](skills/gpu/README.md).

| Skill | Summary |
|-------|---------|
| [gpu README](skills/gpu/README.md) | What each model does + curl examples (Reason2, YOLO, Embed1, Canary) |
| [model-health](skills/gpu/model-health/SKILL.md) | Liveness/readiness for all four |
| [model-smoke-test](skills/gpu/model-smoke-test/SKILL.md) | Minimal real inference per model |

## Typical flows

**Re-ingest** → `login` → `reingest-videos` (or `reingest-chunk`) → `dashboard`

**Search** → `login` → `list-metadata` (optional) → `search` or `agent-qa`

**Watch a result** → `login` → `videos`
