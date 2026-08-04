---
name: ingest-upload-videos
description: >-
  Upload 1..N fixed-length video chunks into vss-chunks via POST /api/v1/videos/upload
  (backend proxies to S3 — works when the S3 VIP is not reachable from the laptop), with
  fallback to direct aws s3 cp to the bucket VIP. Each object should be one time-bounded
  MP4 chunk (~30s). Use for file-based ingest; for live/URL sources use ingest-stream-capture.
---

# Ingest: upload videos (vss2)

Land **fixed-length MP4 chunks** in the ingest bucket (`vss-chunks` / `team-*-vss-chunks`). The DataEngine pipeline watches that bucket (`video-chunk-land-trigger` → `video-segmenter` → …).

**Two upload paths** — use both in order:

| Priority | Path | When it works |
|----------|------|----------------|
| **1 — Preferred** | `POST /api/v1/videos/upload` via **Ingress URL** | Laptop can reach the UI/backend (HTTP), but **not** the internal S3 VIP |
| **2 — Fallback** | `aws s3 cp` to **S3 endpoint VIP** | Laptop has network route to the tenant S3 VIP (often lab/VPN only) |

Do **not** use `/batch-sync/*` for simple file upload — that copies from another S3 bucket/prefix.

## Fixed length (important)

Each object should be one bounded clip (typically **~30 seconds**). Long files must be split locally; the segmenter then produces ~5s segments in `vss-chunks-segments`.

| Layer | Typical length | Where |
|-------|----------------|-------|
| Your upload | ~30s chunk | `vss-chunks` |
| `video-segmenter` | ~5s | `vss-chunks-segments` |

Direct S3 upload can set `capture-interval=30` metadata. The backend upload API does not expose that field today — pre-chunked ~30s MP4s are still fine; use direct S3 if you need explicit `capture-interval`.

## When to use

- Upload 1..N pre-chunked MP4 files from disk.
- **Not** for live URLs / RTSP / YouTube → `ingest-stream-capture`.

## Prerequisites

- **Ingress URL** from `team-configs/<team>.config` (`INGRESS_URL`) — map in `/etc/hosts` if needed.
- Team **username / password** (`USERNAME`, `PASSWORD`) — JWT via `retrieval/login`.
- For S3 fallback only: `S3_ENDPOINT`, `ACCESS_KEY`, `SECRET_KEY`, `S3_CHUNKS_BUCKET` from the same team config.
- DataEngine ingest pipeline deployed (`dataengine-components/`).

Optional metadata options: `GET /api/v1/metadata/ingest-config` (public) — see `retrieval/list-metadata`.

## Split a long file into fixed-length chunks (local)

```bash
ffmpeg -i warehouse_full.mp4 -c copy -f segment -segment_time 30 -reset_timestamps 1 \
  chunks/chunk_%03d.mp4
```

---

## Path 1 — Backend API upload (preferred)

The backend runs **inside the cluster**, has S3 credentials, and writes to `s3_upload_bucket` with ingest metadata. Your laptop only talks to the Ingress host.

### Login

```bash
BACKEND="http://video-lab-team-a.cosmos.vastdata.com"   # INGRESS_URL
TOKEN=$(curl -s -X POST "$BACKEND/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"team-a","password":"Team123!"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token') or '')")
test -n "$TOKEN" || echo "Login failed — see troubleshooting below"
```

### Single file

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST "$BACKEND/api/v1/videos/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@chunks/chunk_000.mp4" \
  -F "is_public=true" \
  -F "capture_type=warehouse" \
  -F "location=Warehouse-A" \
  -F "camera_id=cam-01" \
  -F "scenario=general" \
  -F "tags=demo,hackathon"
