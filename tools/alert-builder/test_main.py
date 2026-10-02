"""Offline tests for the Watchtower store and HTTP API. No network."""

import importlib
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _try_import(name):
    try:
        return importlib.import_module(name)
    except ImportError:
        sys.modules.pop(name, None)
        return None


def _install_rules_stub():
    module = types.ModuleType("rules")
    module.COCO_CLASSES = ["person", "car", "truck", "bicycle", "bus"]

    def new_rule_id():
        return "r_" + uuid.uuid4().hex[:8]

    def validate_rule(rule, vocab):
        out = dict(rule)
        out.setdefault("cameras", [])
        out.setdefault("locations", [])
        out.setdefault("require_classes", {})
        out.setdefault("caption_any", [])
        out.setdefault("enabled", True)
        out.setdefault("name", "rule")
        out.setdefault("text", "")
        out.setdefault("search_query", out.get("text") or "")
        out.setdefault("judge_question", out.get("text") or "")
        out.setdefault("severity_guide", "")
        return out

    def compile_rule(llm, text, vocab):
        rule = validate_rule({"text": text, "name": text[:48] or "rule"}, vocab)
        rule["id"] = new_rule_id()
        return rule

    def heuristic_compile(text, vocab):
        return compile_rule(None, text, vocab)

    def prefilter(rule, segment):
        return True, "ok"

    module.new_rule_id = new_rule_id
    module.validate_rule = validate_rule
    module.compile_rule = compile_rule
    module.heuristic_compile = heuristic_compile
    module.prefilter = prefilter
    sys.modules["rules"] = module
    return module


def _install_judge_stub():
    module = types.ModuleType("judge")

    def judge(llm, rule, segment):
        return {
            "match": True,
            "severity": 4,
            "confidence": 0.8,
            "reason": "person walking near a moving forklift",
        }

    def write_report(llm, rule, segment, verdict):
        return f"## {rule.get('name')}\n\n{verdict.get('reason')}"

    class Evaluator:
        def __init__(self, llm, max_workers=4):
            self.llm = llm

        def evaluate(self, rules, segment):
            results = []
            for rule in rules:
                if not rule.get("enabled", True):
                    continue
                verdict = judge(self.llm, rule, segment)
                results.append({
                    "rule_id": rule.get("id"),
                    "rule_name": rule.get("name"),
                    "prefilter_pass": True,
                    "prefilter_reason": "ok",
                    "verdict": verdict,
                })
            return results

    module.judge = judge
    module.write_report = write_report
    module.Evaluator = Evaluator
    sys.modules["judge"] = module
    return module


_RULES = _try_import("rules")
if _RULES is None:
    _RULES = _install_rules_stub()
_JUDGE = _try_import("judge")
if _JUDGE is None:
    _JUDGE = _install_judge_stub()

from main import App, WatchtowerServer, make_handler, metadata_filters_for_rule  # noqa: E402
from store import Store, make_incident, notify  # noqa: E402


class _Settings:
    mock = True
    vss_url = "http://vss.example"
    vss_username = "user"
    vss_password = "secret-password"
    wandb_api_key = "secret-wandb"
    wandb_project_path = "team/project"
    llm_base_url = "http://llm.example/v1"
    llm_model = "fake-model"
    webhook_url = ""
    port = 0
    data_dir = ""


