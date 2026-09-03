# VSS Hackathon Guidelines

Ten teams. Ten isolated **VAST Video Search System (VSS)** stacks. One shared idea: use video understanding + search + metadata to build something useful.

Each team gets its own pipeline, storage, UI, and credentials (`/config/<your-team>.config` on the VM). Your data stays in your namespace — you are not competing for someone else’s archive.

Each team gets a git repo with the **Cursor skills** for VSS.

- The VM provides `/config/<your-team>.config`, `/config/kubeconfig`, `/config/vss-cli-secret.yaml`, and `/config/backend-secret.yaml`. Keep them outside the repository.

---

## Models (NVIDIA on CoreWeave)

Inference for the pipeline runs on shared **GPU endpoints** (NVIDIA Cosmos + YOLO + Canary). You don’t deploy the models yourself.

| Model | Role in VSS | Endpoint |
|-------|-------------|----------|
| **NVIDIA Cosmos Reason2** (`nvidia/cosmos-reason2-8b`) | Video understanding / reasoning over segments | `http://166.19.38.112:8001` |
| **YOLO11** (`yolo11s`, Ultralytics) | Object detection (bounding boxes / labels) | `http://166.19.38.112:8002` |
| **NVIDIA Cosmos Embed1** (`nvidia/cosmos-embed1`) | Text and visual embeddings (256-dim) for hybrid search | `http://166.19.38.112:8003` |
| **NVIDIA Canary-1B** (`nvidia/canary-1b`) | Speech-to-text (ASR) + speech translation — **not** Cosmos; NeMo audio model | `http://166.19.38.112:8004` |

Host: `166.19.38.112`. The bearer token is in `/config/<team>.config` (`GPU_BEARER_TOKEN`). Ask Cursor / see `.cursor/skills/gpu/` for how to call each model.

Ingest and search call **Reason2**, **YOLO11**, and **Embed1** through your pipeline and backend today.

**Canary-1B** is on the same GPU host (`:8004`) but is **not wired into the current VSS pipeline**. If you want audio transcripts, spoken-word search, or speech translation in your demo — let your imagination go and hook it in. **Ask Cursor how to use Canary-1B** against your stack and skills.

---

## Data Engine

### VSS Blueprint

DataEngine url UI:
https://10.146.15.201/#/login/builder-series-poc

Your team’s ingest runs as a **VAST DataEngine** serverless pipeline. A video chunk lands in S3, then functions run in sequence until searchable rows exist in VastDB.

```
S3 chunks (bucket)  →  Segmenter  →  S3 segments (bucket)
                                           ↓
                                        Detector → Reasoner → Embedder → VastDB writer
```

A separate **events / prompt-suggester** function runs on a schedule and feeds UI suggestions.

![VAST DataEngine pipeline](docs/hackathon/vss-pipeline.png)

**VAST DataEngine (pipeline’s tab)** — there you can edit the pipeline and see the pipeline’s flow, logs, traces, etc.

| Function | What it does |
|----------|--------------|
| **Segmenter** | Splits each uploaded chunk into short fixed-length clips and writes them to the segments bucket. **Builders challenge:** organizers already used the Segmenter to pre-ingest your corpus. During the challenge you **only re-ingest** data that is already segmented and indexed — the Segmenter is **not** in the path you run. |
| **Detector** | Runs **YOLO11** on each segment and records object classes, counts, and bbox sidecars. |
| **Reasoner** | Calls **NVIDIA Cosmos Reason2** to write a searchable natural-language description of the segment. |
| **Embedder** | Calls **NVIDIA Cosmos Embed1** to build text (and visual) vectors for hybrid search. |
| **VastDB writer** | Persists embeddings, reasoning, detections, and metadata as a row in your VastDB collection. |
| **Events (prompt-suggester)** | Periodically scans recent segments and writes suggested search prompts / key events for the UI. |

**Builders challenge note:** your archive is **pre-ingested** (Segmenter already ran). Your live path is **re-ingest** on existing segments: Detector → Reasoner → Embedder → VastDB writer (via `ingest/reingest-videos` / `reingest-chunk`). You don’t upload new chunks or invoke the Segmenter for this challenge.

You don’t need to redeploy this graph for the hackathon — treat it as the engine behind search, dashboard, suggestions, and re-ingest.

---

## What you were given

For your team you already have:


