# vss2-skills

Cursor agent skills for the VSS2 video search stack — ingest video into the pipeline and query the indexed archive.

**Hackathon participants:** start with **[HACKATHON_GUIDELINES.md](HACKATHON_GUIDELINES.md)** (setup, UI walkthrough, starter videos, example prompts). UI screenshots are in [`docs/hackathon/`](docs/hackathon/).

Open this repo in Cursor so agents discover skills under [`.cursor/skills/`](.cursor/skills/). Add your credentials file at `team-configs/<your-team>.config` (see [team-configs/README.md](team-configs/README.md)).

## What’s in here

| Area | Purpose |
|------|---------|
| **Ingest** | Land fixed-length MP4 chunks in `vss-chunks` (S3 upload or live stream capture) |
| **Retrieval** | Search, browse, and ask questions over indexed video via the backend API |

Each skill is a folder with a `SKILL.md` containing full instructions. Agents read those files when a task matches the skill description.

## Skills index

See [`.cursor/README.md`](.cursor/README.md) for the full list, links, and typical flows.

**Ingest:** [upload-videos](.cursor/skills/ingest/upload-videos/SKILL.md) · [stream-capture](.cursor/skills/ingest/stream-capture/SKILL.md)

**Retrieval:** [login](.cursor/skills/retrieval/login/SKILL.md) · [search](.cursor/skills/retrieval/search/SKILL.md) · [list-metadata](.cursor/skills/retrieval/list-metadata/SKILL.md) · [dashboard](.cursor/skills/retrieval/dashboard/SKILL.md) · [suggest-prompts](.cursor/skills/retrieval/suggest-prompts/SKILL.md) · [videos](.cursor/skills/retrieval/videos/SKILL.md) · [agent-qa](.cursor/skills/retrieval/agent-qa/SKILL.md) · [vastdb-read](.cursor/skills/retrieval/vastdb-read/SKILL.md)

Category overviews: [ingest](.cursor/skills/ingest/README.md) · [retrieval](.cursor/skills/retrieval/README.md)

## Quick start

1. Open the repo in Cursor (skills are picked up from `.cursor/skills/`).
2. Ask the agent to do something VSS-related — e.g. “upload these chunks”, “search for people near the entrance”, “how many videos are indexed”.
3. The agent loads the matching skill and follows its `SKILL.md`.

Most retrieval and streaming tasks need auth first — the agent should use the **login** skill before other backend calls.
