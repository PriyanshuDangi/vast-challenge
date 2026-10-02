"""Offline tests for config, segment normalization, the VSS client, and fixtures."""

from __future__ import annotations

import email.message
import io
import json
import math
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from unittest import mock

import config
import fixtures
import vss_client


def _http_error(code: int, headers: dict | None = None, body: bytes = b"") -> urllib.error.HTTPError:
    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(
        "https://vss.example/api",
        code,
        "error",
        message,
        io.BytesIO(body),
    )


class _Response:
    def __init__(self, payload, status: int = 200, headers: dict | None = None, body: bytes | None = None):
        if body is None:
            body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = headers or {}
        self._body = body

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0 or n >= len(self._body):
            data = self._body
            self._body = b""
            return data
        data = self._body[:n]
        self._body = self._body[n:]
        return data

    def close(self) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


class NormalizeTests(unittest.TestCase):
    def test_maps_reasoning_fields_and_similarity(self):
        segment = vss_client.normalize_segment(
            {
                "source": "s3://bucket/seg.mp4",
                "original_video": "s3://bucket/parent.mp4",
                "segment_number": 3,
                "segment_start_sec": 15,
                "segment_end_sec": 20,
                "reasoning_content": "A person walks near a forklift.",
                "object_classes": "Person, Car",
                "object_counts": '{"Person": 2, "TRUCK": 1}',
                "camera_id": "cam-2",
                "location": "warehouse3",
                "capture_type": "cctv",
                "upload_timestamp": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
                "similarity_score": 0.42,
            }
        )
        self.assertEqual(segment["caption"], "A person walks near a forklift.")
        self.assertEqual(segment["start_sec"], 15.0)
        self.assertEqual(segment["end_sec"], 20.0)
        self.assertEqual(segment["object_classes"], ["person", "car"])
        self.assertEqual(segment["object_counts"], {"person": 2, "truck": 1})
        self.assertEqual(segment["upload_timestamp"], "2026-09-01T12:00:00+00:00")
        self.assertEqual(segment["similarity"], 0.42)
        self.assertEqual(segment["segment_number"], 3)

    def test_class_list_and_dict_counts(self):
        segment = vss_client.normalize_segment(
            {
                "object_classes": [" Person ", "car", "person"],
                "object_counts": {"Car": 2.0, "person": "1"},
            }
        )
        self.assertEqual(segment["object_classes"], ["person", "car"])
        self.assertEqual(segment["object_counts"], {"car": 2, "person": 1})

    def test_empty_nan_and_missing_counts_fall_back_to_classes(self):
        for raw in ("", None, "nan", "NaN", float("nan"), "null", {}):
            segment = vss_client.normalize_segment(
                {"object_classes": "person, car", "object_counts": raw}
            )
            self.assertEqual(segment["object_counts"], {"person": 1, "car": 1}, raw)
            self.assertEqual(segment["object_classes"], ["person", "car"])

    def test_invalid_json_and_non_dict_counts_fall_back(self):
        for raw in ("{not json", "[]", 5, ["person"]):
            segment = vss_client.normalize_segment(
                {"object_classes": "truck", "object_counts": raw}
            )
            self.assertEqual(segment["object_counts"], {"truck": 1})

    def test_nan_count_values_are_dropped_then_fallback_if_empty(self):
        segment = vss_client.normalize_segment(
            {
                "object_classes": "person, car",
                "object_counts": {"person": float("nan"), "car": 2},
            }
        )
        self.assertEqual(segment["object_counts"], {"car": 2})
        self.assertEqual(segment["object_classes"], ["person", "car"])

    def test_present_counts_are_not_replaced_by_class_fallback(self):
        segment = vss_client.normalize_segment(
            {"object_classes": "person, car", "object_counts": '{"person": 4}'}
        )
        self.assertEqual(segment["object_counts"], {"person": 4})
        self.assertEqual(segment["object_classes"], ["person", "car"])

    def test_nan_classes_and_empty_row(self):
        segment = vss_client.normalize_segment(
            {"object_classes": "person, nan, , None", "object_counts": ""}
        )
        self.assertEqual(segment["object_classes"], ["person"])
        self.assertEqual(segment["object_counts"], {"person": 1})
        empty = vss_client.normalize_segment({})
        self.assertEqual(empty["caption"], "")
        self.assertEqual(empty["object_classes"], [])
        self.assertEqual(empty["object_counts"], {})
        self.assertEqual(empty["start_sec"], 0.0)
        self.assertEqual(empty["end_sec"], 0.0)
        self.assertEqual(empty["segment_number"], 0)
        self.assertEqual(empty["upload_timestamp"], "")
        self.assertIsNone(empty["similarity"])

    def test_similarity_zero_nan_and_timestamp_strings(self):
        zero = vss_client.normalize_segment({"similarity_score": 0})
        self.assertEqual(zero["similarity"], 0.0)
        missing = vss_client.normalize_segment({"similarity_score": float("nan")})
        self.assertIsNone(missing["similarity"])
        text = vss_client.normalize_segment({"similarity_score": "nan", "upload_timestamp": "nan"})
        self.assertIsNone(text["similarity"])
        self.assertEqual(text["upload_timestamp"], "")
        kept = vss_client.normalize_segment({"upload_timestamp": "2026-09-01T12:00:00"})
        self.assertEqual(kept["upload_timestamp"], "2026-09-01T12:00:00")
        preferred = vss_client.normalize_segment({"similarity_score": 0.2, "similarity": 0.9})
        self.assertEqual(preferred["similarity"], 0.2)

    def test_normalize_video_uses_chunk_duration(self):
        video = vss_client.normalize_video(
            {
                "original_video": "s3://bucket/clip.mp4",
                "filename": "clip.mp4",
                "camera_id": "sdg_warehouse_cam-2",
                "location": "warehouse3",
                "capture_type": "cctv",
                "total_segments": "12",
                "chunk_duration_sec": 60,
                "upload_timestamp": "2026-09-01T12:00:00",
                "preview_source": "s3://bucket/seg.mp4",
                "duration_sec": 1,
            }
        )
        self.assertEqual(video["duration_sec"], 60.0)
        self.assertEqual(video["total_segments"], 12)
        self.assertEqual(video["camera_id"], "sdg_warehouse_cam-2")
        blank = vss_client.normalize_video({"chunk_duration_sec": float("nan")})
        self.assertEqual(blank["duration_sec"], 0.0)
        self.assertEqual(blank["filename"], "")
        self.assertTrue(math.isfinite(blank["duration_sec"]))