```

Success: `{"success":true,"object_key":"team-a/20260804_123456_ab12cd34.mp4",...}`

### Many files (1..N)

```bash
for f in chunks/chunk_*.mp4; do
  echo "Uploading $f ..."
  code=$(curl -s -o /tmp/upload_resp.json -w "%{http_code}" -X POST "$BACKEND/api/v1/videos/upload" \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@$f" \
    -F "is_public=true" \
    -F "capture_type=warehouse" \
    -F "location=Warehouse-A")
  echo "  HTTP $code — $(cat /tmp/upload_resp.json)"
  test "$code" = "200" || echo "  FAILED — see troubleshooting"
done
```

### API form fields

| Field | Notes |
|-------|--------|
| `file` | Required — `.mp4`, `.mov`, `.webm`, `.avi`, `.mkv` |
| `is_public` | `true` / `false` (form boolean) |
| `tags` | Comma-separated |
| `allowed_users` | Comma-separated extra viewers |
| `scenario` | e.g. `general`, `warehouse` — ignored if `custom_prompt` set |
| `custom_prompt` | Max 800 chars |
| `camera_id`, `capture_type`, `location` | Search / dashboard filters |

Limits (backend defaults): **max ~25 MB per file**, concurrent uploads queued (not rejected).

---

## Path 2 — Direct S3 VIP (fallback)

Use when the API path fails for **network** reasons and your laptop **can** reach `S3_ENDPOINT` (lab network / VPN).

```bash
export AWS_ACCESS_KEY_ID="<ACCESS_KEY>"
export AWS_SECRET_ACCESS_KEY="<SECRET_KEY>"
export S3_ENDPOINT="https://builder.cosmos-var201.cosmo.vastdata.com"
BUCKET="team-a-vss-chunks"

aws s3 cp chunks/chunk_000.mp4 "s3://${BUCKET}/team-a/$(date -u +%Y%m%d_%H%M%S)_$(uuidgen | cut -c1-8).mp4" \
  --endpoint-url "$S3_ENDPOINT" \
  --metadata \
camera-id=cam-01,capture-type=warehouse,location=Warehouse-A,scenario=general,\
capture-interval=30,is-public=true,owner=team-a,original-filename=chunk_000.mp4
```

Bulk loop / `aws s3 sync` — same as before; set `capture-interval` to match chunk duration.

### S3 metadata keys (kebab-case)

| Metadata key | Notes |
|--------------|-------|
| `camera-id`, `capture-type`, `location` | ingest filters / search |
| `scenario`, `custom-prompt` | reasoning preset |
| `tags`, `allowed-users` | comma-separated |
| `is-public`, `owner` | ACL |
| `capture-interval` | nominal chunk length (seconds) — **S3 path only** today |

---

## Agent workflow (try API, then S3)

1. Confirm files are **fixed-length chunks** (~30s); split with ffmpeg if needed.
2. **Login** (`retrieval/login`) → get JWT.
3. **Try `POST /api/v1/videos/upload`** for each file (or loop). Report HTTP code + JSON body per file.
4. If API fails with **connection / timeout / DNS** to `INGRESS_URL` → tell the user the Ingress is unreachable (check `/etc/hosts`, VPN, firewall).
5. If API fails with **401** → bad team username/password for this backend tenant.
6. If API fails with **400** → wrong extension; **413** → file larger than `max_upload_size_mb` (split chunk or use smaller file).
7. If API fails with **500** → backend or S3 misconfig on cluster side (backend logs); S3 fallback from laptop will likely fail too.
8. **Only then** try direct S3 (`aws s3 cp`). If that fails with **connection timeout / Could not connect** → explain: *S3 VIP is not routable from your laptop* — that is expected off-lab; use the API path via Ingress instead.
9. If S3 fails with **403 / AccessDenied** → wrong keys or bucket name in team config.
10. After uploads, check `GET /api/v1/dashboard/stats` (`retrieval/dashboard`) — do not claim “searchable” until explore/dashboard shows the parent.

## Troubleshooting — explain this to the user

| Symptom | Likely cause | What to say |
|---------|----------------|-------------|
| `curl: (6) Could not resolve host` on **BACKEND** | `INGRESS_URL` not in `/etc/hosts` or wrong hostname | Add Ingress IP to `/etc/hosts` (organizer IP + `video-lab-team-X.cosmos.vastdata.com`). |
| `curl: (7) Failed to connect` / timeout on **BACKEND** | No route to Ingress / firewall | You cannot reach the cluster UI host from this network; need VPN or run curl from a jump host. |
| HTTP **401** on upload | Invalid login or wrong tenant on backend | Username/password must match the team account; backend `tenant_name` must be your tenant. |
| HTTP **400** `Invalid file extension` | Non-video extension | Use `.mp4` (or allowed list from `GET /api/v1/config`). |
| HTTP **413** `File too large` | Chunk > max upload size (default 25 MB) | Shorten chunk (e.g. 30s) or ask ops to raise `max_upload_size_mb`. |
| HTTP **200** but search empty later | Pipeline lag or ingest errors | Normal for a few minutes; check dashboard `pipeline_alignment`, function logs. |
| `aws s3 cp` **timeout** to `S3_ENDPOINT` | S3 VIP internal-only | **Expected from home/office** — S3 VIP is not on the public internet. Use **API upload via Ingress** instead. |
| `aws s3 cp` **403** | Bad keys or bucket | Re-copy `ACCESS_KEY`, `SECRET_KEY`, `S3_CHUNKS_BUCKET` from `team-configs/<team>.config`. |
| API works, S3 does not | Normal off-lab | API is the right path; S3 VIP is for operators on the lab network. |

## After upload — ingest status

```bash
curl -s "$BACKEND/api/v1/dashboard/stats" -H "Authorization: Bearer $TOKEN"
```

Compare `s3_inventory.chunks_mp4` vs VastDB; parents in `GET /api/v1/videos/explore` (`retrieval/videos`).

## Flow

```
fixed-length MP4(s) → vss-chunks  (API proxy or direct S3)
  → video-segmenter (~5s) → vss-chunks-segments → detector → reasoner → embedder → writer
```
