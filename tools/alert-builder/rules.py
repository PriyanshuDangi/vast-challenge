"""Compile plain-English alerts into structured rules and cheaply prefilter segments."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone

COCO_CLASSES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]

_COCO = set(COCO_CLASSES)

# Objects YOLO cannot see. Matched later as caption keywords.
_NON_COCO = (
    "hard hat",
    "forklift",
    "pallet",
    "vest",
    "helmet",
    "cone",
    "ladder",
    "spill",
    "box",
    "crate",
    "gate",
    "door",
    "lane",
    "crosswalk",
)

_CLASS_SYNONYMS = {
    "people": "person",
    "persons": "person",
    "pedestrian": "person",
    "pedestrians": "person",
    "worker": "person",
    "workers": "person",
    "man": "person",
    "men": "person",
    "woman": "person",
    "women": "person",
    "vehicle": "car",
    "vehicles": "car",
    "lorry": "truck",
    "lorries": "truck",
    "bike": "bicycle",
    "bikes": "bicycle",
    "motorbike": "motorcycle",
    "motorbikes": "motorcycle",
    "plane": "airplane",
    "planes": "airplane",
    "aeroplane": "airplane",
    "aeroplanes": "airplane",
}

_ALERT_PREFIX = re.compile(
    r"^(?:please\s+)?(?:alert|notify|warn|tell)\s+me\s+(?:if|when|whenever)\s+",
    re.IGNORECASE,
)

_DEFAULT_GUIDE = (
    "5 = contact or imminent collision; 4 = very close or sudden maneuver; "
    "3 = near with clear risk; 2 = proximity without immediate danger; 1 = distant or no risk"
)

COMPILE_MARKER = "TASK: COMPILE_RULE"


def new_rule_id() -> str:
    return "r_" + secrets.token_hex(3)


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _as_str(value, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return []


def _as_count(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            number = 1
    return 1 if number < 1 else number


def _as_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    parts = phrase.lower().split()
    bits: list[str] = []
    for index, part in enumerate(parts):
        token = re.escape(part)
        if index == len(parts) - 1:
            if part.endswith(("s", "x", "z", "ch", "sh")):
                token += r"(?:es|s)?"
            else:
                token += r"s?"
        bits.append(token)
    body = r"\s+".join(bits)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def _phrase_in(text: str, phrase: str) -> bool:
    return _phrase_pattern(phrase).search(text) is not None


def _blank_phrase(text: str, phrase: str) -> str:
    return _phrase_pattern(phrase).sub(lambda match: " " * len(match.group(0)), text)


def _naive_plural(phrase: str) -> str:
    parts = phrase.split()
    last = parts[-1]
    if last.endswith("s"):
        plural_last = last
    elif last.endswith(("x", "z", "ch", "sh")):
        plural_last = last + "es"
    else:
        plural_last = last + "s"
    parts[-1] = plural_last
    return " ".join(parts)


def _with_plurals(phrase: str) -> list[str]:
    base = re.sub(r"\s+", " ", phrase.strip().lower())
    plural = _naive_plural(base)
    if plural == base:
        return [base]
    return [base, plural]


def _clean_request(text: str) -> str:
    cleaned = _ALERT_PREFIX.sub("", text.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned


def _detect_terms(text: str) -> tuple[dict[str, int], list[str]]:
    work = text.lower()
    phrases: list[tuple[str, str, str]] = []
    for phrase in _NON_COCO:
        phrases.append((phrase, "caption", phrase))
    for phrase, canonical in _CLASS_SYNONYMS.items():
        phrases.append((phrase, "class", canonical))
    for name in COCO_CLASSES:
        phrases.append((name, "class", name))
    phrases.sort(key=lambda item: len(item[0]), reverse=True)

    classes: dict[str, int] = {}
    captions: list[str] = []
    for phrase, kind, canonical in phrases:
        if not _phrase_in(work, phrase):
            continue
        work = _blank_phrase(work, phrase)
        if kind == "class":
            classes.setdefault(canonical, 1)
        else:
            captions.extend(_with_plurals(canonical))
    return classes, _dedupe(captions)


def _scoped(text: str, options) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for option in options or []:
        name = str(option).strip()
        if not name:
            continue
        if _phrase_in(lowered, name.lower()):
            found.append(name.lower())
    return _dedupe(found)


def _name_from(cleaned: str) -> str:
    body = re.sub(r"^(?:a|an|the)\s+", "", cleaned, flags=re.IGNORECASE).strip(" .?")
    words = body.split()
    if not words:
        return "Safety alert"
    title = " ".join(words[:6])
    return title[:1].upper() + title[1:]


def _judge_question(cleaned: str, original: str) -> str:
    subject = (cleaned or original).strip().rstrip(".?")
    if not subject:
        subject = "the described event"
    return f"In this 5-second segment, does this happen: {subject}?"


def heuristic_compile(text, vocab) -> dict:
    """Keyword compile used when the LLM is unavailable."""
    raw = "" if text is None else str(text).strip()
    vocab = vocab or {}
    cleaned = _clean_request(raw)
    classes, captions = _detect_terms(raw)
    rule = {
        "name": _name_from(cleaned),
        "text": raw,
        "cameras": _scoped(raw, vocab.get("cameras")),
        "locations": _scoped(raw, vocab.get("locations")),
        "require_classes": classes,
        "caption_any": captions,
        "search_query": cleaned or raw or "safety event",
        "judge_question": _judge_question(cleaned, raw),
        "severity_guide": _DEFAULT_GUIDE,
        "enabled": True,
    }
    return validate_rule(rule, vocab)


def validate_rule(rule: dict, vocab: dict) -> dict:
    """Fill defaults, keep COCO counts, and move other class names to keywords."""
    if not isinstance(rule, dict):
        rule = {}
    vocab = vocab or {}

    camera_vocab = [str(item).strip() for item in (vocab.get("cameras") or []) if str(item).strip()]
    location_vocab = [str(item).strip() for item in (vocab.get("locations") or []) if str(item).strip()]
    camera_ok = {item.lower() for item in camera_vocab}
    location_ok = {item.lower() for item in location_vocab}

    def _filter_scope(values, allowed: set[str], vocab_given: bool) -> list[str]:
        kept: list[str] = []
        for item in _as_str_list(values):
            lowered = item.lower()
            if vocab_given and lowered not in allowed:
                continue
            kept.append(lowered)
        return _dedupe(kept)

    raw_counts = rule.get("require_classes")
    if isinstance(raw_counts, list):
        raw_counts = {str(item): 1 for item in raw_counts}
    elif not isinstance(raw_counts, dict):
        raw_counts = {}

    require: dict[str, int] = {}
    moved: list[str] = []
    for key, value in raw_counts.items():
        name = str(key).strip().lower()
        if not name:
            continue
        if name in _COCO:
            require[name] = _as_count(value)
        else:
            moved.append(name)

    normalized_captions: list[str] = []
    for item in _as_str_list(rule.get("caption_any")) + moved:
        normalized_captions.append(re.sub(r"\s+", " ", item.strip().lower()))

    text = _as_str(rule.get("text")).strip()
    name = _as_str(rule.get("name")).strip()
    if not name:
        name = "Safety alert"
    name = " ".join(name.split()[:6])

    search_query = _as_str(rule.get("search_query")).strip()
    if not search_query:
        search_query = text or name

    judge_question = _as_str(rule.get("judge_question")).strip()
    if not judge_question:
        judge_question = _judge_question(text, name)

    severity_guide = _as_str(rule.get("severity_guide")).strip()
    if len(severity_guide) < 20:
        severity_guide = _DEFAULT_GUIDE

    if "enabled" not in rule or rule.get("enabled") is None:
        enabled = True
    else:
        enabled = _as_bool(rule.get("enabled"), True)

    out = {
        "name": name,
        "text": text,
        "cameras": _filter_scope(rule.get("cameras"), camera_ok, bool(camera_vocab)),
        "locations": _filter_scope(rule.get("locations"), location_ok, bool(location_vocab)),
        "require_classes": require,
        "caption_any": _dedupe([c for c in normalized_captions if c]),
        "search_query": search_query,
        "judge_question": judge_question,
        "severity_guide": severity_guide,
        "enabled": enabled,
    }
    out["id"] = _as_str(rule.get("id")) if rule.get("id") else ""
    out["created_at"] = _as_str(rule.get("created_at")) if rule.get("created_at") else ""
    return out


def compile_rule(llm, text, vocab: dict) -> dict:
    """Ask the LLM to compile ``text``; fall back to the heuristic on any failure."""
    raw = "" if text is None else str(text)
    vocab = vocab or {}
    cameras = list(vocab.get("cameras") or [])
    locations = list(vocab.get("locations") or [])
    prompt = (
        f"{COMPILE_MARKER}\n"
        "You compile one plain-English safety alert into a JSON rule for a video archive.\n"
        "YOLO detections only know these COCO classes (lowercase): "
        + ", ".join(COCO_CLASSES)
        + ".\n"
        "There is no YOLO class for non-COCO objects such as forklift, vest, hard hat, "
        "pallet, helmet, cone, ladder, spill, or similar site objects. Put those, plus "
        "synonyms and plurals, into caption_any as lowercase keywords.\n"
        "caption_any is an any-of gate on segment captions: a segment is skipped unless its "
        "caption contains one keyword. Use only short object nouns (1-2 words) that a scene "
        "description would literally contain, never action phrases like 'steps into road'. "
        "Leave caption_any empty when require_classes already captures the objects involved.\n"
        "require_classes may list only COCO classes, each mapped to a minimum integer count (>= 1).\n"
        "require_classes is an all-of gate: every listed class must be detected in the same segment. "
        "For a generic word like 'vehicle', list just 'car', never every vehicle class.\n"
        "cameras and locations must be copied from the vocab lists below, and only when the "
        "user clearly limits the rule to them. Otherwise use empty arrays, which means all "
        "cameras and all locations.\n"
        "search_query is a descriptive natural-language query for semantic video search.\n"
        "judge_question is a yes/no question about one 5-second video segment.\n"
        "severity_guide is a brief rubric from 1 (no risk) to 5 (contact or imminent collision).\n"
        "name is a short title of at most 6 words.\n"
        "USER_TEXT_BEGIN\n"
        + raw
        + "\nUSER_TEXT_END\n"
        "VOCAB_CAMERAS_JSON: "
        + json.dumps(cameras)
        + "\n"
        "VOCAB_LOCATIONS_JSON: "
        + json.dumps(locations)
        + "\n"
        "Return only a JSON object with keys: name, cameras, locations, require_classes, "
        "caption_any, search_query, judge_question, severity_guide.\n"
    )
    messages = [
        {
            "role": "system",
            "content": "You compile safety-alert rules into JSON. Return only a JSON object.",
        },
        {"role": "user", "content": prompt},
    ]
    try:
        data = llm.chat_json(messages, temperature=0.0, max_tokens=800)
        if not isinstance(data, dict):
            raise TypeError("compiled rule was not a JSON object")
        for nest in ("rule", "result", "data"):
            nested = data.get(nest)
            if isinstance(nested, dict) and "name" not in data and "require_classes" not in data:
                data = nested
                break
        data["text"] = raw
        rule = validate_rule(data, vocab)
    except Exception:
        rule = heuristic_compile(raw, vocab)
    rule["id"] = new_rule_id()
    rule["text"] = raw
    rule["enabled"] = True
    rule["created_at"] = datetime.now(timezone.utc).isoformat()
    return rule


def _count_of(segment: dict, name: str) -> int:
    counts = segment.get("object_counts")
    if not isinstance(counts, dict) or not counts:
        classes = segment.get("object_classes") or []
        if isinstance(classes, str):
            classes = [part.strip() for part in classes.split(",") if part.strip()]
        counts = {str(item): 1 for item in classes} if classes else {}
    raw = 0
    for key, value in counts.items():
        if str(key).lower() == name.lower():
            raw = value
            break
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return 0


def _scope_miss(wanted, actual: str) -> bool:
    if not wanted:
        return False
    allowed = {str(item).strip().lower() for item in wanted if str(item).strip()}
    if not allowed:
        return False
    return actual.strip().lower() not in allowed


def prefilter(rule, segment) -> tuple[bool, str]:
    """Cheap gate: camera/location scope, then YOLO counts, then caption keywords."""
    if not isinstance(rule, dict):
        return False, "invalid rule"
    if not isinstance(segment, dict):
        return False, "invalid segment"

    if _scope_miss(rule.get("cameras"), _as_str(segment.get("camera_id"))):
        return False, "camera out of scope"
    if _scope_miss(rule.get("locations"), _as_str(segment.get("location"))):
        return False, "location out of scope"

    require = rule.get("require_classes") or {}
    if not isinstance(require, dict):
        require = {}
    passed_counts: list[str] = []
    for name, minimum in require.items():
        need = _as_count(minimum)
        have = _count_of(segment, str(name))
        if have < need:
            return False, f"{str(name).lower()} {have} < {need}"
        passed_counts.append(f"{str(name).lower()}>={need}")

    keywords = [str(item).strip().lower() for item in (rule.get("caption_any") or []) if str(item).strip()]
    caption = _as_str(segment.get("caption"))
    if keywords:
        matched = ""
        for keyword in keywords:
            if _phrase_in(caption.lower(), keyword):
                matched = keyword
                break
        if not matched:
            return False, "no " + "/".join(keywords) + " in caption"
        bits = list(passed_counts)
        bits.append(f"caption mentions {matched}")
        return True, ", ".join(bits)

    if passed_counts:
        return True, ", ".join(passed_counts)
    if rule.get("cameras") or rule.get("locations"):
        return True, "in scope"
    return True, "no filter"