class ConfigTests(unittest.TestCase):
    def _load(self, directory: str, env: dict[str, str]):
        with mock.patch.object(config, "CONFIG_DIR", directory):
            with mock.patch.dict(os.environ, env, clear=True):
                return config.load_settings()

    def test_parses_config_file_and_masks_repr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "team.config")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    "\n".join(
                        [
                            "# comment",
                            "",
                            "export INGRESS_URL=\"https://from-file.example\"",
                            "USERNAME='user-file'",
                            "export PASSWORD=\"pw file\"",
                            "#PASSWORD=ignored",
                            "WANDB_API_KEY=\"wk-file\"",
                            "WANDB_TEAM=teamx",
                            "WANDB_PROJECT='projy'",
                            "PORT=9091",
                            "MOCK=true",
                            "ALERT_WEBHOOK_URL=https://hook.example/a",
                            "LLM_MODEL=model-x",
                            "DATA_DIR=/tmp/custom-data",
                            "notes.txt=not-a-real-use",
                        ]
                    )
                )
            with open(os.path.join(directory, "notes.txt"), "w", encoding="utf-8") as handle:
                handle.write("INGRESS_URL=https://ignored.example\n")
            settings = self._load(directory, {})
        self.assertEqual(settings.vss_url, "https://from-file.example")
        self.assertEqual(settings.vss_username, "user-file")
        self.assertEqual(settings.vss_password, "pw file")
        self.assertEqual(settings.wandb_api_key, "wk-file")
        self.assertEqual(settings.wandb_project_path, "teamx/projy")
        self.assertEqual(settings.port, 9091)
        self.assertTrue(settings.mock)
        self.assertEqual(settings.webhook_url, "https://hook.example/a")
        self.assertEqual(settings.llm_model, "model-x")
        self.assertEqual(settings.data_dir, "/tmp/custom-data")
        self.assertEqual(settings.llm_base_url, "https://api.inference.wandb.ai/v1")
        text = repr(settings)
        self.assertIn("vss_password='***'", text)
        self.assertIn("wandb_api_key='***'", text)
        self.assertNotIn("pw file", text)
        self.assertNotIn("wk-file", text)
        self.assertIn("user-file", text)

    def test_env_beats_file_and_vss_names_beat_ingress(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "team.config"), "w", encoding="utf-8") as handle:
                handle.write(
                    "\n".join(
                        [
                            "VSS_URL=https://file-vss.example",
                            "INGRESS_URL=https://file-ingress.example",
                            "USERNAME=file-user",
                            "PASSWORD=file-pw",
                            "WANDB_API_KEY=file-key",
                            "WANDB_PROJECT_PATH=file/path",
                            "WANDB_TEAM=fileteam",
                            "WANDB_PROJECT=fileproj",
                            "MOCK=1",
                        ]
                    )
                )
            settings = self._load(
                directory,
                {
                    "INGRESS_URL": "https://env-ingress.example",
                    "VSS_USERNAME": "env-user",
                    "PASSWORD": "SENTINEL_PASSWORD_9f3a",
                    "WANDB_API_KEY": "SENTINEL_WANDB_KEY_9f3a",
                    "WANDB_TEAM": "envteam",
                    "WANDB_PROJECT": "envproj",
                    "MOCK": "false",
                },
            )
        self.assertEqual(settings.vss_url, "https://env-ingress.example")
        self.assertEqual(settings.vss_username, "env-user")
        self.assertEqual(settings.vss_password, "SENTINEL_PASSWORD_9f3a")
        self.assertEqual(settings.wandb_api_key, "SENTINEL_WANDB_KEY_9f3a")
        self.assertEqual(settings.wandb_project_path, "envteam/envproj")
        self.assertFalse(settings.mock)
        text = repr(settings)
        self.assertNotIn("SENTINEL_PASSWORD_9f3a", text)
        self.assertNotIn("SENTINEL_WANDB_KEY_9f3a", text)

    def test_project_path_env_overrides_team_pair_and_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "only.config"), "w", encoding="utf-8") as handle:
                handle.write("WANDB_PROJECT_PATH=file/path\nWANDB_TEAM=t\nWANDB_PROJECT=p\n")
            settings = self._load(
                directory,
                {"WANDB_PROJECT_PATH": "explicit/path", "WANDB_TEAM": "t", "WANDB_PROJECT": "p"},
            )
        self.assertEqual(settings.wandb_project_path, "explicit/path")

    def test_partial_env_team_does_not_mix_with_file_project(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "only.config"), "w", encoding="utf-8") as handle:
                handle.write("WANDB_TEAM=fileteam\nWANDB_PROJECT=fileproj\n")
            settings = self._load(directory, {"WANDB_TEAM": "envteam"})
        self.assertEqual(settings.wandb_project_path, "fileteam/fileproj")

    def test_missing_config_dir_uses_defaults(self):
        settings = self._load(os.path.join(tempfile.gettempdir(), "alert-builder-missing-config"), {})
        self.assertEqual(settings.vss_url, "")
        self.assertEqual(settings.vss_password, "")
        self.assertEqual(settings.wandb_project_path, "")
        self.assertEqual(settings.port, 8080)
        self.assertFalse(settings.mock)
        self.assertEqual(settings.data_dir, "/tmp/alert-builder")

    def test_multiple_config_files_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("a.config", "b.config"):
                with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
                    handle.write("USERNAME=u\n")
            with self.assertRaises(ValueError):
                self._load(directory, {})

    def test_file_vss_url_beats_file_ingress(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "team.config"), "w", encoding="utf-8") as handle:
                handle.write("INGRESS_URL=https://ingress.example\nVSS_URL=https://vss.example\n")
            settings = self._load(directory, {})
        self.assertEqual(settings.vss_url, "https://vss.example")


