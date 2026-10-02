"""Watchtower HTTP server: rules, replay evaluation, incidents, stream proxy."""

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from store import Store, make_incident, notify

VIDEO_TTL_SEC = 60 * 60
SEGMENT_TTL_SEC = 60 * 60
FEED_PREFIX = "camera:"
STREAM_CHUNK = 64 * 1024
_MAX_BODY = 2_000_000

STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/styles.css": "styles.css",
}
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_str_list(value):
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def metadata_filters_for_rule(rule) -> dict | None:
    """Scope archive search when a rule names exactly one camera, else one location."""
    cameras = _as_str_list((rule or {}).get("cameras"))
    locations = _as_str_list((rule or {}).get("locations"))
    if len(cameras) == 1:
        return {"camera_id": cameras[0]}
    if len(locations) == 1:
        return {"location": locations[0]}
    return None


class WatchtowerServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class App:
    """Process state. Tests construct this with fakes; ``main`` wires real clients."""

    def __init__(self, settings, vss, llm, store, evaluator=None, tracing=False, static_dir=None):
        self.settings = settings
        self.vss = vss
        self.llm = llm
        self.store = store
        self.tracing = bool(tracing)
        self.static_dir = static_dir or os.path.dirname(os.path.abspath(__file__))
        if evaluator is None:
            from judge import Evaluator

            evaluator = Evaluator(llm)
        self.evaluator = evaluator
        self._cache_lock = threading.Lock()
        self._videos = None
        self._videos_at = 0.0
        self._segments = {}
        self._llm_model = None

    def videos(self):
        now = time.monotonic()
        with self._cache_lock:
            if self._videos is not None and (now - self._videos_at) < VIDEO_TTL_SEC:
                return [dict(video) for video in self._videos]
        fresh = [dict(video) for video in (self.vss.list_videos() or [])]
        with self._cache_lock:
            self._videos = fresh
            self._videos_at = now
        return [dict(video) for video in fresh]

    def segments(self, original_video):
        now = time.monotonic()
        with self._cache_lock:
            cached = self._segments.get(original_video)
            if cached is not None and (now - cached[0]) < SEGMENT_TTL_SEC:
                return [dict(segment) for segment in cached[1]]
        fresh = [dict(segment) for segment in (self.vss.segments(original_video) or [])]
        with self._cache_lock:
            self._segments[original_video] = (now, fresh)
        return [dict(segment) for segment in fresh]

    def vocab(self):
        import rules as rules_mod

        videos = self.videos()
        cameras = sorted({(video.get("camera_id") or "").strip() for video in videos} - {""})
        locations = sorted({(video.get("location") or "").strip() for video in videos} - {""})
        return {
            "classes": list(rules_mod.COCO_CLASSES),
            "cameras": cameras,
            "locations": locations,
        }

    def vss_ok(self) -> bool:
        with self._cache_lock:
            if self._videos:
                return True
        try:
            return bool(self.vss.ping())
        except Exception:
            return False

    def llm_model(self):
        if self._llm_model:
            return self._llm_model
        try:
            name = self.llm.model()
        except Exception:
            return None
        if not name:
            return None
        self._llm_model = str(name)
        return self._llm_model

    def scan_candidates(self, rule, limit, max_videos=200, batch=16):
        """Prefilter-only sweep of the archive; works when semantic search is down."""
        from concurrent.futures import ThreadPoolExecutor

        import rules as rules_mod

        cameras = {str(c).lower() for c in rule.get("cameras") or []}
        locations = {str(l).lower() for l in rule.get("locations") or []}
        videos = [
            video for video in self.videos()
            if (not cameras or str(video.get("camera_id") or "").lower() in cameras)
            and (not locations or str(video.get("location") or "").lower() in locations)
        ][:max_videos]
        found = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for start in range(0, len(videos), batch):
                chunk = videos[start:start + batch]
                for segments in pool.map(self._segments_or_empty, [v.get("original_video") for v in chunk]):
                    for segment in segments:
                        ok, _reason = rules_mod.prefilter(rule, segment)
                        if ok:
                            found.append(segment)
                if len(found) >= limit:
                    break
        return found[:limit]

    def warm(self):
        """Prefetch every video's segments so scans and replays don't wait on VSS."""
        from concurrent.futures import ThreadPoolExecutor

        started = time.monotonic()
        try:
            videos = self.videos()
        except Exception as exc:
            print(f"warm: video list failed: {type(exc).__name__}", flush=True)
            return
        with ThreadPoolExecutor(max_workers=8) as pool:
            counts = list(pool.map(
                lambda v: len(self._segments_or_empty(v.get("original_video"))), videos
            ))
        print(
            f"warm: {len(videos)} videos, {sum(counts)} segments in "
            f"{time.monotonic() - started:.1f}s",
            flush=True,
        )

    def camera_feeds(self):
        """One virtual video per camera: every clip from that camera, oldest first."""
        by_camera = {}
        for video in self.videos():
            camera = video.get("camera_id") or ""
            if camera:
                by_camera.setdefault(camera, []).append(video)
        feeds = []
        for camera, videos in sorted(by_camera.items()):
            feeds.append({
                "original_video": FEED_PREFIX + camera,
                "filename": f"\u25b6 All clips ({len(videos)} videos)",
                "camera_id": camera,
                "location": videos[0].get("location") or "",
                "capture_type": videos[0].get("capture_type") or "",
                "total_segments": sum(int(v.get("total_segments") or 0) for v in videos),
                "duration_sec": sum(float(v.get("duration_sec") or 0) for v in videos),
                "upload_timestamp": videos[0].get("upload_timestamp") or "",
                "preview_source": videos[0].get("preview_source") or "",
            })
        return feeds

    def feed_segments(self, camera_id):
        from concurrent.futures import ThreadPoolExecutor

        videos = sorted(
            (v for v in self.videos() if v.get("camera_id") == camera_id),
            key=lambda v: (str(v.get("upload_timestamp") or ""), str(v.get("filename") or "")),
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            per_video = list(pool.map(self._segments_or_empty, [v.get("original_video") for v in videos]))
        return [segment for segments in per_video for segment in segments]

    def _segments_or_empty(self, original_video, attempts=3):
        for attempt in range(attempts):
            try:
                return self.segments(original_video)
            except Exception:
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
        return []

    def find_segment(self, original_video, segment_number):
        for segment in self.segments(original_video):
            if _same_segment(segment.get("segment_number"), segment_number):
                return segment
        return None


def _same_segment(left, right) -> bool:
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return left == right


def _script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def make_handler(app):
    """Return a request-handler class bound to ``app``."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def __init__(self, *args, **kwargs):
            self.app = app
            self._started = False
            super().__init__(*args, **kwargs)

        def do_GET(self):
            self._guard()

        def do_POST(self):
            self._guard()

        def do_PATCH(self):
            self._guard()

        def do_DELETE(self):
            self._guard()

        def log_message(self, fmt, *args):
            path = urlparse(self.path).path
            status = "-"
            if len(args) >= 2 and str(args[1]).isdigit():
                status = args[1]
            sys.stderr.write(f"{self.command} {path} {status}\n")

        def _guard(self):
            self._started = False
            try:
                self._route()
            except Exception as exc:
                try:
                    self._json(500, {"error": self._public_error(exc)})
                except Exception:
                    pass
            finally:
                self.close_connection = True

        def _public_error(self, exc) -> str:
            message = str(exc) or exc.__class__.__name__
            settings = self.app.settings
            for attr in ("vss_password", "wandb_api_key", "webhook_url"):
                secret = getattr(settings, attr, "") or ""
                if isinstance(secret, str) and len(secret) >= 4:
                    message = message.replace(secret, "***")
            message = re.sub(r"Bearer\s+\S+", "Bearer ***", message, flags=re.IGNORECASE)
            message = re.sub(r"(https?://\S+)\?\S+", r"\1", message)
            if len(message) > 300:
                message = message[:300]
            return message

        def _json(self, status, obj):
            if self._started:
                return
            self._started = True
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                return None, "invalid JSON"
            if length < 0 or length > _MAX_BODY:
                return None, "invalid JSON"
            raw = self.rfile.read(length) if length else b""
            if not raw.strip():
                return {}, None
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, "invalid JSON"
            if not isinstance(data, dict):
                return None, "invalid JSON"
            return data, None

        def _query(self):
            return parse_qs(urlparse(self.path).query)

        def _q(self, name):
            values = self._query().get(name)
            if not values:
                return ""
            return values[0]

        def _route(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if len(path) > 1 and path.endswith("/"):
                path = path[:-1]
            method = self.command

            if path in STATIC_FILES:
                if method != "GET":
                    return self._json(405, {"error": "method not allowed"})
                return self._static(STATIC_FILES[path])
            if path == "/health":
                if method != "GET":
                    return self._json(405, {"error": "method not allowed"})
                return self._json(200, {"ok": True})
            if path == "/api/status":
                if method != "GET":
                    return self._json(405, {"error": "method not allowed"})
                return self._status()
            if path == "/api/videos":
                if method != "GET":
                    return self._json(405, {"error": "method not allowed"})
                return self._json(200, self.app.camera_feeds() + self.app.videos())
            if path == "/api/vocab":
                if method != "GET":
                    return self._json(405, {"error": "method not allowed"})
                return self._json(200, self.app.vocab())
            if path == "/api/segments":
                if method != "GET":
                    return self._json(405, {"error": "method not allowed"})
                return self._segments()
            if path == "/api/rules":
                if method == "GET":
                    return self._json(200, self.app.store.list_rules())
                if method == "POST":
                    return self._create_rule()
                return self._json(405, {"error": "method not allowed"})
            if path == "/api/rules/preview":
                if method != "POST":
                    return self._json(405, {"error": "method not allowed"})
                return self._preview_rule()
            if path == "/api/evaluate":
                if method != "POST":
                    return self._json(405, {"error": "method not allowed"})
                return self._evaluate()
            if path == "/api/incidents":
                if method == "GET":
                    return self._json(200, self.app.store.list_incidents())
                if method == "DELETE":
                    self.app.store.clear_incidents()
                    return self._json(200, {"ok": True})
                return self._json(405, {"error": "method not allowed"})
            if path == "/api/stream":
                if method != "GET":
                    return self._json(405, {"error": "method not allowed"})
                return self._stream()
            if path == "/api/detections":
                if method != "GET":
                    return self._json(405, {"error": "method not allowed"})
                return self._detections()

            prefix = "/api/rules/"
            if path.startswith(prefix):
                rest = path[len(prefix):]
                if rest.endswith("/backfill") and "/" not in rest[: -len("/backfill")]:
                    if method != "POST":
                        return self._json(405, {"error": "method not allowed"})
                    rule_id = rest[: -len("/backfill")]
                    return self._backfill(rule_id)
                if rest and "/" not in rest:
                    if method == "PATCH":
                        return self._patch_rule(rest)
                    if method == "DELETE":
                        return self._delete_rule(rest)
                    return self._json(405, {"error": "method not allowed"})
            return self._json(404, {"error": "not found"})

        def _static(self, name):
            root = os.path.realpath(self.app.static_dir)
            full = os.path.realpath(os.path.join(root, name))
            if full != os.path.join(root, name) and not full.startswith(root + os.sep):
                return self._json(404, {"error": "not found"})
            if not os.path.isfile(full):
                return self._json(404, {"error": "not found"})
            with open(full, "rb") as handle:
                body = handle.read()
            ext = os.path.splitext(name)[1]
            self._started = True
            self.send_response(200)
            self.send_header("Content-Type", STATIC_TYPES.get(ext, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _status(self):
            self._json(200, {
                "mock": bool(getattr(self.app.settings, "mock", False)),
                "vss_ok": self.app.vss_ok(),
                "llm_model": self.app.llm_model(),
                "webhook_configured": bool(getattr(self.app.settings, "webhook_url", "") or ""),
                "tracing": bool(self.app.tracing),
            })

        def _segments(self):
            original_video = self._q("original_video")
            if not original_video:
                return self._json(400, {"error": "missing original_video"})
            if original_video.startswith(FEED_PREFIX):
                return self._json(200, self.app.feed_segments(original_video[len(FEED_PREFIX):]))
            return self._json(200, self.app.segments(original_video))

        def _finalize_rule(self, rule):
            import rules as rules_mod

            if not isinstance(rule, dict):
                raise ValueError("rule must be an object")
            if not rule.get("id"):
                rule["id"] = rules_mod.new_rule_id()
            if not rule.get("created_at"):
                rule["created_at"] = _utc_now()
            if "enabled" not in rule:
                rule["enabled"] = True
            return rule

        def _preview_rule(self):
            import rules as rules_mod

            body, err = self._read_json()
            if err:
                return self._json(400, {"error": err})
            text = (body.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "text is required"})
            rule = rules_mod.compile_rule(self.app.llm, text, self.app.vocab())
            return self._json(200, rule)

        def _create_rule(self):
            import rules as rules_mod

            body, err = self._read_json()
            if err:
                return self._json(400, {"error": err})
            vocab = self.app.vocab()
            provided = body.get("rule")
            if isinstance(provided, dict):
                rule = rules_mod.validate_rule(dict(provided), vocab)
            else:
                text = (body.get("text") or "").strip()
                if not text:
                    return self._json(400, {"error": "text or rule is required"})
                rule = rules_mod.compile_rule(self.app.llm, text, vocab)
            saved = self.app.store.save_rule(self._finalize_rule(rule))
            return self._json(200, saved)

        def _patch_rule(self, rule_id):
            body, err = self._read_json()
            if err:
                return self._json(400, {"error": err})
            if self.app.store.get_rule(rule_id) is None:
                return self._json(404, {"error": "rule not found"})
            fields = {key: value for key, value in body.items() if key != "id"}
            if fields:
                updated = self.app.store.update_rule(rule_id, **fields)
            else:
                updated = self.app.store.get_rule(rule_id)
            if updated is None:
                return self._json(404, {"error": "rule not found"})
            return self._json(200, updated)

        def _delete_rule(self, rule_id):
            if not self.app.store.delete_rule(rule_id):
                return self._json(404, {"error": "rule not found"})
            return self._json(200, {"ok": True})

        def _evaluate(self):
            import judge as judge_mod

            body, err = self._read_json()
            if err:
                return self._json(400, {"error": err})
            original_video = body.get("original_video")
            if not original_video or "segment_number" not in body:
                return self._json(400, {"error": "original_video and segment_number are required"})
            segment = self.app.find_segment(original_video, body.get("segment_number"))
            if segment is None:
                return self._json(404, {"error": "segment not found"})
            rules = self.app.store.list_rules()
            by_id = {rule.get("id"): rule for rule in rules}
            results = self.app.evaluator.evaluate(rules, segment) or []
            incidents = []
            for result in results:
                verdict = result.get("verdict") if isinstance(result, dict) else None
                if not verdict or not verdict.get("match"):
                    continue
                rule = by_id.get(result.get("rule_id"))
                if not rule:
                    continue
                report = judge_mod.write_report(self.app.llm, rule, segment, verdict)
                incident = make_incident(rule, segment, verdict, report, "live")
                stored = self.app.store.add_incident(incident)
                if stored is None:
                    continue
                incident_id = stored["id"]

                def _on_done(ok, _id=incident_id):
                    self.app.store.set_incident_notified(_id, bool(ok))

                notify(getattr(self.app.settings, "webhook_url", "") or "", stored, on_done=_on_done)
                incidents.append(stored)
            return self._json(200, {"segment": segment, "results": results, "incidents": incidents})

        def _backfill(self, rule_id):
            import judge as judge_mod

            body, err = self._read_json()
            if err:
                return self._json(400, {"error": err})
            rule = self.app.store.get_rule(rule_id)
            if rule is None:
                return self._json(404, {"error": "rule not found"})
            limit = 12
            if body and "limit" in body and body.get("limit") is not None:
                try:
                    limit = int(body.get("limit"))
                except (TypeError, ValueError):
                    return self._json(400, {"error": "limit must be an integer"})
            if limit < 1:
                limit = 1
            filters = metadata_filters_for_rule(rule)
            method = "search"
            try:
                hits = self.app.vss.search(
                    rule.get("search_query") or "",
                    top_k=limit,
                    min_similarity=0.2,
                    metadata_filters=filters,
                ) or []
            except Exception:
                hits = []
            if not hits:
                method = "scan"
                hits = self.app.scan_candidates(rule, limit)
            from concurrent.futures import ThreadPoolExecutor

            def _check(hit):
                results = self.app.evaluator.evaluate([rule], hit) or []
                for result in results:
                    candidate = result.get("verdict") if isinstance(result, dict) else None
                    if candidate and candidate.get("match"):
                        return candidate, judge_mod.write_report(self.app.llm, rule, hit, candidate)
                return None, None

            hits = [hit for hit in hits if isinstance(hit, dict)]
            with ThreadPoolExecutor(max_workers=6) as pool:
                outcomes = list(pool.map(_check, hits))

            checked = 0
            matched = 0
            incidents = []
            summaries = []
            for hit, (verdict, report) in zip(hits, outcomes):
                checked += 1
                is_match = verdict is not None
                if is_match:
                    incident = make_incident(rule, hit, verdict, report, "backfill")
                    stored = self.app.store.add_incident(incident)
                    if stored is not None:
                        incidents.append(stored)
                    matched += 1
                summaries.append({
                    "original_video": hit.get("original_video"),
                    "segment_number": hit.get("segment_number"),
                    "matched": is_match,
                })
            return self._json(200, {
                "checked": checked,
                "matched": matched,
                "method": method,
                "incidents": incidents,
                "hits": summaries,
            })

        def _detections(self):
            source = self._q("source")
            if not source:
                return self._json(400, {"error": "missing source"})
            found = self.app.vss.detections(source)
            return self._json(200, found)

        def _stream(self):
            source = self._q("source")
            if not source:
                return self._json(400, {"error": "missing source"})
            status, headers, body = self.app.vss.open_stream(source, self.headers.get("Range"))
            if body is None:
                body = _EmptyBody()
            try:
                status = int(status)
                if status >= 400:
                    return self._json(status, {"error": "upstream error"})
                header_map = headers if isinstance(headers, dict) else {}
                content_type = _header(header_map, "Content-Type") or "video/mp4"
                self._started = True
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
                    value = _header(header_map, name)
                    if value is not None and value != "":
                        self.send_header(name, str(value))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = body.read(STREAM_CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                close = getattr(body, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        pass

    return Handler


class _EmptyBody:
    def read(self, _n=None):
        return b""

    def close(self):
        return None


def _header(headers, name):
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return value
    return None


def build_app(settings=None):
    """Wire clients from settings. MOCK uses the offline fakes and skips tracing."""
    if settings is None:
        import config

        settings = config.load_settings()
    tracing = False
    if settings.mock:
        from fixtures import FakeVSSClient
        from llm import FakeLLM

        vss = FakeVSSClient()
        llm = FakeLLM()
    else:
        from llm import LLMClient, init_tracing
        from vss_client import VSSClient

        vss = VSSClient(settings.vss_url, settings.vss_username, settings.vss_password)
        llm = LLMClient(
            settings.llm_base_url,
            settings.wandb_api_key,
            settings.wandb_project_path,
            settings.llm_model,
        )
        try:
            tracing = bool(init_tracing(settings.wandb_project_path, settings.wandb_api_key))
        except Exception:
            tracing = False
    from judge import Evaluator

    store = Store(settings.data_dir)
    return App(settings, vss, llm, store, Evaluator(llm), tracing=tracing)


def main():
    app = build_app()
    server = WatchtowerServer(("0.0.0.0", int(app.settings.port)), make_handler(app))
    print(f"Watchtower listening on 0.0.0.0:{app.settings.port}", flush=True)
    threading.Thread(target=app.warm, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