| Piece | What it is |
|-------|------------|
| **Ingest pipeline** | DataEngine graph that pre-ingested your corpus (Segmenter already ran). During the challenge you **re-ingest** existing segments (detect → reason → embed → write VastDB) |
| **UI** | Web app at your `INGRESS_URL` (frontend + backend) |
| **S3 buckets** | Chunks + segments for uploads |
| **VastDB** | Indexed segments, embeddings, detections, reasoning text |
| **This repo in Cursor** | Agent **skills** that know how to call every important API — open this project in Cursor and describe what you want |
| **Source code repo** | Full VSS Blueprint (`vss-blueprint`) — pipeline functions, backend, frontend, deployments. Clone it if you want to dig in; open it in Cursor to **view / change / update / add** anything for your use case |

```bash
cd /vss-blueprint
```

Cloning or changing the Blueprint is **not a requirement**. The live UI, APIs, and Cursor skills in this repo are enough for a strong demo — search, filters, re-ingest, dashboards, mini-apps on top of the archive. Dig into the source only if you want custom pipeline/UI behavior.

Open the UI, log in with your team user, and open **this skills repo** in **Cursor** (add the source repo only if you want to modify the stack). Skills are how you move fast against the live APIs.

---

## VSS UI

1. Go to your **Ingress URL** (`INGRESS_URL` in your team config).
2. **Log in** with your team’s username and password (`USERNAME` / `PASSWORD` in `/config/<your-team>.config`).

![VSS login](docs/hackathon/vss-login.png)

*Log in with your team’s username.*

3. Use the **Search** tab to query the archive.

![VSS search tab](docs/hackathon/vss-search-tab.png)

*The Search tab.*

4. Open the **Dashboard** tab to inspect ingest health, object counts, and pipeline alignment.

![VSS dashboard](docs/hackathon/vss-dasboard-tab.png)

*The VSS dashboard.*

Screenshots live in `docs/hackathon/`.

---

## The challenge

**Discover. Explore. Invent. Build.**

1. **Discover the skills** — ask Cursor what VSS can do; let it find and use the skills under `.cursor/skills/`.
2. **Explore the product** — search, filter by metadata (`city`, `camera_id`, `scenario`, `category`), try suggestions, open the dashboard, explore clips, ask the agent. Optionally open the Blueprint source and see how the pipeline and UI are built.
3. **Pick a use case** from the video corpus below (or combine groups) — traffic, crowds, egocentric / robotics, warehouse safety, driving, NYC street safety.
4. **Build something** — a workflow, a mini-app, a Cursor-driven demo, a report pipeline, a filtered “ops board”, a Q&A bot for your scenario. Stay on the live APIs and skills, **or** change the Blueprint (pipeline functions, prompts, backend, UI) when your use case needs it.

Judges care about **clarity of the use case**, **clever use of search + metadata + features** (and optional pipeline/UI changes), and **something that actually runs** — not how many slides you write.

---

## Video corpus (already indexed)

Shared lab MP4s land in object storage as:

```text
s3://videos-source/<folder>/<file>.mp4
```

Each `<folder>` is one source (one camera / POV). Files are already time-chunked (`name_0.mp4`, `name_1.mp4`, …). For the hackathon, treat them as **already in your team’s VSS archive** — explore and **re-ingest** with the prompt/metadata you need (`ingest/reingest-videos` / `reingest-chunk`).

**One pipeline, one VastDB index, several operational lenses.** Only `scenario` + metadata change. Anchor demo line:

> *Show me every clip, from any camera in any city, where a person is close to a moving vehicle*

That should pull Bangkok traffic, NYC/SF driving, and warehouse forklift POV into one result set.

Useful metadata on these objects: `scenario`, `city`, `camera_id`, `category`, `chunk_index`. Built-in scenarios include `surveillance`, `traffic`, `retail`, `warehouse`, `egocentric`, `sports`, `nhl`, `general`.

### Use-case groups

#### 1. Vehicle & Pedestrian Monitoring — `traffic` / `surveillance`

| Folder (`s3://videos-source/<folder>/…`) | City | Category | `camera_id` |
|------------------------------------------|------|----------|-------------|
| `bangkok_intersection2` | bangkok | Traffic | `bangkok_cam-1` |
| `bankgog_intersection1` | bangkok | Traffic | `bangkok_cam-2` |
| `dublin_surv_cam` | dublin | Crowds | `surveillance_1` |
| `london_surv_cam` | london | Crowds | `london_surveillance_1` |

Fixed street/intersection cams. Story: congestion + pedestrian-density search across Bangkok and European CCTV. Try: *“Motorbikes on a Bangkok arterial”*, *“pedestrians on the zebra crossing while a bus waits”*, *“busiest minute in Dublin.”*

