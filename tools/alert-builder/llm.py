"""OpenAI-compatible W&B Inference client, offline fake, and optional weave tracing."""

from __future__ import annotations

import functools
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

PREFERRED_MODELS = (
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "meta-llama/Llama-3.3-70B-Instruct",
    "openai/gpt-oss-120b",
    "meta-llama/Llama-3.1-8B-Instruct",
)

COMPILE_MARKER = "TASK: COMPILE_RULE"
JUDGE_MARKER = "TASK: JUDGE_SEGMENT"
REPORT_MARKER = "TASK: INCIDENT_REPORT"
JSON_ONLY = "Return ONLY a valid JSON object."

_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*")

_tracing = False
_weave = None
_op_cache: dict[int, object] = {}
_op_lock = threading.Lock()


class LLMError(Exception):
    """Inference call failed, or the model did not return usable JSON."""


def _scrub(secret: str, message: str) -> str:
    if secret and secret in message:
        return message.replace(secret, "***")
    return message


def _copy_messages(messages) -> list[dict]:
    copied = []
    for message in messages or []:
        if isinstance(message, dict):
            copied.append(dict(message))
        else:
            copied.append({"role": "user", "content": str(message)})
    return copied


def _messages_text(messages) -> str:
    parts: list[str] = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else message
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    parts.append(str(block.get("text") or ""))
                else:
                    parts.append(str(block))
        elif content is not None:
            parts.append(str(content))
    return "\n".join(parts)


def _balanced_end(text: str, start: int) -> int | None:
    depth = 0
    in_str = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _extract_object(text: str) -> dict | None:
    idx = 0
    length = len(text)
    while idx < length:
        start = text.find("{", idx)
        if start < 0:
            return None
        end = _balanced_end(text, start)
        if end is None:
            idx = start + 1
            continue
        try:
            value = json.loads(text[start:end])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if isinstance(value, dict):
            return value
        idx = end
    return None


def _parse_json_content(text: str | None) -> dict | None:
    if text is None:
        return None
    cleaned = _THINK_RE.sub("", str(text))
    cleaned = _FENCE_RE.sub("", cleaned)
    return _extract_object(cleaned)


def _ensure_json_instruction(messages: list[dict]) -> None:
    needle = JSON_ONLY.lower()
    for message in messages:
        if needle in str(message.get("content") or "").lower():
            return
    messages.append({"role": "user", "content": JSON_ONLY})


def _model_ids(payload) -> list[str]:
    items = None
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            items = payload["data"]
        elif isinstance(payload.get("models"), list):
            items = payload["models"]
    elif isinstance(payload, list):
        items = payload
    ids: list[str] = []
    for item in items or []:
        if isinstance(item, str) and item.strip():
            ids.append(item.strip())
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name") or ""
            if str(model_id).strip():
                ids.append(str(model_id).strip())
    return ids


def _choose_model(ids: list[str]) -> str:
    if not ids:
        raise LLMError("no models available")
    available = set(ids)
    for preferred in PREFERRED_MODELS:
        if preferred in available:
            return preferred
    return ids[0]


