# Watchtower

Plain-English safety alerts over a VAST VSS video archive. An operator types a rule ("alert me when a person walks near a forklift"). An LLM compiles it into a structured rule. Archived footage is replayed segment by segment as if the cameras were live. Each segment is checked in two stages: a cheap filter on YOLO object counts and caption keywords, then an LLM judge. Matches become incidents (severity, reason, short report, clip) and can be pushed to a Discord or Slack webhook.

Saving a rule also backfills it across the archive with semantic search, so the operator sees how many times it would already have fired. No new video is ingested.

The service listens on `/` (local) and is published at `/app` behind the team ingress, which strips that prefix.

## Run locally

Offline, with fake cameras and a heuristic LLM:

```bash
MOCK=1 ./run_local.sh
```

Open `http://127.0.0.1:8080`.

Against the real archive, `./run_local.sh` loads the single `/config/*.config` in the environment (values are not printed) and starts the same server.

## Deploy

```bash
./deploy.sh
```

This installs a `python:3.12-slim` Deployment in the team namespace, mounts the app from a ConfigMap, reads credentials from a Secret, and exposes `http://<team-host>/app`. `./deploy.sh --dry-run` prints the manifests with secret values redacted and does not apply them.

`weave` (W&B tracing) is optional. The container runs `pip install -r requirements.txt || true`, so a missing package does not stop the server.

## Environment

| Variable | Role |
|---|---|
| `MOCK` | `1` or `true` uses fake VSS data and a fake LLM. No network. |
| `PORT` | Listen port. Default `8080`. |
| `DATA_DIR` | Where `rules.json` and `incidents.json` are stored. Default `./.data` locally, `/tmp/alert-builder` in the pod. |
| `VSS_URL`, `VSS_USERNAME`, `VSS_PASSWORD` | Retrieval API. Falls back to `INGRESS_URL`, `USERNAME`, `PASSWORD`, then `/config/*.config`. |
| `WANDB_API_KEY` | W&B Inference key. |
| `WANDB_PROJECT_PATH` | `team/project`. Else `WANDB_TEAM` / `WANDB_PROJECT`. |
| `LLM_MODEL` | Optional model id. Empty lets the client pick one. |
| `ALERT_WEBHOOK_URL` | Optional Discord or Slack incoming webhook. |

Rules and incidents survive restarts on disk. Deleting a rule keeps its incidents. A live match notifies the webhook in the background; backfill does not.

## Demo (about 2 minutes)

1. State the problem: nobody watches the archive, and search only helps after the fact.
2. Show the camera wall replaying warehouse, dashcam, and highway footage. Say plainly that this is archived footage replayed as a live feed.
3. Type "Alert me when a person walks near a forklift". Show the compiled rule and the line "would have fired N times".
4. The warehouse tile goes red. Open the incident: severity, reason, and written report. A webhook ping lands if one is configured.
5. Add a second rule for the dashcam ("a pedestrian steps into the road"). The same agent fires it. No code change.
6. Point at the decision log, and at W&B traces if tracing is on, so the filter-then-judge reasoning is visible.