class FakeVSS:
    def __init__(self):
        self.videos = [{
            "original_video": "s3://bucket/warehouse.mp4",
            "filename": "warehouse.mp4",
            "camera_id": "sdg_warehouse_cam-2",
            "location": "warehouse3",
            "capture_type": "cctv",
            "total_segments": 2,
            "duration_sec": 20.0,
            "upload_timestamp": "2026-09-01T12:00:00",
            "preview_source": "s3://bucket/warehouse.mp4",
        }]
        self._segments = [
            {
                "source": "s3://bucket/warehouse_seg0.mp4",
                "original_video": "s3://bucket/warehouse.mp4",
                "segment_number": 0,
                "start_sec": 75.0,
                "end_sec": 80.0,
                "caption": "a person walks near a moving forklift in the aisle",
                "object_counts": {"person": 1},
                "object_classes": ["person"],
                "camera_id": "sdg_warehouse_cam-2",
                "location": "warehouse3",
                "capture_type": "cctv",
                "upload_timestamp": "2026-09-01T12:00:00",
                "similarity": None,
            },
            {
                "source": "s3://bucket/warehouse_seg1.mp4",
                "original_video": "s3://bucket/warehouse.mp4",
                "segment_number": 1,
                "start_sec": 5.0,
                "end_sec": 10.0,
                "caption": "empty aisle, no people",
                "object_counts": {},
                "object_classes": [],
                "camera_id": "sdg_warehouse_cam-2",
                "location": "warehouse3",
                "capture_type": "cctv",
                "upload_timestamp": "2026-09-01T12:00:05",
                "similarity": None,
            },
        ]
        self.searches = []
        self.last_range = None
        self.ping_ok = True

    def list_videos(self):
        return [dict(video) for video in self.videos]

    def segments(self, original_video):
        return [dict(segment) for segment in self._segments if segment["original_video"] == original_video]

    def search(self, query, top_k=20, min_similarity=0.25, metadata_filters=None):
        self.searches.append({
            "query": query,
            "top_k": top_k,
            "min_similarity": min_similarity,
            "metadata_filters": metadata_filters,
        })
        hits = []
        for segment in self._segments:
            if metadata_filters:
                camera = metadata_filters.get("camera_id")
                location = metadata_filters.get("location")
                if camera and segment.get("camera_id") != camera:
                    continue
                if location and segment.get("location") != location:
                    continue
            hit = dict(segment)
            hit["similarity"] = 0.9
            hits.append(hit)
            if len(hits) >= top_k:
                break
        return hits

    def detections(self, source):
        return None

    def open_stream(self, source, range_header=None):
        self.last_range = range_header
        payload = b"fake-mp4-bytes"
        body = io.BytesIO(payload)
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(len(payload)),
            "Accept-Ranges": "bytes",
        }
        if range_header:
            headers["Content-Range"] = f"bytes 0-{len(payload) - 1}/{len(payload)}"
            return 206, headers, body
        return 200, headers, body

    def ping(self):
        return self.ping_ok


class FakeLLM:
    def model(self):
        return "fake-model"

    def chat(self, messages, temperature=0.1, max_tokens=800):
        return "## Incident\n\nA person is walking near a moving forklift."

    def chat_json(self, messages, temperature=0.0, max_tokens=800):
        return {
            "match": True,
            "severity": 4,
            "confidence": 0.8,
            "reason": "person walking near a moving forklift",
        }


RULE = {
    "id": "r_test01",
    "name": "Person near forklift",
    "text": "Alert me when a person walks near a forklift",
    "cameras": [],
    "locations": [],
    "require_classes": {"person": 1},
    "caption_any": ["forklift"],
    "search_query": "person walking near a moving forklift in a warehouse aisle",
    "judge_question": "Is a person within a few metres of a moving forklift?",
    "severity_guide": "5 = contact or imminent collision. 1 = distant, no risk",
    "enabled": True,
    "created_at": "2026-10-02T00:00:00Z",
}


class TestStore(unittest.TestCase):
    def test_dedupe_order_reload_and_rule_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "rules.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "incidents.json")))
            rule = dict(RULE)
            store.save_rule(rule)
            segment = {
                "source": "s3://bucket/a.mp4",
                "original_video": "s3://bucket/warehouse.mp4",
                "segment_number": 3,
                "start_sec": 75,
                "end_sec": 80,
                "camera_id": "sdg_warehouse_cam-2",
                "location": "warehouse3",
                "upload_timestamp": "2026-09-01T12:00:00",
                "caption": "forklift",
                "object_counts": {"person": 1},
            }
            verdict = {"match": True, "severity": 4, "confidence": 0.8, "reason": "close"}
            first = make_incident(rule, segment, verdict, "report", "live")
            self.assertTrue(first["id"].startswith("i_"))
            self.assertIsNone(first["notified"])
            self.assertEqual(first["created_at"][-1], "Z")
            stored = store.add_incident(first)
            self.assertIsNotNone(stored)
            again = make_incident(rule, segment, verdict, "report", "live")
            self.assertIsNone(store.add_incident(again))
            segment_b = dict(segment)
            segment_b["source"] = "s3://bucket/b.mp4"
            second = make_incident(rule, segment_b, verdict, "report", "backfill")
            second["created_at"] = "2099-01-01T00:00:00Z"
            store.add_incident(second)
            listed = store.list_incidents()
            self.assertEqual([item["source"] for item in listed], ["s3://bucket/b.mp4", "s3://bucket/a.mp4"])
            self.assertTrue(store.delete_rule(rule["id"]))
            self.assertEqual(len(store.list_incidents()), 2)
            self.assertIsNone(store.get_rule(rule["id"]))
            marked = store.set_incident_notified(stored["id"], True)
            self.assertTrue(marked["notified"])
            reloaded = Store(tmp)
            self.assertEqual(len(reloaded.list_rules()), 0)
            self.assertEqual(len(reloaded.list_incidents()), 2)
            self.assertTrue(reloaded.list_incidents()[1]["notified"] or reloaded.list_incidents()[0]["notified"])


