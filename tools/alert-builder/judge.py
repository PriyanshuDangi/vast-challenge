"""LLM judge, incident report writer, and parallel rule evaluator."""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from llm import LLMError, traced
from rules import prefilter

JUDGE_MARKER = "TASK: JUDGE_SEGMENT"
REPORT_MARKER = "TASK: INCIDENT_REPORT"

_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*")


def _is_enabled(rule: dict) -> bool:
    if "enabled" not in rule or rule.get("enabled") is None:
        return True
    value = rule.get("enabled")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _clamp_severity(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(5, number))


def _clamp_confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return max(0.0, min(1.0, number))


def _as_match(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"yes", "true"}
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _normalize_verdict(data) -> dict:
    if not isinstance(data, dict):
        raise LLMError("verdict was not a JSON object")
    reason = "" if data.get("reason") is None else str(data.get("reason"))
    if len(reason) > 240:
        reason = reason[:240]
    return {
        "match": _as_match(data.get("match")),
        "severity": _clamp_severity(data.get("severity", 1)),
        "confidence": _clamp_confidence(data.get("confidence", 0.0)),
        "reason": reason,
    }


def _keywords(rule: dict) -> str:
    items = []
    for item in rule.get("caption_any") or []:
        text = str(item).strip()
        if text:
            items.append(text)
    return ", ".join(items)


def _one_line(value) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    for token in (
        "CAPTION_BEGIN",
        "CAPTION_END",
        "USER_TEXT_BEGIN",
        "USER_TEXT_END",
        "TASK: COMPILE_RULE",
        JUDGE_MARKER,
        REPORT_MARKER,
    ):
        text = text.replace(token, "")
    return text.strip()


def _clean_report(text: str) -> str:
    cleaned = _THINK_RE.sub("", text or "")
    cleaned = _FENCE_RE.sub("", cleaned)
    return cleaned.strip()


def _template_report(rule, segment, verdict) -> str:
    rule = rule if isinstance(rule, dict) else {}
    segment = segment if isinstance(segment, dict) else {}
    verdict = verdict if isinstance(verdict, dict) else {}
    reason = str(verdict.get("reason") or "").strip()
    caption = str(segment.get("caption") or "").strip()
    happened = reason or caption or str(rule.get("name") or "Alert")
    camera = segment.get("camera_id") or "unknown camera"
    location = segment.get("location") or "unknown location"
    start = segment.get("start_sec", "")
    end = segment.get("end_sec", "")
    severity = verdict.get("severity", "")
    return (
        f"## What happened\n{happened}\n\n"
        f"## Where & when\n{camera} at {location}, {start}s–{end}s.\n\n"
        f"## Risk\nSeverity {severity}. {reason}\n\n"
        "## Recommended action\nReview the clip and follow site safety procedure.\n"
    )


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip()
    line = text.splitlines()[0] if text else exc.__class__.__name__
    line = re.sub(r"\s+", " ", line).strip()
    if len(line) > 160:
        line = line[:160]
    return line


def _clone_result(item: dict) -> dict:
    out = dict(item)
    verdict = out.get("verdict")
    if isinstance(verdict, dict):
        out["verdict"] = dict(verdict)
    return out


@traced
def judge(llm, rule, segment) -> dict:
    """Return a Verdict for one rule on one segment."""
    rule = rule or {}
    segment = segment or {}
    counts = segment.get("object_counts") if isinstance(segment.get("object_counts"), dict) else {}
    caption = "" if segment.get("caption") is None else str(segment.get("caption"))
    lines = [
        JUDGE_MARKER,
        "Decide whether this single 5-second video segment satisfies the safety rule.",
        "Use only the caption, YOLO object counts, camera, location, and time range.",
        "Do not assume anything that is not described in this segment.",
        f"RULE_ID: {_one_line(rule.get('id'))}",
        f"RULE_NAME: {_one_line(rule.get('name'))}",
        f"RULE_TEXT: {_one_line(rule.get('text'))}",
        f"JUDGE_QUESTION: {_one_line(rule.get('judge_question'))}",
        f"SEVERITY_GUIDE: {_one_line(rule.get('severity_guide'))}",
        f"KEYWORDS: {_keywords(rule)}",
        "REQUIRE_CLASSES_JSON: " + json.dumps(rule.get("require_classes") or {}),
        f"CAMERA: {_one_line(segment.get('camera_id'))}",
        f"LOCATION: {_one_line(segment.get('location'))}",
        f"START_SEC: {_one_line(segment.get('start_sec', ''))}",
        f"END_SEC: {_one_line(segment.get('end_sec', ''))}",
        "OBJECT_COUNTS_JSON: " + json.dumps(counts),
        "CAPTION_BEGIN",
        caption,
        "CAPTION_END",
        'Return a JSON object with keys match (boolean), severity (integer 1-5), '
        'confidence (float 0 to 1), and reason (one short sentence).',
    ]
    messages = [
        {
            "role": "system",
            "content": "You judge safety events in one video segment. Return only JSON.",
        },
        {"role": "user", "content": "\n".join(lines)},
    ]
    data = llm.chat_json(messages, temperature=0.0, max_tokens=800)
    return _normalize_verdict(data)


