"""Offline tests for the LLM client, fake judge, and evaluator."""

import io
import json
import os
import sys
import types
import unittest
from unittest import mock
import urllib.error

from judge import Evaluator, judge, write_report
from llm import JUDGE_MARKER, REPORT_MARKER, FakeLLM, LLMClient, LLMError, init_tracing, traced
from rules import heuristic_compile


def _rule(**overrides):
    rule = heuristic_compile(
        "Alert me when a person walks near a forklift",
        {"cameras": [], "locations": []},
    )
    rule["id"] = "r_fork01"
    rule["severity_guide"] = "5 = contact or imminent collision; 1 = distant, no risk"
    rule.update(overrides)
    return rule


def _segment(**overrides):
    segment = {
        "source": "s3://warehouse/seg-3.mp4",
        "original_video": "s3://warehouse/video.mp4",
        "segment_number": 3,
        "start_sec": 15.0,
        "end_sec": 20.0,
        "caption": "A worker walks near a moving forklift in the aisle.",
        "object_counts": {"person": 1},
        "object_classes": ["person"],
        "camera_id": "sdg_warehouse_cam-2",
        "location": "warehouse3",
        "capture_type": "cctv",
        "upload_timestamp": "2026-09-01T12:00:00",
        "similarity": None,
    }
    segment.update(overrides)
    return segment


class _Body:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestLLMClient(unittest.TestCase):
    def test_prefers_listed_model_and_caches(self):
        payload = json.dumps(
            {
                "data": [
                    {"id": "openai/gpt-oss-120b"},
                    {"id": "meta-llama/Llama-3.3-70B-Instruct"},
                    {"id": "other/model"},
                ]
            }
        ).encode()
        calls = {"n": 0}

        def urlopen(req, timeout=60):
            calls["n"] += 1
            self.assertTrue(req.full_url.endswith("/models"))
            names = {key.lower(): value for key, value in req.header_items()}
            self.assertEqual(names["authorization"], "Bearer test-key")
            self.assertEqual(names["content-type"], "application/json")
            self.assertEqual(names["openai-project"], "team/proj")
            self.assertIn("OpenAI-Project", req.headers)
            return _Body(payload)

        client = LLMClient("https://example.test/v1/", "test-key", "team/proj")
        with mock.patch("llm.urllib.request.urlopen", side_effect=urlopen):
            self.assertEqual(client.model(), "meta-llama/Llama-3.3-70B-Instruct")
            self.assertEqual(client.model(), "meta-llama/Llama-3.3-70B-Instruct")
        self.assertEqual(calls["n"], 1)
        self.assertNotIn("test-key", repr(client))

    def test_first_listed_when_none_preferred(self):
        payload = json.dumps({"data": [{"id": "custom/first"}, {"id": "custom/second"}]}).encode()

        def urlopen(req, timeout=60):
            return _Body(payload)

        client = LLMClient("https://example.test/v1", "k", "")
        with mock.patch("llm.urllib.request.urlopen", side_effect=urlopen):
            self.assertEqual(client.model(), "custom/first")
            names = {}
            # A second client call checks the project header is omitted when empty.
            seen = {}

            def capture(req, timeout=60):
                seen["headers"] = {key: value for key, value in req.header_items()}
                return _Body(payload)

        client2 = LLMClient("https://example.test/v1", "k", "")
        with mock.patch("llm.urllib.request.urlopen", side_effect=capture):
            client2.model()
        self.assertNotIn("OpenAI-Project", seen["headers"])
        self.assertNotIn("Openai-project", seen["headers"])

    def test_explicit_model_skips_network(self):
        client = LLMClient("https://example.test/v1", "k", "p", model="custom-model")
        with mock.patch("llm.urllib.request.urlopen") as urlopen:
            self.assertEqual(client.model(), "custom-model")
            urlopen.assert_not_called()

    def test_empty_model_list_raises(self):
        def urlopen(req, timeout=60):
            return _Body(b'{"data": []}')

        client = LLMClient("https://example.test/v1", "super-secret-key", "p")
        with mock.patch("llm.urllib.request.urlopen", side_effect=urlopen):
            with self.assertRaises(LLMError) as caught:
                client.model()
        self.assertNotIn("super-secret-key", str(caught.exception))

    def test_chat_posts_and_retries_server_errors(self):
        calls = {"n": 0}

        def urlopen(req, timeout=60):
            calls["n"] += 1
            self.assertTrue(req.full_url.endswith("/chat/completions"))
            body = json.loads(req.data.decode())
            self.assertEqual(body["model"], "custom-model")
            self.assertEqual(body["temperature"], 0.1)
            self.assertEqual(body["max_tokens"], 40)
            self.assertEqual(body["messages"][0]["content"], "hi")
            if calls["n"] == 1:
                raise urllib.error.HTTPError(
                    req.full_url,
                    503,
                    "super-secret-key",
                    {},
                    io.BytesIO(b"super-secret-key"),
                )
            return _Body(b'{"choices":[{"message":{"content":"hello"}}]}')

        client = LLMClient("https://example.test/v1", "super-secret-key", "team/proj", model="custom-model")
        with mock.patch("llm.urllib.request.urlopen", side_effect=urlopen), mock.patch("llm.time.sleep"):
            self.assertEqual(client.chat([{"role": "user", "content": "hi"}], max_tokens=40), "hello")
        self.assertEqual(calls["n"], 2)

    def test_chat_blank_content_and_no_retry_on_400(self):
        client = LLMClient("https://example.test/v1", "super-secret-key", "", model="m")

        def blank(req, timeout=60):
            return _Body(b'{"choices":[{"message":{"content":null}}]}')

        with mock.patch("llm.urllib.request.urlopen", side_effect=blank):
            self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), "")

        calls = {"n": 0}

        def denied(req, timeout=60):
            calls["n"] += 1
            raise urllib.error.HTTPError(req.full_url, 400, "super-secret-key", {}, io.BytesIO(b"super-secret-key"))

        with mock.patch("llm.urllib.request.urlopen", side_effect=denied):
            with self.assertRaises(LLMError) as caught:
                client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(calls["n"], 1)
        self.assertNotIn("super-secret-key", str(caught.exception))


