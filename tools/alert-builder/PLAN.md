# Watchtower: plain-English safety rules over the video archive

Operators type a rule in English. An LLM compiles it into a structured rule. Archived
footage is replayed segment by segment as if it were a live camera feed, and every rule is
checked against each segment in two stages: a cheap filter on YOLO counts and caption
keywords, then an LLM judge. Matches become incidents (clip, severity, reason, written
report) and are optionally pushed to a webhook. Saving a rule also "backfills" it over the
whole archive via search ("would have fired N times").

No new video is ingested. Everything comes from the team's existing VSS index.

## Constraints (apply to every file)

- Python 3.12 **standard library only** at runtime (`urllib`, `http.server`, `json`,
  `threading`, `concurrent.futures`). The pod is `python:3.12-slim` with no guaranteed pip.
  `weave` is optional: import inside `try`, never required.
- All files live **flat** in `tools/alert-builder/` (a Kubernetes ConfigMap cannot hold
  subdirectories). Tests are `test_*.py` in the same folder, `unittest`, runnable with
  `python3 -m unittest discover -s tools/alert-builder -p 'test_*.py'`. Tests must not
  touch the network.
- Never print, log, or commit secrets (passwords, JWTs, API keys). Never log request
  headers.
- `MOCK=1` runs the whole app offline with fake VSS data and a heuristic fake LLM, so the
  UI can be developed and demoed without network.
- The app is served behind Ingress at `/app` with the prefix stripped, and also at `/`
  locally. The frontend must use **relative** URLs via a computed base (see frontend).

## Data shapes (backend facts, verified in vss-blueprint source)

VSS backend base URL = `$INGRESS_URL`. Auth: `POST /api/v1/auth/login {username,password}`
-> `{access_token}`; send `Authorization: Bearer <jwt>`; on 401 re-login once.

- `GET /api/v1/videos/explore?scope=all&limit=<=100&offset=` -> `{chunks:[ChunkSearchResult], total, locations:[...]}`.
  ChunkSearchResult fields used: `original_video, filename, total_segments, chunk_duration_sec,
  preview_source, upload_timestamp, camera_id, capture_type, location`.
- `GET /api/v1/tools/segments?original_video=<s3 uri>` -> `{original_video,total,segments:[row]}`, rows
  ordered by segment_number with: `filename, source, reasoning_content, upload_timestamp,
  duration, segment_number, total_segments, segment_start_sec, segment_end_sec, camera_id,
  capture_type, location, object_classes` (comma-separated string), `object_counts`
  (JSON-encoded dict string like `{"person": 2, "truck": 1}`, may be `""`), `max_detection_conf`.
- `POST /api/v1/search {query, top_k, min_similarity, metadata_filters}` -> `{results:[VideoSearchResult]}`
  (same row fields as above plus `original_video`, `similarity_score`).
- `GET /api/v1/videos/detections?source=` -> YOLO bbox JSON, 404 = none.
- `GET /api/v1/videos/stream?source=<uri>&token=<jwt>` -> range-capable mp4.
- YOLO11 is COCO-trained: there is **no `forklift` class**. Things like forklift, vest,
  hard hat, pallet must be matched via caption keywords, not YOLO.

W&B Inference (OpenAI compatible): base `https://api.inference.wandb.ai/v1`,
headers `Authorization: Bearer $WANDB_API_KEY` and `OpenAI-Project: $WANDB_TEAM/$WANDB_PROJECT`.
`GET /models`, `POST /chat/completions`.

## Shared normalized types (plain dicts)

**Video**
```json
{"original_video": "s3://...", "filename": "...", "camera_id": "sdg_warehouse_cam-2",
 "location": "warehouse3", "capture_type": "...", "total_segments": 12,
 "duration_sec": 60.0, "upload_timestamp": "2026-09-01T12:00:00", "preview_source": "s3://..."}
```

**Segment**
```json
{"source": "s3://...seg.mp4", "original_video": "s3://...", "segment_number": 3,
 "start_sec": 15.0, "end_sec": 20.0, "caption": "<reasoning_content>",
 "object_counts": {"person": 2}, "object_classes": ["person"],
 "camera_id": "...", "location": "...", "capture_type": "...",
 "upload_timestamp": "iso string or ''", "similarity": null}
```
`object_counts` falls back to `{cls: 1 for cls in object_classes}` when counts are missing.