def _between(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    if begin < 0:
        return ""
    begin += len(start)
    finish = text.find(end, begin)
    if finish < 0:
        return text[begin:].strip()
    return text[begin:finish].strip()


def _field(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:[ \t]*(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _json_list_after(text: str, label: str) -> list[str]:
    match = re.search(rf"{re.escape(label)}\s*(\[[^\n]*\])", text)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _split_csv(value: str) -> list[str]:
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def _caption_has_keyword(keyword: str, caption: str) -> bool:
    parts = keyword.lower().split()
    if not parts:
        return False
    bits = [re.escape(part) for part in parts[:-1]]
    last = parts[-1]
    last_pat = re.escape(last)
    if last.endswith(("s", "x", "z", "ch", "sh")):
        last_pat += r"(?:es|s)?"
    else:
        last_pat += r"s?"
    bits.append(last_pat)
    pattern = r"(?<![a-z0-9])" + r"\s+".join(bits) + r"(?![a-z0-9])"
    return re.search(pattern, caption.lower()) is not None


_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("changes lanes", re.compile(r"\bchang(?:e|es|ed|ing)\s+lanes?\b", re.IGNORECASE)),
    ("steps into", re.compile(r"\bstep(?:s|ped|ping)?\s+into\b", re.IGNORECASE)),
    ("approaching", re.compile(r"\bapproach\w*\b", re.IGNORECASE)),
    ("crossing", re.compile(r"\bcross(?:es|ed|ing)?\b", re.IGNORECASE)),
    ("swerves", re.compile(r"\bswerv\w*\b", re.IGNORECASE)),
    ("collision", re.compile(r"\bcollisions?\b|\bcollid(?:e|es|ed|ing)\b", re.IGNORECASE)),
    ("toward", re.compile(r"\btowards?\b", re.IGNORECASE)),
    ("nearly", re.compile(r"\bnearly\b", re.IGNORECASE)),
    ("sudden", re.compile(r"\bsudden\w*\b", re.IGNORECASE)),
    ("close", re.compile(r"\bclose\b", re.IGNORECASE)),
    ("near", re.compile(r"\bnear\b", re.IGNORECASE)),
    ("cuts", re.compile(r"\bcuts?\b", re.IGNORECASE)),
)

_STOPWORDS = {
    "alert", "notify", "warn", "tell", "please", "when", "whenever", "there",
    "this", "that", "with", "from", "into", "onto", "over", "under", "near",
    "close", "toward", "towards", "about", "after", "before", "while", "where",
    "your", "their", "them", "they", "someone", "something", "happens", "happen",
    "segment", "video", "camera", "footage", "second", "seconds", "does", "doing",
    "have", "has", "been", "being", "were", "was", "are", "and", "the", "for",
    "you", "me", "any", "all", "not", "but", "its", "it's",
}


def _fallback_keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9'-]{2,}", text.lower())
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word in _STOPWORDS or word in seen:
            continue
        if word.isdigit():
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= 8:
            break
    return out


def _danger_cue(caption: str) -> str:
    for label, pattern in _CUES:
        if pattern.search(caption):
            return label
    return ""


def _severity(caption: str) -> int:
    lowered = caption.lower()
    if re.search(r"\bcollisions?\b|\bcollid(?:e|es|ed|ing)\b", lowered):
        return 5
    if re.search(r"\bnearly\b", lowered) or re.search(r"\bsudden\w*\b", lowered):
        return 4
    if re.search(r"\bclose\b", lowered):
        return 3
    return 2


def _verdict(match: bool, severity: int, confidence: float, reason: str) -> dict:
    return {
        "match": bool(match),
        "severity": int(severity),
        "confidence": float(confidence),
        "reason": reason[:240],
    }


class LLMClient:
    def __init__(self, base_url, api_key, project_path, model="", timeout=60):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.project_path = project_path or ""
        self._model = (model or "").strip()
        self.timeout = timeout
        self._lock = threading.Lock()
        self._resolved = ""

    def __repr__(self) -> str:
        return (
            f"LLMClient(base_url={self.base_url!r}, project_path={self.project_path!r}, "
            f"model={self._model!r})"
        )

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self._url(path)
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        last_status = None
        for attempt in range(2):
            req = urllib.request.Request(url, data=data, method=method)
            # add_header capitalizes names ("OpenAI-Project" -> "Openai-project").
            # Content-type must be the key urllib checks, or it overwrites JSON
            # with form-urlencoded. The project header keeps its exact casing.
            req.add_header("Authorization", "Bearer " + self.api_key)
            req.add_header("Content-Type", "application/json")
            # W&B Inference is behind Cloudflare, which 403s urllib's default User-Agent (error 1010).
            req.add_header("User-Agent", "watchtower/1.0")
            if self.project_path:
                req.headers["OpenAI-Project"] = self.project_path
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as exc:
                try:
                    exc.read()
                except Exception:
                    pass
                last_status = exc.code
                if exc.code == 429 or exc.code >= 500:
                    if attempt == 0:
                        time.sleep(0.25)
                        continue
                raise LLMError(self._safe(f"HTTP {exc.code} calling {path}")) from None
            except urllib.error.URLError:
                raise LLMError(self._safe(f"connection error calling {path}")) from None
            except Exception:
                raise LLMError(self._safe(f"request failed calling {path}")) from None
            text = raw.decode("utf-8-sig") if raw else ""
            if not text.strip():
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                raise LLMError(self._safe(f"invalid JSON from {path}")) from None
            if isinstance(parsed, dict):
                return parsed
            raise LLMError(self._safe(f"unexpected payload from {path}"))
        raise LLMError(self._safe(f"HTTP {last_status} calling {path}"))

    def _safe(self, message: str) -> str:
        return _scrub(self.api_key, message)

    def model(self) -> str:
        if self._model:
            return self._model
        with self._lock:
            if self._resolved:
                return self._resolved
            payload = self._request("GET", "/models")
            chosen = _choose_model(_model_ids(payload))
            self._resolved = chosen
            return chosen

    def chat(self, messages, temperature=0.1, max_tokens=800) -> str:
        body = {
            "model": self.model(),
            "messages": list(messages or []),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = self._request("POST", "/chat/completions", body)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("chat response missing content") from None
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            bits = []
            for block in content:
                if isinstance(block, dict):
                    bits.append(str(block.get("text") or ""))
                else:
                    bits.append(str(block))
            return "".join(bits)
        return str(content)

    def chat_json(self, messages, temperature=0.0, max_tokens=800) -> dict:
        msgs = _copy_messages(messages)
        _ensure_json_instruction(msgs)
        parsed = _parse_json_content(self.chat(msgs, temperature=temperature, max_tokens=max_tokens))
        if parsed is not None:
            return parsed
        msgs.append({"role": "user", "content": JSON_ONLY})
        parsed = _parse_json_content(self.chat(msgs, temperature=temperature, max_tokens=max_tokens))
        if parsed is None:
            raise LLMError("model did not return valid JSON")
        return parsed


class FakeLLM:
    """Heuristic stand-in used when MOCK=1. Same surface as LLMClient."""

    def __init__(self, base_url="", api_key="", project_path="", model="", timeout=60):
        self.base_url = base_url or ""
        self.api_key = api_key or ""
        self.project_path = project_path or ""
        self._model = model or ""
        self.timeout = timeout

    def __repr__(self) -> str:
        return "FakeLLM()"

    def model(self) -> str:
        return "fake-llm"

    def _dispatch(self, messages):
        blob = _messages_text(messages)
        if REPORT_MARKER in blob:
            return "report", self._report(blob)
        if JUDGE_MARKER in blob:
            return "json", self._judge(blob)
        if COMPILE_MARKER in blob:
            return "json", self._compile(blob)
        return "text", ""

    def chat(self, messages, temperature=0.1, max_tokens=800) -> str:
        kind, payload = self._dispatch(messages)
        if kind == "json":
            return json.dumps(payload)
        return payload if isinstance(payload, str) else ""

    def chat_json(self, messages, temperature=0.0, max_tokens=800) -> dict:
        kind, payload = self._dispatch(messages)
        if kind == "json" and isinstance(payload, dict):
            return payload
        if kind == "report":
            return {"report": payload}
        parsed = _parse_json_content(payload if isinstance(payload, str) else "")
        if parsed is None:
            raise LLMError("model did not return valid JSON")
        return parsed

    def _compile(self, blob: str) -> dict:
        text = _between(blob, "USER_TEXT_BEGIN", "USER_TEXT_END")
        vocab = {
            "classes": [],
            "cameras": _json_list_after(blob, "VOCAB_CAMERAS_JSON:"),
            "locations": _json_list_after(blob, "VOCAB_LOCATIONS_JSON:"),
        }
        from rules import heuristic_compile

        return heuristic_compile(text, vocab)

    def _judge(self, blob: str) -> dict:
        caption = _between(blob, "CAPTION_BEGIN", "CAPTION_END")
        if not caption:
            caption = _field(blob, "CAPTION")
        keywords = _split_csv(_field(blob, "KEYWORDS"))
        if not keywords:
            keywords = _fallback_keywords(
                _field(blob, "RULE_TEXT") + " " + _field(blob, "JUDGE_QUESTION")
            )
        matched = ""
        for keyword in keywords:
            if _caption_has_keyword(keyword, caption):
                matched = keyword
                break
        cue = _danger_cue(caption)
        if not matched or not cue:
            if keywords and not matched:
                reason = "Caption does not mention the rule keywords."
            else:
                reason = "Caption has no proximity or danger cue."
            return _verdict(False, 1, 0.2, reason)
        severity = _severity(caption)
        confidence = 0.86 if severity >= 4 else 0.74
        reason = f"Caption matches '{matched}' with a danger cue ({cue})."
        return _verdict(True, severity, confidence, reason)

    def _report(self, blob: str) -> str:
        caption = _between(blob, "CAPTION_BEGIN", "CAPTION_END")
        camera = _field(blob, "CAMERA") or "unknown"
        location = _field(blob, "LOCATION") or "unknown"
        start = _field(blob, "START_SEC")
        end = _field(blob, "END_SEC")
        rule_name = _field(blob, "RULE_NAME") or "the rule"
        reason = _field(blob, "REASON") or caption or "The rule condition was observed."
        severity = _field(blob, "SEVERITY") or "n/a"
        if len(reason) > 240:
            reason = reason[:240].rstrip()
        return (
            f"## What happened\n{reason}\n\n"
            f"## Where & when\nCamera {camera} at {location}, {start}-{end} seconds.\n\n"
            f"## Risk\nSeverity {severity} for {rule_name}.\n\n"
            "## Recommended action\nPause nearby equipment and clear the zone.\n"
        )


def init_tracing(project_path, api_key) -> bool:
    """Enable weave tracing when the library and both credentials are present."""
    global _tracing, _weave
    if not project_path or not api_key:
        return False
    try:
        import weave
    except Exception:
        return False
    try:
        if not os.environ.get("WANDB_API_KEY"):
            os.environ["WANDB_API_KEY"] = api_key
        weave.init(project_path)
    except Exception:
        return False
    _weave = weave
    _tracing = True
    return True


def _cached_op(fn):
    if not _tracing or _weave is None:
        return None
    key = id(fn)
    with _op_lock:
        cached = _op_cache.get(key)
        if cached is not None:
            return cached
        try:
            cached = _weave.op(fn)
        except Exception:
            return None
        _op_cache[key] = cached
        return cached


def traced(fn):
    """Call ``fn`` through ``weave.op`` when tracing is active at call time."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        op = _cached_op(fn)
        if op is None:
            return fn(*args, **kwargs)
        return op(*args, **kwargs)

    return wrapper