class TestNotify(unittest.TestCase):
    def test_posts_slack_and_discord_body(self):
        received = {}
        done = threading.Event()

        class Capture(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or "0")
                received["body"] = json.loads(self.rfile.read(length).decode())
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                done.set()

            def log_message(self, fmt, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            incident = {
                "severity": 4,
                "rule_name": "Person near forklift",
                "camera_id": "sdg_warehouse_cam-2",
                "start_sec": 75,
                "reason": "person crossing aisle ahead of forklift",
            }
            box = {}
            notify(
                f"http://127.0.0.1:{port}/hook",
                incident,
                on_done=lambda ok: box.update(ok=ok),
            )
            self.assertTrue(done.wait(3))
            expected = (
                "🚨 [sev 4] Person near forklift — sdg_warehouse_cam-2 @ 00:01:15 — "
                "person crossing aisle ahead of forklift"
            )
            self.assertEqual(received["body"]["content"], expected)
            self.assertEqual(received["body"]["text"], expected)
            for _ in range(50):
                if "ok" in box:
                    break
                threading.Event().wait(0.05)
            self.assertTrue(box["ok"])
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    def test_empty_url_and_failure_are_quiet(self):
        called = []
        notify("", {"severity": 1, "rule_name": "n", "camera_id": "c", "start_sec": 0, "reason": "r"}, on_done=called.append)
        self.assertEqual(called, [])
        done = threading.Event()
        box = {}

        def on_done(ok):
            box["ok"] = ok
            done.set()

        notify(
            "http://127.0.0.1:1/hook",
            {"severity": 1, "rule_name": "n", "camera_id": "c", "start_sec": 0, "reason": "r"},
            on_done=on_done,
        )
        self.assertTrue(done.wait(6))
        self.assertFalse(box["ok"])


class TestMetadataFilters(unittest.TestCase):
    def test_camera_then_location(self):
        self.assertIsNone(metadata_filters_for_rule({"cameras": [], "locations": []}))
        self.assertEqual(
            metadata_filters_for_rule({"cameras": ["cam-2"], "locations": ["warehouse3"]}),
            {"camera_id": "cam-2"},
        )
        self.assertEqual(
            metadata_filters_for_rule({"cameras": [], "locations": ["warehouse3"]}),
            {"location": "warehouse3"},
        )
        self.assertEqual(
            metadata_filters_for_rule({"cameras": ["a", "b"], "locations": ["x"]}),
            {"location": "x"},
        )
        self.assertIsNone(metadata_filters_for_rule({"cameras": ["a", "b"], "locations": ["x", "y"]}))


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.static = tempfile.TemporaryDirectory()
        cls.vss = FakeVSS()
        cls.llm = FakeLLM()
        settings = _Settings()
        settings.data_dir = cls.tmp.name
        store = Store(cls.tmp.name)
        cls.app = App(settings, cls.vss, cls.llm, store, static_dir=cls.static.name)
        cls.httpd = WatchtowerServer(("127.0.0.1", 0), make_handler(cls.app))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=5)
        cls.httpd.server_close()
        cls.tmp.cleanup()
        cls.static.cleanup()

    def _request(self, method, path, body=None, headers=None, raw=None):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = raw
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        if data is not None:
            hdrs["Content-Length"] = str(len(data))
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        status = resp.status
        resp_headers = dict(resp.getheaders())
        conn.close()
        content_type = resp_headers.get("Content-Type", "")
        if "json" in content_type and payload:
            return status, json.loads(payload.decode()), resp_headers
        return status, payload, resp_headers

    def test_api_flow(self):
        status, body, _headers = self._request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

        status, videos, _headers = self._request("GET", "/api/videos")
        self.assertEqual(status, 200)
        self.assertEqual(videos[0]["camera_id"], "sdg_warehouse_cam-2")

        status, status_body, _headers = self._request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(status_body["mock"])
        self.assertTrue(status_body["vss_ok"])
        self.assertEqual(status_body["llm_model"], "fake-model")
        self.assertFalse(status_body["webhook_configured"])

        status, _body, _headers = self._request("POST", "/api/rules", raw=b"{")
        self.assertEqual(status, 400)

        status, saved, _headers = self._request("POST", "/api/rules", {"rule": RULE})
        self.assertEqual(status, 200, saved)
        rule_id = saved["id"]
        self.assertTrue(rule_id)

        status, evaluated, _headers = self._request("POST", "/api/evaluate", {
            "original_video": "s3://bucket/warehouse.mp4",
            "segment_number": 0,
        })
        self.assertEqual(status, 200, evaluated)
        self.assertEqual(evaluated["segment"]["segment_number"], 0)
        self.assertTrue(evaluated["incidents"], evaluated.get("results"))
        self.assertEqual(evaluated["incidents"][0]["mode"], "live")
        self.assertTrue(evaluated["incidents"][0]["report"])
        self.assertEqual(evaluated["incidents"][0]["rule_id"], rule_id)

        status, again, _headers = self._request("POST", "/api/evaluate", {
            "original_video": "s3://bucket/warehouse.mp4",
            "segment_number": 0,
        })
        self.assertEqual(status, 200, again)
        self.assertEqual(again["incidents"], [])
        self.assertTrue(any(
            isinstance(result, dict) and (result.get("verdict") or {}).get("match")
            for result in again["results"]
        ))

        status, incidents, _headers = self._request("GET", "/api/incidents")
        self.assertEqual(status, 200)
        self.assertEqual(len(incidents), 1)

        status, cleared, _headers = self._request("DELETE", "/api/incidents")
        self.assertEqual(status, 200)
        self.assertEqual(cleared, {"ok": True})
        status, incidents, _headers = self._request("GET", "/api/incidents")
        self.assertEqual(incidents, [])

        status, missing, _headers = self._request("POST", "/api/evaluate", {
            "original_video": "s3://bucket/missing.mp4",
            "segment_number": 1,
        })
        self.assertEqual(status, 404)
        self.assertIn("error", missing)

        status, backfill, _headers = self._request(
            "POST", f"/api/rules/{rule_id}/backfill", {"limit": 12}
        )
        self.assertEqual(status, 200, backfill)
        self.assertGreaterEqual(backfill["checked"], 1)
        self.assertGreaterEqual(backfill["matched"], 1)
        self.assertGreaterEqual(len(backfill["incidents"]), 1)
        self.assertEqual(backfill["incidents"][0]["mode"], "backfill")
        self.assertIn("matched", backfill["hits"][0])
        self.assertTrue(self.vss.searches)
        self.assertEqual(self.vss.searches[-1]["min_similarity"], 0.2)
        self.assertEqual(self.vss.searches[-1]["top_k"], 12)
        self.assertIsNone(self.vss.searches[-1]["metadata_filters"])

        for path in ("/", "/index.html", "/app.js", "/styles.css"):
            status, payload, headers = self._request("GET", path)
            self.assertEqual(status, 404, path)
            self.assertIsInstance(payload, dict)
            self.assertIn("error", payload)
            self.assertIn("json", headers.get("Content-Type", ""))

        status, payload, headers = self._request(
            "GET",
            "/api/stream?source=s3://bucket/warehouse_seg0.mp4",
            headers={"Range": "bytes=0-13"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(payload, b"fake-mp4-bytes")
        self.assertEqual(self.vss.last_range, "bytes=0-13")
        self.assertIn("video/mp4", headers.get("Content-Type", ""))
        self.assertIn("Content-Range", headers)


if __name__ == "__main__":
    unittest.main()