**Rule**
```json
{"id": "r_ab12cd", "name": "Person near forklift", "text": "<original english>",
 "cameras": [], "locations": [],
 "require_classes": {"person": 1},
 "caption_any": ["forklift", "pallet jack"],
 "search_query": "person walking near a moving forklift in a warehouse aisle",
 "judge_question": "Is a person within a few metres of a moving forklift?",
 "severity_guide": "5 = contact or imminent collision ... 1 = distant, no risk",
 "enabled": true, "created_at": "iso"}
```
Empty `cameras`/`locations` = all. `require_classes` keys must be COCO classes (lowercase);
every listed class must be present with count >= value. `caption_any` = lowercase keywords;
if non-empty, at least one must appear in the caption (case-insensitive, naive plural
handling: "forklift" matches "forklifts").

**Verdict**: `{"match": bool, "severity": 1-5, "confidence": 0.0-1.0, "reason": "one sentence"}`

**Incident**
```json
{"id": "i_...", "rule_id": "...", "rule_name": "...", "mode": "live|backfill",
 "source": "...", "original_video": "...", "segment_number": 3,
 "camera_id": "...", "location": "...", "start_sec": 15.0, "end_sec": 20.0,
 "upload_timestamp": "...", "caption": "...", "object_counts": {...},
 "severity": 4, "confidence": 0.8, "reason": "...", "report": "markdown",
 "created_at": "iso", "notified": null}
```
Deduplicate incidents on `(rule_id, source, mode)`, so a live replay still raises an
incident (and webhook) for a clip that backfill already found.

## Module contracts

### `config.py` (Agent A)
```python
@dataclass
class Settings:
    vss_url: str; vss_username: str; vss_password: str
    wandb_api_key: str; wandb_project_path: str   # "team/project" or ""
    llm_base_url: str = "https://api.inference.wandb.ai/v1"
    llm_model: str = ""          # env LLM_MODEL; "" = auto-pick
    webhook_url: str = ""        # env ALERT_WEBHOOK_URL
    port: int = 8080             # env PORT
    data_dir: str = "/tmp/alert-builder"   # env DATA_DIR
    mock: bool = False           # env MOCK in ("1","true")
def load_settings() -> Settings
```
Resolution order: `VSS_URL/VSS_USERNAME/VSS_PASSWORD`, else `INGRESS_URL/USERNAME/PASSWORD`,
else parse the single `/config/*.config` (KEY=VALUE, ignore comments, strip quotes) for the
missing keys. `wandb_project_path` = env `WANDB_PROJECT_PATH`, else `WANDB_TEAM/WANDB_PROJECT`.
`Settings.__repr__` must mask secrets.

### `vss_client.py` (Agent A)
```python
class VSSError(Exception): status: int | None
def normalize_segment(row: dict) -> dict          # -> Segment
def normalize_video(chunk: dict) -> dict          # -> Video
class VSSClient:
    def __init__(self, base_url, username, password, timeout=30)
    def list_videos(self) -> list[dict]           # paginate explore until total reached (cap 1000)
    def segments(self, original_video) -> list[dict]
    def search(self, query, top_k=20, min_similarity=0.25, metadata_filters=None) -> list[dict]  # Segments with similarity
    def detections(self, source) -> dict | None
    def open_stream(self, source, range_header=None) -> tuple[int, dict, "file-like"]  # status, selected headers (Content-Type, Content-Length, Content-Range, Accept-Ranges), body with .read(n) and .close()
    def ping(self) -> bool
```
Thread-safe token cache (lock), lazy login, one re-login retry on 401.

### `fixtures.py` (Agent A)
`class FakeVSSClient` with the same interface and deterministic data: 3 videos
(warehouse `sdg_warehouse_cam-2`/`warehouse3`, dashcam `pie_cam-3`/`toronto`,
highway `i24_cam-1`/`nashville`), 10-14 segments each, realistic captions. The warehouse
video must include several segments where a person walks near a moving forklift (caption
mentions forklift; YOLO counts only `person`), the dashcam one pedestrians stepping into
the road, the highway one trucks changing lanes. `search` = keyword overlap scoring.
`open_stream` returns `(404, {}, empty body)`.

### `smoke.py` (Agent A)
CLI `python3 smoke.py` against the real stack: login ok, number of videos per camera,
segments + sample caption/object_counts for the first warehouse video, distinct YOLO classes
seen, LLM model list reachable and a 1-line chat works. Prints no secrets.