class TestTracing(unittest.TestCase):
    def test_inactive_without_credentials(self):
        self.assertFalse(init_tracing("", "key"))
        self.assertFalse(init_tracing("proj", ""))

        @traced
        def add(value):
            return value + 1

        self.assertEqual(add(2), 3)

    def test_lazy_weave_op_after_decoration(self):
        import llm

        saved_mod = sys.modules.get("weave")
        saved_key = os.environ.get("WANDB_API_KEY")
        events = {}
        module = types.ModuleType("weave")

        def _init(path):
            events["path"] = path

        def _op(fn):
            def wrapped(*args, **kwargs):
                events["op"] = True
                return fn(*args, **kwargs)

            return wrapped

        module.init = _init
        module.op = _op
        sys.modules["weave"] = module
        llm._tracing = False
        llm._weave = None
        llm._op_cache.clear()
        try:
            os.environ.pop("WANDB_API_KEY", None)

            @llm.traced
            def double(value):
                return value * 2

            self.assertEqual(double(3), 6)
            self.assertFalse(events.get("op"))
            self.assertTrue(llm.init_tracing("team/proj", "wk"))
            self.assertEqual(events["path"], "team/proj")
            self.assertEqual(os.environ.get("WANDB_API_KEY"), "wk")
            self.assertEqual(double(4), 8)
            self.assertTrue(events.get("op"))
            os.environ["WANDB_API_KEY"] = "already"
            events.clear()

            def boom(_path):
                raise RuntimeError("offline")

            module.init = boom
            llm._tracing = False
            llm._weave = None
            self.assertFalse(llm.init_tracing("team/other", "new-key"))
            self.assertEqual(os.environ.get("WANDB_API_KEY"), "already")
        finally:
            sys.modules.pop("weave", None)
            if saved_mod is not None:
                sys.modules["weave"] = saved_mod
            llm._tracing = False
            llm._weave = None
            llm._op_cache.clear()
            if saved_key is None:
                os.environ.pop("WANDB_API_KEY", None)
            else:
                os.environ["WANDB_API_KEY"] = saved_key


class TestJudge(unittest.TestCase):
    def test_clamps_verdict(self):
        class Stub:
            def chat_json(self, messages, temperature=0.0, max_tokens=800):
                blob = "\n".join(str(message.get("content")) for message in messages)
                self.blob = blob
                return {"match": "yes", "severity": 99, "confidence": 2.5, "reason": "x" * 300}

            def model(self):
                return "stub"

        stub = Stub()
        verdict = judge(stub, _rule(), _segment())
        self.assertIn(JUDGE_MARKER, stub.blob)
        self.assertIn("A worker walks near a moving forklift", stub.blob)
        self.assertTrue(verdict["match"])
        self.assertEqual(verdict["severity"], 5)
        self.assertEqual(verdict["confidence"], 1.0)
        self.assertEqual(len(verdict["reason"]), 240)

        class Nope:
            def chat_json(self, messages, temperature=0.0, max_tokens=800):
                return {"match": "false", "severity": 0, "confidence": -1, "reason": "no"}

        verdict = judge(Nope(), _rule(), _segment())
        self.assertFalse(verdict["match"])
        self.assertEqual(verdict["severity"], 1)
        self.assertEqual(verdict["confidence"], 0.0)

    def test_fakellm_forklift_match(self):
        verdict = judge(FakeLLM("", "", ""), _rule(), _segment())
        self.assertTrue(verdict["match"])
        self.assertGreaterEqual(verdict["severity"], 2)
        self.assertLessEqual(verdict["severity"], 5)
        self.assertGreaterEqual(verdict["confidence"], 0.0)
        self.assertLessEqual(verdict["confidence"], 1.0)
        self.assertTrue(verdict["reason"])

    def test_fakellm_severity_and_ignores_prompt_words(self):
        fake = FakeLLM("", "", "")
        rule = _rule()
        parked = _segment(caption="A forklift sits parked in an empty aisle.")
        self.assertFalse(judge(fake, rule, parked)["match"])

        collision = _segment(caption="A person is near a forklift and a collision is imminent.")
        verdict = judge(fake, rule, collision)
        self.assertTrue(verdict["match"])
        self.assertEqual(verdict["severity"], 5)

        close = _segment(caption="A person is close to the moving forklift.")
        verdict = judge(fake, rule, close)
        self.assertTrue(verdict["match"])
        self.assertEqual(verdict["severity"], 3)

    def test_write_report_fake_and_fallback(self):
        rule = _rule()
        segment = _segment()
        verdict = {"match": True, "severity": 4, "confidence": 0.8, "reason": "Person is near the forklift."}
        report = write_report(FakeLLM("", "", ""), rule, segment, verdict)
        self.assertIn("What happened", report)
        self.assertIn("Where & when", report)
        self.assertIn("Risk", report)
        self.assertIn("Recommended action", report)
        self.assertIn("clear the zone", report)

        class Spy:
            def chat(self, messages, temperature=0.2, max_tokens=800):
                blob = "\n".join(str(message.get("content")) for message in messages)
                self.blob = blob
                return "<think>hidden</think>\n## What happened\nCustom body.\n"

        spy = Spy()
        cleaned = write_report(spy, rule, segment, verdict)
        self.assertIn(REPORT_MARKER, spy.blob)
        self.assertIn("Custom body.", cleaned)
        self.assertNotIn("<think>", cleaned)

        class Down:
            def chat(self, messages, temperature=0.2, max_tokens=800):
                raise RuntimeError("down")

        fallback = write_report(Down(), rule, segment, verdict)
        self.assertIn("What happened", fallback)
        self.assertIn("Recommended action", fallback)
        self.assertIn("site safety procedure", fallback)
        self.assertNotIn("clear the zone", fallback)


