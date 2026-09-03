---
name: gpu-model-health
description: >-
  Check liveness/readiness of VSS GPU models using the paths that each NIM actually
  exposes (Reason2/Embed1: /v1/models + /v1/health/*; YOLO: /healthz; Canary:
  /v1/health/ready|live only). Config: team-configs/gpu-endpoints.config.
---

# GPU: model health (vss2)

Config (required): [`team-configs/gpu-endpoints.config`](../../../../team-configs/gpu-endpoints.config)

If `GPU_BEARER_TOKEN` is `<fill_me>`/empty → copy from `team-configs/<team>.config`; if still missing → ask the user. Do not invent the token.

```bash
set -a && source team-configs/gpu-endpoints.config && set +a
AUTH=(-H "Authorization: Bearer ${GPU_BEARER_TOKEN}")
```

**Do not** probe the same paths on every model. These NIMs differ — use the matrix below (verified on the hackathon host).

## Per-model health matrix (source of truth)

| Model | URL env | Health checks that work | Do **not** use (404 here) |
|-------|---------|-------------------------|---------------------------|
| **Reason2** | `$COSMOS_REASON2_URL` | `GET /v1/models` → 200; `GET /v1/health/ready` → 200; `GET /v1/health/live` → 200 | `/v1/health`, `/health` |
| **YOLO11** | `$YOLO_URL` | `GET /healthz` → 200 (`ok`, `model_loaded`); optional `GET /openapi.json` / `/docs` | `/v1/models`, `/v1/health/ready`, `/v1/health/live` |
| **Embed1** | `$COSMOS_EMBED1_URL` | `GET /v1/models` → 200; `GET /v1/health/ready` → 200; `GET /v1/health/live` → 200 | `/v1/health`, `/health` |
| **Canary-1B** | `$CANARY_1B_URL` | `GET /v1/health/ready` → 200; `GET /v1/health/live` → 200; optional `GET /openapi.json` / `/docs` | `/v1/models`, `/healthz`, `/v1/health` |

A **404 on an unsupported path is not a failure** for that model. Only fail health if the **supported** checks above are non-200 (or connection error).

## Commands (copy/paste)

### Reason2 + Embed1 (OpenAI/NIM-style)

```bash
for ep in "$COSMOS_REASON2_URL" "$COSMOS_EMBED1_URL"; do
  echo "== $ep =="
  curl -s -o /dev/null -w "models:%{http_code} " "${AUTH[@]}" "$ep/v1/models"
  curl -s -o /dev/null -w "ready:%{http_code} " "${AUTH[@]}" "$ep/v1/health/ready"
  curl -s -o /dev/null -w "live:%{http_code}\n" "${AUTH[@]}" "$ep/v1/health/live"
done
# Expect all three codes = 200 for each.
curl -s "${AUTH[@]}" "$COSMOS_REASON2_URL/v1/models" | python3 -m json.tool   # id ~ cosmos-reason2
curl -s "${AUTH[@]}" "$COSMOS_EMBED1_URL/v1/models" | python3 -m json.tool    # id ~ cosmos-embed1
```

### YOLO11 (custom FastAPI — `/healthz` only)

```bash
curl -s "${AUTH[@]}" "$YOLO_URL/healthz" | python3 -m json.tool
# Expect: "ok": true, "model_loaded": true, "cuda_available": true
# Infer route exists (405 on GET is fine):
curl -s -o /dev/null -w "infer_GET:%{http_code}\n" "${AUTH[@]}" "$YOLO_URL/v1/infer"   # expect 405
```

### Canary-1B (Riva HTTP — ready/live only; no `/v1/models`)

```bash
curl -s "${AUTH[@]}" "$CANARY_1B_URL/v1/health/ready" | python3 -m json.tool   # status ready
curl -s "${AUTH[@]}" "$CANARY_1B_URL/v1/health/live"  | python3 -m json.tool   # status live
# Optional API surface:
curl -s -o /dev/null -w "openapi:%{http_code} docs:%{http_code}\n" \
  "${AUTH[@]}" "$CANARY_1B_URL/openapi.json" "${AUTH[@]}" "$CANARY_1B_URL/docs"
# Do NOT treat /v1/models 404 as down.
```

## Pass / fail rules

| Model | Pass when |
|-------|-----------|
| Reason2 / Embed1 | `/v1/models` + `/v1/health/ready` + `/v1/health/live` all 200 |
| YOLO | `/healthz` 200 and JSON `ok==true` and `model_loaded==true` |
| Canary | `/v1/health/ready` + `/v1/health/live` both 200 |

If health passes but pipeline still fails → [model-smoke-test](../model-smoke-test/SKILL.md).

## Agent instructions

1. Resolve bearer (`gpu-endpoints.config` ← `team-configs/<team>.config` if needed; else ask user). Source `gpu-endpoints.config`; always send the bearer token.
2. Use **only** the supported paths per model from the matrix — never require `/v1/models` on YOLO or Canary.
3. Report per model: which checks ran, HTTP codes, and pass/fail by the rules above.
4. Never invent hosts/ports; never print the bearer token.