### `llm.py` (Agent B)
```python
class LLMError(Exception)
class LLMClient:
    def __init__(self, base_url, api_key, project_path, model="", timeout=60)
    def model(self) -> str     # if empty: GET /models, choose first available from a preference
                               # list (Qwen/Qwen3-235B-A22B-Instruct-2507, meta-llama/Llama-3.3-70B-Instruct,
                               # openai/gpt-oss-120b, meta-llama/Llama-3.1-8B-Instruct), else first listed
    def chat(self, messages, temperature=0.1, max_tokens=800) -> str
    def chat_json(self, messages, temperature=0.0, max_tokens=800) -> dict  # strips <think>..</think>
                               # and ``` fences, extracts first {...}; one retry with a "JSON only" nudge
class FakeLLM:  # same interface; heuristic answers for MOCK mode, keyed off prompt content
def init_tracing(project_path, api_key) -> bool   # weave.init if importable, else False
def traced(fn)                                     # weave.op if tracing active, else identity
```

### `rules.py` (Agent B)
```python
COCO_CLASSES: list[str]
def new_rule_id() -> str
def compile_rule(llm, text, vocab: dict) -> dict     # vocab = {"classes":[...], "cameras":[...], "locations":[...]}
def heuristic_compile(text, vocab) -> dict            # used when the LLM fails
def validate_rule(rule: dict, vocab: dict) -> dict    # non-COCO classes move to caption_any; unknown cameras dropped; defaults filled
def prefilter(rule, segment) -> tuple[bool, str]      # reason e.g. "no forklift in caption", "person 0 < 1", "camera out of scope"
```
The compile prompt must explain that YOLO only knows COCO classes, ask for synonyms in
`caption_any`, and return the Rule JSON fields.

### `judge.py` (Agent B)
```python
def judge(llm, rule, segment) -> dict                 # Verdict; uses caption + object_counts + camera/time
def write_report(llm, rule, segment, verdict) -> str  # short markdown incident report (what, where, when, risk, recommended action)
class Evaluator:
    def __init__(self, llm, max_workers=4)
    def evaluate(self, rules, segment) -> list[dict]  # per enabled rule: {"rule_id","rule_name","prefilter_pass","prefilter_reason","verdict"|None}
                                                       # cached on (rule_id, source); judge only runs when prefilter passes
```
Judge failures produce `verdict=None` with the error in `prefilter_reason`; never raise.

### `store.py` (Agent C)
```python
class Store:
    def __init__(self, data_dir)                      # loads/saves rules.json, incidents.json atomically; thread-safe
    def list_rules(self) / get_rule(id) / save_rule(rule) / update_rule(id, **fields) / delete_rule(id)
    def list_incidents(self) -> list  (newest first)
    def add_incident(self, incident) -> dict | None   # None if duplicate (rule_id, source)
    def clear_incidents(self)
def make_incident(rule, segment, verdict, report, mode) -> dict
def notify(webhook_url, incident, on_done=None)       # background thread; JSON {"content": text, "text": text}; swallow errors
```

### `main.py` (Agent C)
`ThreadingHTTPServer` on `0.0.0.0:$PORT`. Builds real or fake clients from `Settings.mock`.
Caches: videos (5 min), segments per original_video (10 min).

| Route | Behaviour |
|---|---|
| `GET /`, `/index.html`, `/app.js`, `/styles.css` | static files from the script directory |
| `GET /health` | `{"ok": true}` |
| `GET /api/status` | `{"mock", "vss_ok", "llm_model", "webhook_configured", "tracing"}` |
| `GET /api/videos` | `[Video]` |
| `GET /api/vocab` | `{"classes": COCO_CLASSES, "cameras": [...], "locations": [...]}` from videos |
| `GET /api/segments?original_video=` | `[Segment]` |
| `GET /api/rules` | `[Rule]` |
| `POST /api/rules/preview {text}` | compiled Rule, not saved |
| `POST /api/rules {text}` or `{rule}` | compile (or validate given rule), save, return Rule |
| `PATCH /api/rules/<id> {enabled}` | updated Rule |
| `DELETE /api/rules/<id>` | `{"ok": true}` |
| `POST /api/rules/<id>/backfill {limit=12}` | search `rule.search_query` (scoped by camera/location filters when exactly one), prefilter + judge top hits, add incidents with mode `backfill`; returns `{"checked","matched","incidents":[...],"hits":[{"original_video","segment_number","matched"}]}` |
| `POST /api/evaluate {original_video, segment_number}` | evaluate all enabled rules on that segment; new incidents (mode `live`) get a report + webhook; returns `{"segment": Segment, "results": [...], "incidents": [...]}` |
| `GET /api/incidents` / `DELETE /api/incidents` | list / clear |
| `GET /api/stream?source=` | proxy `open_stream` forwarding `Range`, streaming in 64 KiB chunks (JWT never reaches the browser) |
| `GET /api/detections?source=` | passthrough or `null` |

Errors return JSON `{"error": "..."}` with a sensible status. Client disconnects during
streaming must not crash the server.

### Frontend: `index.html`, `app.js`, `styles.css` (Agent D)
Vanilla HTML/CSS/JS, no frameworks, no CDN. Dark "control room" look.
- Base path: first script in `<head>` computes `BASE = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/'`
  and every fetch / asset / video URL is `BASE + 'api/...'`. Load `styles.css` and `app.js` relative to BASE.