class TestEvaluator(unittest.TestCase):
    def test_skips_judge_when_prefilter_fails_and_keeps_order(self):
        calls = {"n": 0}

        class Counting:
            def chat_json(self, messages, temperature=0.0, max_tokens=800):
                calls["n"] += 1
                blob = "\n".join(str(message.get("content")) for message in messages)
                if "RULE_NAME: Slow" in blob:
                    return {"match": True, "severity": 2, "confidence": 0.4, "reason": "slow"}
                return {"match": True, "severity": 3, "confidence": 0.6, "reason": "fast"}

        slow = _rule(id="r_slow", name="Slow")
        fast = _rule(id="r_fast", name="Fast", require_classes={"person": 1}, caption_any=["forklift"])
        blocked = _rule(id="r_block", name="Blocked", require_classes={"person": 5}, caption_any=["forklift"])
        disabled = _rule(id="r_off", name="Off", enabled=False)
        evaluator = Evaluator(Counting(), max_workers=4)
        results = evaluator.evaluate([disabled, blocked, slow, fast], _segment())
        self.assertEqual([item["rule_id"] for item in results], ["r_block", "r_slow", "r_fast"])
        self.assertFalse(results[0]["prefilter_pass"])
        self.assertIsNone(results[0]["verdict"])
        self.assertIn("person", results[0]["prefilter_reason"])
        self.assertTrue(results[1]["prefilter_pass"])
        self.assertTrue(results[2]["verdict"]["match"])
        self.assertEqual(calls["n"], 2)

    def test_caches_on_rule_and_source(self):
        calls = {"n": 0}

        class Counting:
            def chat_json(self, messages, temperature=0.0, max_tokens=800):
                calls["n"] += 1
                return {"match": True, "severity": 3, "confidence": 0.5, "reason": "cached"}

        evaluator = Evaluator(Counting(), max_workers=2)
        rule = _rule()
        first = evaluator.evaluate([rule], _segment())
        second = evaluator.evaluate([rule], _segment(caption="Different wording about the same forklift clip."))
        self.assertEqual(calls["n"], 1)
        self.assertEqual(first[0]["verdict"]["reason"], second[0]["verdict"]["reason"])
        self.assertTrue(second[0]["prefilter_pass"])
        evaluator.clear_cache(rule["id"])
        third = evaluator.evaluate([rule], _segment())
        self.assertEqual(calls["n"], 2)
        self.assertTrue(third[0]["verdict"]["match"])
        evaluator.clear_cache()
        evaluator.evaluate([rule], _segment())
        self.assertEqual(calls["n"], 3)

    def test_judge_error_does_not_raise(self):
        class Bad:
            def chat_json(self, messages, temperature=0.0, max_tokens=800):
                raise RuntimeError("boom secret")

        results = Evaluator(Bad()).evaluate([_rule()], _segment())
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["prefilter_pass"])
        self.assertIsNone(results[0]["verdict"])
        self.assertIn("| judge error: boom secret", results[0]["prefilter_reason"])

    def test_empty_and_invalid_inputs(self):
        self.assertEqual(Evaluator(FakeLLM("", "", "")).evaluate([], _segment()), [])
        self.assertEqual(Evaluator(FakeLLM("", "", "")).evaluate(None, _segment()), [])


if __name__ == "__main__":
    unittest.main()
