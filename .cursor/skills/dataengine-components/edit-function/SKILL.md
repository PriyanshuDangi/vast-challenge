---
name: dataengine-edit-function
description: >-
  Author or edit VSS DataEngine serverless function code (init/handler, VastEvent,
  vss2-secret Settings, VastDB SDK, OpenTelemetry, requirements.txt). Use when
  creating or changing the code of an ingest/enrichment function before building
  and registering it.
---

# Edit DataEngine function code (vss2)

For the function **code** itself. After editing, build (`dataengine-build-function`) and register (`dataengine-functions`). Follow existing functions under `source-code/ingest/*` and `source-code/enrichment/*` before inventing structure.

## Layout

```
<function-name>/
├── main.py              # init(ctx), handler(ctx, event: VastEvent)
├── requirements.txt     # standard stack + function-specific deps
├── README.md            # secret keys, deploy notes
└── common/
    ├── models.py        # Settings.from_ctx_secrets, event/result models
    ├── vastdb_client.py # VastDB connect, select/insert (if needed)
    ├── vastdb_patch.py  # vector-column select patch (if reading collection)
    └── …                # domain logic (LLM/NIM client, parsers, etc.)
```

## Entry points

```python
from opentelemetry import trace
from vast_runtime.vast_event import VastEvent  # type: ignore

def init(ctx):
    with ctx.tracer.start_as_current_span("<Name> Initialization"):
        settings = Settings.from_ctx_secrets(ctx.secrets)
        ctx.settings = settings
        ctx.vastdb_client = VastDBClient(settings)  # if VastDB
        ctx.logger.info("[INIT] …")

def handler(ctx, event: VastEvent):
    with ctx.tracer.start_as_current_span("<Name> Handler") as handler_span:
        try:
            data = event.get_data()
            event_type = getattr(event, "get_type", lambda: "element_trigger")()
            # pipeline: honor data.get("status") == "error"|"skipped" like vastdb-writer
            return {"status": "success", "...": "..."}
        except Exception as e:
            handler_span.set_attribute("error", True)
            handler_span.record_exception(e)
            ctx.logger.error(f"…: {e}")
            return {"status": "error", "error": str(e)}
```

- **Pipeline functions**: default `event_type` `element_trigger`; parse `event.get_data()` from upstream.
- **Scheduled enrichment** (prompt-suggester): default `scheduled_trigger`; may ignore empty `data`; nest spans per phase (fetch → compute → VastDB write).
- Errors are **returned**, not raised (`{"status":"error"|"skipped"}`) — so DataEngine retries/dead-letter only fire on hard failures (crash/OOM/timeout).

## Settings (`common/models.py`)

- Load from `secrets["vss2-secret"]` only.
- Field names match secret keys (lowercase, no underscores): `vdbendpoint`, `vdbcollection`, `cosmos_host`, `embeddinghost`, `yolo_infer_host`, …

```python
@classmethod
def from_ctx_secrets(cls, secrets: Dict[str, Any]) -> "Settings":
    raw = secrets["vss2-secret"]
    config = {k: raw[k] for k in cls.__annotations__ if k in raw}
    return cls(**config)
```

New keys → add to the secret templates (see `dataengine-secret-manifest`).

## Standard `requirements.txt`

Start **every** function with this block verbatim (don't drop lines):

```
cloudevents==1.10.1
vastdb==1.3.2
ibis-framework[duckdb]==9.0.0
pyarrow
pydantic==2.5.2
pydantic-settings==2.1.0
opentelemetry-api==1.38
opentelemetry-sdk==1.38
opentelemetry-exporter-otlp==1.38
opentelemetry-processor-baggage==0.59b0
```

Add only what the function needs: S3 → `boto3`/`botocore`; HTTP/LLM/NIM → `requests==2.31.0`. Avoid `httpx`, unpinned `vastdb`, `pandas` unless required.

## VastDB reads on `vss-collection`

Tables include `vectors` / `vectors_visual`. Import `common/vastdb_patch.py` before `table.select()` so vector columns are excluded from projections. Use `vastdb.connect(endpoint=…, access=…, secret=…, ssl_verify=False)`; prefer `arrow.to_pylist()`. Log `[VASTDB]` / `[COMPLETE]`.

## Reference implementations

| Type | Example |
|------|---------|
| Pipeline write | `source-code/ingest/vastdb-writer/` |
| Pipeline LLM (VLM) | `source-code/ingest/video-reasoner/` |
| Pipeline detector | `source-code/ingest/video-detector/` |
| Scheduled enrichment | `source-code/enrichment/prompt-suggester/` |

## Checklist for a new function

1. Copy layout + standard `requirements.txt` (incl. `opentelemetry-processor-baggage==0.59b0`).
2. `Settings` + secret template keys (`dataengine-secret-manifest`).
3. `init` stores clients on `ctx`; `handler` uses spans + structured return dict.
4. VastDB patch if selecting from the collection table.
5. Build (`dataengine-build-function`) → register (`dataengine-functions`) → wire (`dataengine-pipeline-manifest`).