- **Header**: product name, tagline, status pills from `/api/status` (MOCK, LLM model, webhook).
- **Left: Rule builder**: textarea + example chips ("Alert me when a person walks near a forklift",
  "Alert me when a pedestrian steps into the road in front of the car", "Alert me when a truck changes lanes in dense traffic").
  "Preview" shows the compiled rule as readable chips (scope, YOLO needs, caption keywords, judge question).
  "Activate" saves, then runs backfill and shows "Would have fired N times in the archive".
  Active rules list with enable toggle, delete, and a backfill-result line.
- **Center: Camera wall**: 3 tiles. Each tile: video picker (grouped by camera_id/location), start
  segment, play/pause, seconds-per-segment (default 4). Plays the segment clip muted via `api/stream`;
  if the video fails, shows a caption card instead. Overlays: camera id, footage clock
  (upload date + start_sec as HH:MM:SS), YOLO count chips, caption ticker. On each step POST
  `api/evaluate`; tile border states: checking (blue), prefilter passed / judging (amber), ALERT (red
  flashing + severity badge). "Jump to next event" uses the latest backfill `hits` for that tile's
  video to start two segments before a match.
- **Right: Incident inbox**: newest first, severity colour, rule name, camera, footage time, reason,
  live/backfill tag, notified tick. Click opens a modal with the clip player, report (minimal markdown:
  headings, bold, bullets), caption, YOLO counts. "Clear" button.
- **Bottom: Decision log**: one line per rule per segment ("seg 7 cam-2 | Person near forklift |
  filter: no forklift in caption" / "judge: MATCH sev 4 - person crossing aisle ahead of forklift").
  This makes the agent's reasoning visible in the demo.
- Toast + optional beep (WebAudio) when a new incident arrives.

### Ops: `run_local.sh`, `deploy.sh`, `requirements.txt`, `README.md` (Agent C)
- `run_local.sh`: `set -a; source` the single `/config/*.config`; `python3 main.py`. `MOCK=1 ./run_local.sh` for offline.
- `deploy.sh`: follows `.cursor/skills/deployment/deploy-app-no-registry` exactly. `APP_NAME=alert-builder`,
  namespace `$USERNAME`, ConfigMap from explicit `--from-file` of runtime files only (no tests, no PLAN/README),
  Secret with `VSS_URL, VSS_USERNAME, VSS_PASSWORD, WANDB_API_KEY, WANDB_PROJECT_PATH`, optional
  `ALERT_WEBHOOK_URL`, `LLM_MODEL`; env `DATA_DIR=/tmp/alert-builder`; `pip install -r requirements.txt || true`;
  Ingress host from `$INGRESS_URL`, path `/app(/|$)(.*)` with rewrite; rollout restart after ConfigMap update;
  prints the final URL. Never echoes secrets.
- `requirements.txt`: only `weave` (optional).
- `README.md`: what it is, run locally, mock mode, deploy, demo script.

## Demo script (2 minutes)
1. Problem: nobody watches the archive; search only helps after the fact.
2. Camera wall replaying warehouse, dashcam and highway footage (state plainly it's archived footage replayed live).
3. Type "Alert me when a person walks near a forklift" -> compiled rule -> "would have fired N times".
4. Warehouse tile goes red, incident with severity, reason, report; webhook ping if reachable.
5. Second rule on the dashcam tile fires too: same agent, no code changes.
6. Decision log and W&B traces show the reasoning.

## Work split
- **Agent A**: `config.py`, `vss_client.py`, `fixtures.py`, `smoke.py`, `test_vss_client.py`
- **Agent B**: `llm.py`, `rules.py`, `judge.py`, `test_rules.py`, `test_judge.py`
- **Agent C**: `store.py`, `main.py`, `run_local.sh`, `deploy.sh`, `requirements.txt`, `README.md`, `test_main.py`
- **Agent D**: `index.html`, `app.js`, `styles.css`
- **Integrator** (after): run all tests, `MOCK=1` end-to-end, then real stack smoke test, deploy.
