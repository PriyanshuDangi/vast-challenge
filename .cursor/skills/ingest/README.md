# Ingest (vss2)

For this hackathon, existing indexed videos are ingested only through re-ingest.
Direct upload, batch sync, and manual S3 copy are not supported workflows.

## Skills

| Skill | Mechanism | Purpose |
|-------|-----------|---------|
| [reingest-videos](reingest-videos/SKILL.md) | `POST /api/v1/dashboard/reingest` | Select an indexed video/stream, choose prompt and metadata behavior, choose latest complete chunks, and monitor replacement |
| [reingest-chunk](reingest-chunk/SKILL.md) | Explore/search → `POST /api/v1/dashboard/reingest` | Find one specific chunk from a filename, metadata, date, or scene description, then re-ingest only that Explore card |

The skill discovers all accessible indexed targets, requires explicit choices, and
re-runs complete chunks through detector → reasoner → embedder → writer. Writer
replacement is atomic per segment slot.

Auth is a backend JWT from `POST /api/v1/auth/login`.
