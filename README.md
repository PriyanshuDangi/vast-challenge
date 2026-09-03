# vss2-skills

Cursor agent skills for the VSS2 video search stack — re-ingest indexed video and query the archive.

**Hackathon participants:** start with **[HACKATHON_GUIDELINES.md](HACKATHON_GUIDELINES.md)** (setup, UI walkthrough, starter videos, example prompts). UI screenshots are in [`docs/hackathon/`](docs/hackathon/).

Open this repo in Cursor so agents discover skills under [`.cursor/skills/`](.cursor/skills/). On the VM, runtime configuration is mounted at absolute `/config/`: `<team>.config`, `kubeconfig`, `vss-cli-secret.yaml`, and `backend-secret.yaml`. Keep credentials outside this repository.

## What’s in here

| Area | Purpose |
|------|---------|
| **Ingest** | Re-ingest existing indexed videos/chunks (hackathon path) |
| **Retrieval** | Search, browse, and ask questions over indexed video via the backend API |
| **DataEngine** | Build/register pipeline functions, triggers, secrets (`vastde`) |
| **Deployment** | Deploy retrieval K8s apps and check health |
| **GPU** | Health-check and smoke-test Cosmos / YOLO model endpoints |

Each skill is a folder with a `SKILL.md` containing full instructions. Agents read those files when a task matches the skill description.

## Skills index

See [`.cursor/README.md`](.cursor/README.md) for the full list, links, and typical flows.

**Ingest:** [reingest-videos](.cursor/skills/ingest/reingest-videos/SKILL.md) · [reingest-chunk](.cursor/skills/ingest/reingest-chunk/SKILL.md)

**Retrieval:** [login](.cursor/skills/retrieval/login/SKILL.md) · [search](.cursor/skills/retrieval/search/SKILL.md) · [list-metadata](.cursor/skills/retrieval/list-metadata/SKILL.md) · [dashboard](.cursor/skills/retrieval/dashboard/SKILL.md) · [suggest-prompts](.cursor/skills/retrieval/suggest-prompts/SKILL.md) · [videos](.cursor/skills/retrieval/videos/SKILL.md) · [agent-qa](.cursor/skills/retrieval/agent-qa/SKILL.md) · [vastdb-read](.cursor/skills/retrieval/vastdb-read/SKILL.md)

**DataEngine:** [dataengine-components](.cursor/skills/dataengine-components/README.md) · **Deployment:** [deployment](.cursor/skills/deployment/README.md) · **GPU:** [gpu](.cursor/skills/gpu/README.md)

## Quick start

1. Open the repo in Cursor (skills are picked up from `.cursor/skills/`).
2. Ask the agent to do something VSS-related — e.g. “re-ingest this video”, “search for people near the entrance”, “how many videos are indexed”.
3. The agent loads the matching skill and follows its `SKILL.md`.

Most retrieval and re-ingest tasks need auth first — the agent should use the **login** skill before other backend calls.