#### 2. First-Person Activity Analysis — `egocentric` (Robotics)

| Folder | City | Category | `camera_id` |
|--------|------|----------|-------------|
| `barista1` | nyc | Robotics | `barista_pov_1` |
| `barista2` | nyc | Robotics | `barista_pov_2` |
| `chef_cooking` | san-jose | Robotics | `cook_pov1` |
| `sushi_pov` | nyc | Robotics | `sushi_pov_1` |
| `forklift_1` | warehouse1 | Robotics | `forklift_pov_1` |

Head-mounted / POV hands-at-work. Story: task & SOP analysis and robot-learning data. Try: *“When was milk steamed?”*, *“find the knife-on-fish moment”*, *“forklift approaching a pallet.”* Two barista POVs give busy-vs-calm on the same prompt.

#### 3. Warehouse Safety & Operations — `warehouse` / `egocentric`

| Folder | City | Category | `camera_id` |
|--------|------|----------|-------------|
| `warehouse1` | warehouse | Robotics | `warehouse_cam1` |
| `parking-cam-private` | oregon | Streets | `parking_cam_1` |
| `forklift_2` | warehouse | Robotics | `forklift_pov_2` |
| `forklift_3` | warehouse | Robotics | `forklift_pov_3` |

Industrial **safety & near-miss** search — *“forklift near a person in an aisle”*, *“tight trailer squeeze”*, *“PPE missing.”*  
`parking-cam-private` is **real private footage** — keep it **internal only** (not for public decks).

#### 4. Live Driving & Road Safety — `traffic` / `egocentric`

| Folder | City | Category | `camera_id` |
|--------|------|----------|-------------|
| `driving_nyc` | nyc | Traffic | `nyc_driver_1` |
| `driving_sf` | sanfrancisco | Traffic | `sf_driver_1` |

Dashcam/hood POV — Manhattan vs quiet Sunday San Francisco. Try: *“Cross a busy intersection with pedestrians”*, *“hill descent with parked cars.”*

#### 5. NYC Street Safety Surveillance — `surveillance` / `egocentric`

| Folder | City | Category | `camera_id` |
|--------|------|----------|-------------|
| `nyc_surv_cam` | nyc | Crowds | `nyc_surveillance_2` |
| `walking_cam_nyc` | nyc | Crowds | `nyc_walk_1` |

Overhead Times Square + street-level Manhattan walk — **overhead vs street-level** on the same city. Try: *“Find Times Square in the walking tour”*, *“the moment the plaza packed.”*

### Cross-group demos (the payoff)

| Query | Hits across groups |
|-------|--------------------|
| *“person close to a moving vehicle”* | Bangkok traffic (1) + NYC/SF driving (4) + forklift POV (2/3) |
| *“dense crowd of pedestrians”* | London/Dublin CCTV (1) + Times Square + NYC walk (5) |
| *“hands performing a repetitive task”* | barista / sushi / cook / forklift POV (2) |
| *“same city, different camera”* | NYC: `nyc_surveillance_2` + `nyc_walk_1` + `nyc_driver_1` |

### Recommended demo packs

Don’t try to demo everything at once. Start with a short pack, prove search → filters → UI, then expand.

| Pack | Folders under `s3://videos-source/` | Story |
|------|-------------------------------------|-------|
| **A — Vehicle & Pedestrian** | `bangkok_intersection2`, `bankgog_intersection1`, `dublin_surv_cam`, `london_surv_cam` | Traffic + crowd CCTV across cities |
| **B — First-Person / Robotics** | `barista1`, `barista2`, `chef_cooking`, `sushi_pov`, `forklift_1` | Egocentric task analysis |
| **C — Warehouse Safety** | `warehouse1`, `forklift_2`, `forklift_3`, `parking-cam-private` | Near-miss + PPE (keep private cam internal) |
| **D — Driving** | `driving_nyc`, `driving_sf` | Road-scene understanding |
| **E — NYC Street Safety** | `nyc_surv_cam`, `walking_cam_nyc` | Overhead vs street-level |

Start with **Pack A (Vehicle & Pedestrian)** or **Pack C (Warehouse Safety)** — short and high-signal — before leaning on the large NYC walk (`walking_cam_nyc`).

### Example queries

- *“Yellow taxi near a dense crowd”* → `nyc_surveillance_2` + `nyc_walk_1`
- *“Motorbikes on a Bangkok arterial”* → `bangkok_cam-1` / `bangkok_cam-2`
- *“Person on a zebra crossing with a bus waiting”* → `london_surveillance_1`
- *“Forklift approaching a person in a warehouse aisle”* → `forklift_pov_2` + `warehouse_cam1`
- *“Barista pouring two drinks with a line behind”* → `barista_pov_1`
- *“Vehicle entering a private driveway at night”* → `parking_cam_1` (**internal only**)