@traced
def write_report(llm, rule, segment, verdict) -> str:
    """Write a short markdown incident report. Never raises."""
    try:
        rule = rule or {}
        segment = segment or {}
        verdict = verdict or {}
        caption = "" if segment.get("caption") is None else str(segment.get("caption"))
        lines = [
            REPORT_MARKER,
            "Write a concise markdown incident report of at most 120 words.",
            "Use exactly these sections:",
            "## What happened",
            "## Where & when",
            "## Risk",
            "## Recommended action",
            f"RULE_NAME: {_one_line(rule.get('name'))}",
            f"RULE_TEXT: {_one_line(rule.get('text'))}",
            f"CAMERA: {_one_line(segment.get('camera_id'))}",
            f"LOCATION: {_one_line(segment.get('location'))}",
            f"START_SEC: {_one_line(segment.get('start_sec', ''))}",
            f"END_SEC: {_one_line(segment.get('end_sec', ''))}",
            f"UPLOAD_TIMESTAMP: {_one_line(segment.get('upload_timestamp'))}",
            f"SEVERITY: {_one_line(verdict.get('severity', ''))}",
            f"CONFIDENCE: {_one_line(verdict.get('confidence', ''))}",
            f"REASON: {_one_line(verdict.get('reason'))}",
            "CAPTION_BEGIN",
            caption,
            "CAPTION_END",
        ]
        text = llm.chat(
            [
                {
                    "role": "system",
                    "content": "You write short safety incident reports in markdown.",
                },
                {"role": "user", "content": "\n".join(lines)},
            ],
            temperature=0.2,
            max_tokens=800,
        )
        cleaned = _clean_report("" if text is None else str(text))
        if not cleaned:
            return _template_report(rule, segment, verdict)
        return cleaned
    except Exception:
        return _template_report(rule, segment, verdict)


class Evaluator:
    def __init__(self, llm, max_workers=4):
        self.llm = llm
        self.max_workers = max_workers
        self._cache: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    def clear_cache(self, rule_id=None):
        with self._lock:
            if rule_id is None:
                self._cache.clear()
                return
            for key in [key for key in self._cache if key[0] == rule_id]:
                del self._cache[key]

    def _cached(self, key: tuple) -> dict | None:
        with self._lock:
            item = self._cache.get(key)
            return None if item is None else _clone_result(item)

    def _store(self, key: tuple, item: dict) -> None:
        with self._lock:
            self._cache[key] = _clone_result(item)

    def _judge_one(self, rule: dict, segment: dict, reason: str) -> dict:
        base = {
            "rule_id": rule.get("id"),
            "rule_name": rule.get("name") or "",
            "prefilter_pass": True,
            "prefilter_reason": reason,
            "verdict": None,
        }
        try:
            base["verdict"] = judge(self.llm, rule, segment)
        except Exception as exc:
            base["prefilter_reason"] = f"{reason} | judge error: {_short_error(exc)}"
            base["verdict"] = None
        return base

    def evaluate(self, rules, segment) -> list[dict]:
        """Prefilter every enabled rule; judge the passes. Never raises."""
        try:
            return self._evaluate(rules, segment)
        except Exception as exc:
            return [
                {
                    "rule_id": None,
                    "rule_name": "",
                    "prefilter_pass": False,
                    "prefilter_reason": f"evaluate error: {_short_error(exc)}",
                    "verdict": None,
                }
            ]

    def _evaluate(self, rules, segment) -> list[dict]:
        try:
            rule_list = list(rules or [])
        except TypeError:
            return []
        if not isinstance(segment, dict):
            segment = {}
        enabled = [rule for rule in rule_list if isinstance(rule, dict) and _is_enabled(rule)]

        prepared: list[tuple[dict, bool, str]] = []
        for rule in enabled:
            try:
                ok, reason = prefilter(rule, segment)
            except Exception as exc:
                ok, reason = False, f"prefilter error: {_short_error(exc)}"
            prepared.append((rule, bool(ok), reason))

        results: list[dict | None] = [None] * len(prepared)
        to_judge: list[int] = []
        for index, (rule, ok, reason) in enumerate(prepared):
            if not ok:
                results[index] = {
                    "rule_id": rule.get("id"),
                    "rule_name": rule.get("name") or "",
                    "prefilter_pass": False,
                    "prefilter_reason": reason,
                    "verdict": None,
                }
                continue
            key = (rule.get("id"), segment.get("source"))
            cached = self._cached(key)
            if cached is not None:
                results[index] = cached
            else:
                to_judge.append(index)

        if to_judge:
            workers = max(1, min(int(self.max_workers or 1), len(to_judge)))

            def _run(index: int):
                rule, _ok, reason = prepared[index]
                item = self._judge_one(rule, segment, reason)
                self._store((rule.get("id"), segment.get("source")), item)
                return index, item

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_run, index) for index in to_judge]
                for future in as_completed(futures):
                    try:
                        index, item = future.result()
                    except Exception as exc:
                        continue
                    results[index] = item

        out: list[dict] = []
        for index, item in enumerate(results):
            if item is None:
                rule, ok, reason = prepared[index]
                item = {
                    "rule_id": rule.get("id"),
                    "rule_name": rule.get("name") or "",
                    "prefilter_pass": bool(ok),
                    "prefilter_reason": reason,
                    "verdict": None,
                }
            out.append(item)
        return out