class FakeVSSClientTests(unittest.TestCase):
    def setUp(self):
        self.client = fixtures.FakeVSSClient("https://example.test/", "user", "pw")

    def test_catalog_shape(self):
        videos = self.client.list_videos()
        self.assertEqual(
            [(video["camera_id"], video["location"]) for video in videos],
            [
                ("sdg_warehouse_cam-2", "warehouse3"),
                ("pie_cam-3", "toronto"),
                ("i24_cam-1", "nashville"),
            ],
        )
        self.assertTrue(self.client.ping())
        self.assertEqual(self.client.base_url, "https://example.test")
        for video in videos:
            segments = self.client.segments(video["original_video"])
            self.assertGreaterEqual(len(segments), 10)
            self.assertLessEqual(len(segments), 14)
            self.assertEqual(video["total_segments"], len(segments))
            self.assertEqual(video["duration_sec"], len(segments) * 5)
            numbers = [segment["segment_number"] for segment in segments]
            self.assertEqual(numbers, sorted(numbers))
            for segment in segments:
                self.assertEqual(segment["end_sec"] - segment["start_sec"], 5.0)
                self.assertEqual(segment["camera_id"], video["camera_id"])
                self.assertEqual(segment["location"], video["location"])
                self.assertIsNone(segment["similarity"])

    def test_scenario_captions_and_yolo_limits(self):
        videos = {video["camera_id"]: video for video in self.client.list_videos()}
        warehouse = self.client.segments(videos["sdg_warehouse_cam-2"]["original_video"])
        moving = [
            segment
            for segment in warehouse
            if "moving forklift" in segment["caption"]
        ]
        self.assertGreaterEqual(len(moving), 3)
        for segment in moving:
            self.assertEqual(set(segment["object_counts"]), {"person"})
            self.assertNotIn("forklift", segment["object_classes"])
            self.assertGreaterEqual(segment["object_counts"]["person"], 1)
        self.assertTrue(any("forklift" not in segment["caption"].lower() for segment in warehouse))
        self.assertTrue(all("forklift" not in segment["object_classes"] for segment in warehouse))

        dashcam = self.client.segments(videos["pie_cam-3"]["original_video"])
        crossings = [segment for segment in dashcam if "steps into the road" in segment["caption"]]
        self.assertGreaterEqual(len(crossings), 2)
        for segment in crossings:
            self.assertIn("person", segment["object_counts"])
            self.assertIn("car", segment["object_counts"])

        highway = self.client.segments(videos["i24_cam-1"]["original_video"])
        changes = [segment for segment in highway if "changes lanes" in segment["caption"]]
        self.assertGreaterEqual(len(changes), 2)
        for segment in changes:
            self.assertIn("truck", segment["object_counts"])
            self.assertIn("car", segment["object_counts"])

    def test_search_filters_threshold_and_copies(self):
        hits = self.client.search("forklift", top_k=2, min_similarity=0.5)
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(hit["similarity"] == 1.0 for hit in hits))
        self.assertTrue(all("forklift" in hit["caption"] for hit in hits))
        self.assertLessEqual(hits[1]["similarity"], hits[0]["similarity"])

        scoped = self.client.search(
            "forklift",
            top_k=20,
            min_similarity=0.1,
            metadata_filters={"camera_id": "pie_cam-3"},
        )
        self.assertEqual(scoped, [])
        toronto = self.client.search(
            "pedestrian",
            top_k=20,
            min_similarity=0.5,
            metadata_filters={"location": "toronto"},
        )
        self.assertTrue(toronto)
        self.assertTrue(all(hit["location"] == "toronto" for hit in toronto))
        mismatched = self.client.search(
            "pedestrian",
            metadata_filters={"camera_id": "sdg_warehouse_cam-2", "location": "toronto"},
        )
        self.assertEqual(mismatched, [])
        partial = self.client.search("forklift zebra", top_k=20, min_similarity=0.51)
        self.assertEqual(partial, [])
        included = self.client.search("forklift zebra", top_k=20, min_similarity=0.5)
        self.assertTrue(included)
        self.assertTrue(all(0.0 <= hit["similarity"] <= 1.0 for hit in included))

        hits[0]["caption"] = "mutated"
        hits[0]["object_counts"]["person"] = 99
        again = self.client.search("forklift", top_k=1, min_similarity=0.5)
        self.assertNotEqual(again[0]["caption"], "mutated")
        self.assertNotEqual(again[0]["object_counts"].get("person"), 99)

    def test_stream_detections_and_unknown_video(self):
        status, headers, body = self.client.open_stream("s3://missing", "bytes=0-1")
        self.assertEqual(status, 404)
        self.assertEqual(headers, {})
        self.assertEqual(body.read(), b"")
        body.close()
        source = self.client.segments(self.client.list_videos()[0]["original_video"])[1]["source"]
        payload = self.client.detections(source)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["source"], source)
        self.assertIn("frames", payload)
        self.assertEqual(self.client.segments("s3://missing"), [])


