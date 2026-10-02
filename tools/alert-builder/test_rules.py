"""Offline tests for rule compile, validation, and prefilter."""

import unittest
from datetime import datetime

from llm import COMPILE_MARKER, FakeLLM, LLMClient
from rules import (
    COCO_CLASSES,
    compile_rule,
    heuristic_compile,
    new_rule_id,
    prefilter,
    validate_rule,
)


def _segment(**overrides):
    segment = {
        "source": "s3://seg.mp4",
        "original_video": "s3://video.mp4",
        "segment_number": 3,
        "start_sec": 15.0,
        "end_sec": 20.0,
        "caption": "A person walks near a moving forklift.",
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


class TestCoco(unittest.TestCase):
    def test_eighty_lowercase_classes(self):
        self.assertEqual(len(COCO_CLASSES), 80)
        self.assertEqual(len(set(COCO_CLASSES)), 80)
        self.assertEqual(COCO_CLASSES[0], "person")
        self.assertIn("toothbrush", COCO_CLASSES)
        self.assertIn("traffic light", COCO_CLASSES)
        self.assertIn("hair drier", COCO_CLASSES)
        self.assertTrue(all(name == name.lower() for name in COCO_CLASSES))

    def test_new_rule_id_shape(self):
        rule_id = new_rule_id()
        self.assertRegex(rule_id, r"^r_[0-9a-f]{6}$")


class TestPrefilter(unittest.TestCase):
    def test_camera_out_of_scope_wins_over_counts(self):
        rule = {
            "cameras": ["pie_cam-3"],
            "locations": [],
            "require_classes": {"person": 1},
            "caption_any": ["forklift"],
        }
        ok, reason = prefilter(rule, _segment())
        self.assertFalse(ok)
        self.assertEqual(reason, "camera out of scope")

    def test_location_out_of_scope(self):
        rule = {
            "cameras": [],
            "locations": ["toronto"],
            "require_classes": {"person": 1},
            "caption_any": [],
        }
        ok, reason = prefilter(rule, _segment())
        self.assertFalse(ok)
        self.assertEqual(reason, "location out of scope")

    def test_class_count(self):
        rule = {"cameras": [], "locations": [], "require_classes": {"person": 1}, "caption_any": []}
        ok, reason = prefilter(rule, _segment(object_counts={}, object_classes=[]))
        self.assertFalse(ok)
        self.assertEqual(reason, "person 0 < 1")

        ok, reason = prefilter(
            {"require_classes": {"person": 3}, "caption_any": []},
            _segment(object_counts={"person": 2}),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "person 2 < 3")

        ok, reason = prefilter(rule, _segment(object_counts={"Person": 2}))
        self.assertTrue(ok)
        self.assertEqual(reason, "person>=1")

    def test_plural_keyword(self):
        rule = {
            "require_classes": {"person": 1},
            "caption_any": ["forklift"],
            "cameras": [],
            "locations": [],
        }
        ok, reason = prefilter(rule, _segment(caption="Two FORKLIFTS pass the worker."))
        self.assertTrue(ok)
        self.assertEqual(reason, "person>=1, caption mentions forklift")

        ok, reason = prefilter(rule, _segment(caption="A forklifter drives by."))
        self.assertFalse(ok)
        self.assertEqual(reason, "no forklift in caption")

        ok, reason = prefilter(rule, _segment(caption="myforklift is painted yellow."))
        self.assertFalse(ok)

    def test_multiword_keyword(self):
        rule = {
            "require_classes": {},
            "caption_any": ["hard hat", "pallet jack"],
            "cameras": [],
            "locations": [],
        }
        ok, reason = prefilter(rule, _segment(caption="Worker wearing hard hats near the line."))
        self.assertTrue(ok)
        self.assertEqual(reason, "caption mentions hard hat")

        ok, reason = prefilter(rule, _segment(caption="A hardhat is on the bench."))
        self.assertFalse(ok)
        self.assertEqual(reason, "no hard hat/pallet jack in caption")

        ok, reason = prefilter(rule, _segment(caption="He left a hard hat on the pallet."))
        self.assertTrue(ok)

    def test_no_filter(self):
        ok, reason = prefilter({"cameras": [], "locations": [], "require_classes": {}, "caption_any": []}, _segment())
        self.assertTrue(ok)
        self.assertEqual(reason, "no filter")

    def test_in_scope_only(self):
        ok, reason = prefilter(
            {"cameras": ["sdg_warehouse_cam-2"], "locations": ["warehouse3"], "require_classes": {}, "caption_any": []},
            _segment(),
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "in scope")


class TestValidate(unittest.TestCase):
    def test_moves_forklift_and_coerces_counts(self):
        rule = validate_rule(
            {
                "require_classes": {"Person": "2", "forklift": 3, "truck": 0, "bus": "nope"},
                "caption_any": ["Forklift", "pallet jack", "pallet jack"],
                "cameras": ["cam-1", "cam-9", "CAM-1"],
                "locations": ["Warehouse3", "nope"],
                "enabled": False,
                "id": "r_abc123",
                "created_at": "2026-01-01T00:00:00+00:00",
                "name": "one two three four five six seven",
            },
            {"cameras": ["cam-1", "cam-2"], "locations": ["warehouse3"]},
        )
        self.assertEqual(rule["require_classes"], {"person": 2, "truck": 1, "bus": 1})
        self.assertNotIn("forklift", rule["require_classes"])
        self.assertEqual(rule["caption_any"], ["forklift", "pallet jack"])
        self.assertEqual(rule["cameras"], ["cam-1"])
        self.assertEqual(rule["locations"], ["warehouse3"])
        self.assertFalse(rule["enabled"])
        self.assertEqual(rule["id"], "r_abc123")
        self.assertEqual(rule["created_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(rule["name"], "one two three four five six")

    def test_keeps_cameras_when_vocab_empty(self):
        rule = validate_rule({"cameras": ["Cam-A"], "caption_any": []}, {"cameras": [], "locations": []})
        self.assertEqual(rule["cameras"], ["cam-a"])
        self.assertEqual(rule["id"], "")
        self.assertTrue(rule["enabled"])
        self.assertIn("5-second", rule["judge_question"])

    def test_defaults(self):
        rule = validate_rule({}, {})
        self.assertEqual(rule["name"], "Safety alert")
        self.assertEqual(rule["require_classes"], {})
        self.assertEqual(rule["caption_any"], [])
        self.assertTrue(rule["severity_guide"])
        self.assertTrue(rule["search_query"])


class TestHeuristic(unittest.TestCase):
    def test_person_near_forklift(self):
        rule = heuristic_compile(
            "Alert me when a person walks near a forklift",
            {"cameras": ["pie_cam-3"], "locations": ["toronto"], "classes": []},
        )
        self.assertEqual(rule["require_classes"], {"person": 1})
        self.assertIn("forklift", rule["caption_any"])
        self.assertNotIn("forklift", rule["require_classes"])
        self.assertEqual(rule["cameras"], [])
        self.assertEqual(rule["locations"], [])
        self.assertLessEqual(len(rule["name"].split()), 6)
        self.assertIn("forklift", rule["search_query"])
        self.assertIn("?", rule["judge_question"])
        self.assertIn("5", rule["severity_guide"])
        self.assertTrue(rule["enabled"])

    def test_synonyms_and_empty_caption_when_no_non_coco(self):
        rule = heuristic_compile("Alert me when a lorry is close to a bike", {})
        self.assertEqual(rule["require_classes"], {"truck": 1, "bicycle": 1})
        self.assertEqual(rule["caption_any"], [])

        people = heuristic_compile("Alert me when people gather by the door", {})
        self.assertEqual(people["require_classes"], {"person": 1})
        self.assertIn("door", people["caption_any"])

        steps = heuristic_compile("Alert me when a pedestrian steps into the road", {})
        self.assertEqual(steps["require_classes"], {"person": 1})
        self.assertEqual(steps["caption_any"], [])

    def test_hard_hat_and_lane(self):
        hats = heuristic_compile("Alert me when a worker is missing a hard hat", {})
        self.assertEqual(hats["require_classes"], {"person": 1})
        self.assertIn("hard hat", hats["caption_any"])

        lanes = heuristic_compile("Alert me when a truck changes lanes in dense traffic", {})
        self.assertEqual(lanes["require_classes"], {"truck": 1})
        self.assertIn("lane", lanes["caption_any"])

    def test_scopes_only_when_named(self):
        vocab = {"cameras": ["pie_cam-3", "i24_cam-1"], "locations": ["toronto", "nashville"]}
        rule = heuristic_compile("Alert me when a person walks near a forklift on pie_cam-3", vocab)
        self.assertEqual(rule["cameras"], ["pie_cam-3"])
        self.assertEqual(rule["locations"], [])


class TestCompile(unittest.TestCase):
    def test_prompt_marker_and_coco_guidance(self):
        class Spy:
            def __init__(self):
                self.messages = None

            def chat_json(self, messages, temperature=0.0, max_tokens=800):
                self.messages = messages
                return {
                    "name": "Person near forklift",
                    "require_classes": {"person": 1, "forklift": 2},
                    "caption_any": ["pallet jack"],
                    "search_query": "person walking near a forklift",
                    "judge_question": "Is a person near a forklift in this 5-second segment?",
                    "severity_guide": "5 = collision, 1 = none",
                    "cameras": ["not-a-camera"],
                }

        spy = Spy()
        vocab = {"cameras": ["pie_cam-3"], "locations": ["toronto"]}
        rule = compile_rule(spy, "Alert me when a person walks near a forklift", vocab)
        blob = "\n".join(str(message.get("content")) for message in spy.messages)
        self.assertIn(COMPILE_MARKER, blob)
        self.assertIn("COCO", blob)
        self.assertIn("forklift", blob)
        self.assertIn("caption_any", blob)
        self.assertIn("hard hat", blob)
        self.assertIn("USER_TEXT_BEGIN", blob)
        self.assertIn("Alert me when a person walks near a forklift", blob)
        self.assertIn("pie_cam-3", blob)
        self.assertEqual(rule["require_classes"], {"person": 1})
        self.assertIn("forklift", rule["caption_any"])
        self.assertIn("pallet jack", rule["caption_any"])
        self.assertEqual(rule["cameras"], [])
        self.assertEqual(rule["text"], "Alert me when a person walks near a forklift")
        self.assertTrue(rule["enabled"])
        self.assertRegex(rule["id"], r"^r_[0-9a-f]{6}$")
        datetime.fromisoformat(rule["created_at"])

    def test_fallback_when_llm_raises(self):
        class Raising:
            def chat_json(self, messages, temperature=0.0, max_tokens=800):
                raise RuntimeError("llm down")

        rule = compile_rule(
            Raising(),
            "Alert me when a person walks near a forklift",
            {"cameras": [], "locations": [], "classes": COCO_CLASSES},
        )
        self.assertEqual(rule["require_classes"], {"person": 1})
        self.assertIn("forklift", rule["caption_any"])
        self.assertTrue(rule["enabled"])
        self.assertEqual(rule["text"], "Alert me when a person walks near a forklift")
        self.assertRegex(rule["id"], r"^r_[0-9a-f]{6}$")
        datetime.fromisoformat(rule["created_at"])

    def test_fakellm_compile(self):
        rule = compile_rule(
            FakeLLM("", "", ""),
            "Alert me when a person walks near a forklift",
            {"cameras": [], "locations": []},
        )
        self.assertEqual(rule["require_classes"], {"person": 1})
        self.assertIn("forklift", rule["caption_any"])
        self.assertEqual(FakeLLM("", "", "", model="other").model(), "fake-llm")


class TestChatJson(unittest.TestCase):
    def test_strips_think_blocks_and_fences(self):
        class Stub(LLMClient):
            def chat(self, messages, temperature=0.1, max_tokens=800):
                return (
                    '<think>ignore {"nope": false} and do not use it</think>\n'
                    "```json\n"
                    '{"match": true, "severity": 4, "a": {"b": 1}, "c": "brace } inside"}\n'
                    "```\n"
                )

        stub = Stub("http://127.0.0.1", "super-secret-key", "team/proj", model="stub")
        parsed = stub.chat_json([{"role": "user", "content": "judge this"}])
        self.assertTrue(parsed["match"])
        self.assertEqual(parsed["severity"], 4)
        self.assertEqual(parsed["a"], {"b": 1})
        self.assertEqual(parsed["c"], "brace } inside")
        self.assertNotIn("nope", parsed)
        self.assertNotIn("super-secret-key", str(parsed))

    def test_retries_once_then_parses(self):
        class Stub(LLMClient):
            def __init__(self):
                super().__init__("http://127.0.0.1", "super-secret-key", "", model="stub")
                self.seen = []

            def chat(self, messages, temperature=0.1, max_tokens=800):
                self.seen.append(messages)
                if len(self.seen) == 1:
                    return "I cannot decide."
                return '```json\n{"ok": true}\n```'

        stub = Stub()
        self.assertEqual(stub.chat_json([{"role": "user", "content": "q"}]), {"ok": True})
        self.assertEqual(len(stub.seen), 2)
        retry_blob = "\n".join(str(message.get("content")) for message in stub.seen[1])
        self.assertIn("Return ONLY a valid JSON object.", retry_blob)

    def test_second_failure_raises_without_key(self):
        class Stub(LLMClient):
            def chat(self, messages, temperature=0.1, max_tokens=800):
                return "still not json"

        stub = Stub("http://127.0.0.1", "super-secret-key", "team/proj", model="stub")
        with self.assertRaises(Exception) as caught:
            stub.chat_json([{"role": "user", "content": "q"}])
        self.assertNotIn("super-secret-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