---

## You can do everything from Cursor (skills)

You do **not** need to memorize REST paths. In Cursor, describe what you want; the agent should pick the matching skill.

Useful skills (start here):


| Skill                       | Use it for                                                |
| --------------------------- | --------------------------------------------------------- |
| `retrieval/login`           | Get a JWT with your team username/password                |
| `retrieval/list-metadata`   | Discover filterable fields and legal values               |
| `retrieval/search`          | Semantic / hybrid search + optional LLM synthesis         |
| `retrieval/suggest-prompts` | AI-generated search chips & key events                    |
| `retrieval/dashboard`       | Counts, quality, objects, ingest vs index health          |
| `retrieval/agent-qa`        | Natural-language Q&A grounded in the archive              |
| `ingest/reingest-videos`    | Re-ingest an indexed video/stream (hackathon ingest path) |
| `ingest/reingest-chunk`     | Re-ingest one specific Explore card / chunk               |


And a lot more :)  
Ask Cursor to discover the skills. He’s your best friend 🙂

Full skill index: `[.cursor/README.md](.cursor/README.md)`

---

## Suggested playbook

Write full prompts in Cursor: name what you want, point at your team config, and say what to build. A solid loop is **re-ingest → confirm indexing → ship a thin app on search / videos / dashboard**.

### Example prompts (adapt to your use case)

**1. Re-ingest and verify indexing**

> Re-ingest an indexed video from Pack A (Vehicle & Pedestrian) or Pack C (Warehouse Safety) in my team environment (credentials in `/config/<my-team>.config`).
> Let me pick the target (e.g. `bangkok_intersection2` or `forklift_2`), prompt/metadata behavior, and chunk count.  
> Wait until re-ingest finishes, then run basic sanity checks on counts and pipeline health.  
> *(Cursor will use skills: `ingest/reingest-videos`, `retrieval/dashboard`, `retrieval/login`)*

**2. Cross-camera “person near vehicle” board**

> Build a small webpage that answers: *“person close to a moving vehicle”* across my archive.  
> Group hits by city / `camera_id` (Bangkok traffic, NYC/SF driving, warehouse forklift).  
> For each hit, show a triptych: one clip **before**, the **event**, and one clip **after** (~5 seconds each).  
> Serve it with a local proxy so browser CORS is handled, verify it end-to-end, and save it under `tools/`.  
> *(Cursor will use skills: `retrieval/login`, `retrieval/search`, `retrieval/videos`, `retrieval/list-metadata`)*

**3. Warehouse safety ops board**

> Build a standalone page for Pack C: histogram of top detected objects + a list of near-miss style hits  
> (forklift near a person, tight aisle, missing PPE). For each object, show a small bounding-box crop from a real segment.  
> Filter by `camera_id` / location. Save under `tools/`, open the page, and report the top findings.  
> Keep `parking-cam-private` out of any public demo.  
> *(Cursor will use skills: `retrieval/login`, `retrieval/dashboard`, `retrieval/search`, `retrieval/videos`)*

### Your turn

1. Open your `INGRESS_URL` and log in with the team user from your config file.
2. Pick a **demo pack** from the table above (good first picks: **Vehicle & Pedestrian** or **Warehouse Safety**), search with the example queries, then re-ingest with the prompt/metadata you need.
3. Ask Cursor to discover skills, then write prompts in this style for **your** scenario.
4. Ship something that runs: search + metadata (or detections / dashboard / agent) in a small vertical — not slides.

---

## Rules of the road

- Stay in **your** team credentials, buckets, and UI. Don’t poke other teams’ namespaces.
- Prefer **Cursor + skills** over hand-copying curl forever — but reading a skill once to understand the API is encouraged.
- Don’t burn the whole hackathon redeploying infrastructure; the stack is already up.
- If search returns nothing: check login, check dashboard/pipeline alignment, then check that metadata filter values actually exist (`retrieval/list-metadata`).
- Have fun — the win is a crisp story: *problem → video archive (one of the packs) → search/filters across cameras → insight or action*.
- Keep `parking-cam-private` / `parking_cam_1` **internal only**.

---

## One-line summary

You have a private VSS: indexed video corpus → understand it → search it with language and metadata across cities/cameras → explain it with an agent and a dashboard. **Use Cursor to discover the skills, pick a demo pack, and make a use case real.**