class VSSClientTests(unittest.TestCase):
    def _client(self) -> vss_client.VSSClient:
        return vss_client.VSSClient("https://vss.example/", "user", "pw-secret", timeout=5)

    def test_login_and_relogin_on_401(self):
        explore = {
            "chunks": [
                {
                    "original_video": "s3://bucket/a.mp4",
                    "filename": "a.mp4",
                    "camera_id": "sdg_warehouse_cam-2",
                    "location": "warehouse3",
                    "capture_type": "cctv",
                    "total_segments": 1,
                    "chunk_duration_sec": 5,
                    "upload_timestamp": "2026-09-01T12:00:00",
                    "preview_source": "s3://bucket/seg.mp4",
                }
            ],
            "total": 1,
        }
        calls = []

        def urlopen(req, timeout=None):
            calls.append(req)
            if req.full_url.endswith("/api/v1/auth/login"):
                body = json.loads(req.data.decode())
                self.assertEqual(body["username"], "user")
                self.assertEqual(body["password"], "pw-secret")
                index = sum(1 for item in calls if item.full_url.endswith("/api/v1/auth/login"))
                return _Response({"access_token": f"tok{index}"})
            self.assertIn("/api/v1/videos/explore", req.full_url)
            parsed = urllib.parse.urlparse(req.full_url)
            query = urllib.parse.parse_qs(parsed.query)
            self.assertEqual(query["scope"], ["all"])
            self.assertEqual(query["limit"], ["100"])
            auth = req.get_header("Authorization")
            if auth == "Bearer tok1":
                raise _http_error(401, body=b'{"detail":"expired pw-secret"}')
            self.assertEqual(auth, "Bearer tok2")
            self.assertNotIn("pw-secret", req.full_url)
            return _Response(explore)

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            videos = self._client().list_videos()
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["duration_sec"], 5.0)
        self.assertEqual(videos[0]["camera_id"], "sdg_warehouse_cam-2")
        logins = [req for req in calls if req.full_url.endswith("/api/v1/auth/login")]
        explores = [req for req in calls if "/videos/explore" in req.full_url]
        self.assertEqual(len(logins), 2)
        self.assertEqual(len(explores), 2)
        self.assertTrue(logins[0].full_url.startswith("https://vss.example/api/v1/auth/login"))
        self.assertNotIn("//api/", logins[0].full_url.replace("https://", ""))

    def test_second_401_does_not_login_forever(self):
        calls = {"login": 0}

        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                calls["login"] += 1
                return _Response({"access_token": f"tok{calls['login']}"})
            raise _http_error(401)

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            with self.assertRaises(vss_client.VSSError) as caught:
                self._client().list_videos()
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("pw-secret", str(caught.exception))
        self.assertEqual(calls["login"], 2)

    def test_login_failure_masks_secret_and_ping_is_false(self):
        def urlopen(req, timeout=None):
            raise _http_error(401, body=b'{"password":"pw-secret"}')

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            client = self._client()
            self.assertFalse(client.ping())
            with self.assertRaises(vss_client.VSSError) as caught:
                client.list_videos()
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("pw-secret", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_token_is_cached(self):
        calls = {"login": 0}

        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                calls["login"] += 1
                return _Response({"access_token": "tok"})
            raise AssertionError(req.full_url)

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            client = self._client()
            self.assertTrue(client.ping())
            self.assertTrue(client.ping())
        self.assertEqual(calls["login"], 1)

    def test_explore_paginates_until_total_and_caps_at_1000(self):
        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                return _Response({"access_token": "tok"})
            query = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
            offset = int(query["offset"][0])
            self.assertEqual(query["limit"], ["100"])
            chunks = [
                {
                    "original_video": f"s3://bucket/{offset + index}.mp4",
                    "filename": f"{offset + index}.mp4",
                    "chunk_duration_sec": 5,
                    "total_segments": 1,
                }
                for index in range(100)
            ]
            return _Response({"chunks": chunks, "total": 5000})

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            videos = self._client().list_videos()
        self.assertEqual(len(videos), 1000)
        self.assertEqual(videos[0]["original_video"], "s3://bucket/0.mp4")
        self.assertEqual(videos[-1]["original_video"], "s3://bucket/999.mp4")

    def test_explore_follows_short_pages_until_total(self):
        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                return _Response({"access_token": "tok"})
            query = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
            offset = int(query["offset"][0])
            if offset == 0:
                chunks = [{"original_video": "s3://bucket/a.mp4", "chunk_duration_sec": 10, "filename": "a.mp4"}]
            elif offset == 1:
                chunks = [{"original_video": "s3://bucket/b.mp4", "chunk_duration_sec": 15, "filename": "b.mp4"}]
            else:
                chunks = []
            return _Response({"chunks": chunks, "total": 2})

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            videos = self._client().list_videos()
        self.assertEqual([video["original_video"] for video in videos], ["s3://bucket/a.mp4", "s3://bucket/b.mp4"])
        self.assertEqual(videos[1]["duration_sec"], 15.0)

    def test_segments_are_sorted_and_search_posts_cheap_body(self):
        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                return _Response({"access_token": "tok"})
            if "/api/v1/tools/segments" in req.full_url:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
                self.assertEqual(query["original_video"], ["s3://bucket/a b.mp4"])
                return _Response(
                    {
                        "original_video": "s3://bucket/a b.mp4",
                        "total": 2,
                        "segments": [
                            {
                                "segment_number": 2,
                                "reasoning_content": "later",
                                "segment_start_sec": 10,
                                "segment_end_sec": 15,
                                "object_classes": "person",
                                "object_counts": "",
                            },
                            {
                                "segment_number": 0,
                                "reasoning_content": "earlier",
                                "segment_start_sec": 0,
                                "segment_end_sec": 5,
                                "object_counts": '{"person": 1}',
                            },
                        ],
                    }
                )
            if req.full_url.endswith("/api/v1/search"):
                body = json.loads(req.data.decode())
                self.assertEqual(body["llm_top_n"], 1)
                self.assertEqual(body["include_public"], True)
                self.assertEqual(body["metadata_filters"], {"camera_id": "cam"})
                self.assertEqual(body["top_k"], 5)
                self.assertEqual(body["min_similarity"], 0.3)
                self.assertNotIn("pw-secret", req.full_url)
                return _Response(
                    {
                        "results": [
                            {
                                "reasoning_content": "hit",
                                "similarity_score": 0.8,
                                "segment_number": 1,
                                "object_classes": "car",
                                "object_counts": '{"car": 1}',
                            }
                        ]
                    }
                )
            raise AssertionError(req.full_url)

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            client = self._client()
            segments = client.segments("s3://bucket/a b.mp4")
            hits = client.search("person", top_k=5, min_similarity=0.3, metadata_filters={"camera_id": "cam"})
        self.assertEqual([segment["segment_number"] for segment in segments], [0, 2])
        self.assertEqual(segments[0]["original_video"], "s3://bucket/a b.mp4")
        self.assertEqual(segments[0]["object_counts"], {"person": 1})
        self.assertEqual(segments[1]["object_counts"], {"person": 1})
        self.assertEqual(hits[0]["similarity"], 0.8)
        self.assertEqual(hits[0]["caption"], "hit")

    def test_detections_404_is_none_and_500_raises(self):
        mode = {"status": 404}

        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                return _Response({"access_token": "tok"})
            self.assertIn("/api/v1/videos/detections", req.full_url)
            if mode["status"] == 404:
                raise _http_error(404)
            if mode["status"] == 500:
                raise _http_error(500)
            return _Response({"frames": []})

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            client = self._client()
            self.assertIsNone(client.detections("s3://bucket/seg.mp4"))
            mode["status"] = 200
            self.assertEqual(client.detections("s3://bucket/seg.mp4"), {"frames": []})
            mode["status"] = 500
            with self.assertRaises(vss_client.VSSError) as caught:
                client.detections("s3://bucket/seg.mp4")
        self.assertEqual(caught.exception.status, 500)
        self.assertNotIn("pw-secret", str(caught.exception))

    def test_open_stream_forwards_range_and_retries_401(self):
        calls = {"login": 0, "stream": 0}

        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                calls["login"] += 1
                return _Response({"access_token": f"tok{calls['login']}"})
            self.assertIn("/api/v1/videos/stream", req.full_url)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
            self.assertEqual(query["source"], ["s3://bucket/seg.mp4"])
            calls["stream"] += 1
            self.assertEqual(req.get_header("Range"), "bytes=0-5")
            if query["token"] == ["tok1"]:
                raise _http_error(401)
            self.assertEqual(query["token"], ["tok2"])
            return _Response(
                None,
                status=206,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": "6",
                    "Content-Range": "bytes 0-5/10",
                    "Accept-Ranges": "bytes",
                    "X-Token": "tok2",
                },
                body=b"abcdef",
            )

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            status, headers, body = self._client().open_stream("s3://bucket/seg.mp4", "bytes=0-5")
        self.assertEqual(status, 206)
        self.assertEqual(
            headers,
            {
                "Content-Type": "video/mp4",
                "Content-Length": "6",
                "Content-Range": "bytes 0-5/10",
                "Accept-Ranges": "bytes",
            },
        )
        self.assertEqual(body.read(), b"abcdef")
        self.assertEqual(calls["login"], 2)
        self.assertEqual(calls["stream"], 2)

    def test_open_stream_returns_416_without_rereading(self):
        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                return _Response({"access_token": "tok"})
            raise _http_error(416, {"Content-Range": "bytes */10", "X-Secret": "nope"}, b"nope")

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            status, headers, body = self._client().open_stream("s3://bucket/seg.mp4", "bytes=50-60")
        self.assertEqual(status, 416)
        self.assertEqual(headers, {"Content-Range": "bytes */10"})
        self.assertNotIn("X-Secret", headers)
        self.assertEqual(body.read(), b"nope")

    def test_stream_url_error_does_not_include_token(self):
        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                return _Response({"access_token": "super-token-value"})
            raise urllib.error.URLError("timed out")

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            with self.assertRaises(vss_client.VSSError) as caught:
                self._client().open_stream("s3://bucket/seg.mp4")
        self.assertNotIn("super-token-value", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.status)

    def test_concurrent_calls_login_once(self):
        gate = threading.Event()
        calls = {"login": 0}

        def urlopen(req, timeout=None):
            if req.full_url.endswith("/api/v1/auth/login"):
                calls["login"] += 1
                gate.wait(1)
                return _Response({"access_token": "tok"})
            return _Response({"chunks": [], "total": 0})

        with mock.patch("vss_client.urllib.request.urlopen", side_effect=urlopen):
            client = self._client()
            errors = []

            def work():
                try:
                    client.list_videos()
                except Exception as exc:  # pragma: no cover - surfaced below
                    errors.append(exc)

            first = threading.Thread(target=work)
            second = threading.Thread(target=work)
            first.start()
            second.start()
            gate.set()
            first.join(2)
            second.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(calls["login"], 1)


if __name__ == "__main__":
    unittest.main()
