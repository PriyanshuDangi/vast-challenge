"""VSS retrieval client and normalizers for Watchtower."""

from __future__ import annotations

import json
import math
import threading
import urllib.error
import urllib.parse
import urllib.request

_MAX_VIDEOS = 1000
_PAGE_SIZE = 100
_MISSING_TEXT = {"", "nan", "none", "null", "nat"}
_STREAM_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")


class VSSError(Exception):
    status: int | None

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def normalize_segment(row: dict) -> dict:
    """Map a VastDB / search row onto the Segment dict."""
    if not isinstance(row, dict):
        row = {}
    classes = _object_classes(row.get("object_classes"))
    counts = _object_counts(row.get("object_counts"))
    if not counts and classes:
        counts = {name: 1 for name in classes}
    return {
        "source": _text(row.get("source")),
        "original_video": _text(row.get("original_video")),
        "segment_number": _integer(_first(row, "segment_number")),
        "start_sec": _number(_first(row, "segment_start_sec", "start_sec")),
        "end_sec": _number(_first(row, "segment_end_sec", "end_sec")),
        "caption": _text(_first(row, "reasoning_content", "caption")),
        "object_counts": counts,
        "object_classes": classes,
        "camera_id": _text(row.get("camera_id")),
        "location": _text(row.get("location")),
        "capture_type": _text(row.get("capture_type")),
        "upload_timestamp": _iso(row.get("upload_timestamp")),
        "similarity": _similarity(row),
    }


def normalize_video(chunk: dict) -> dict:
    """Map an explore ChunkSearchResult onto the Video dict."""
    if not isinstance(chunk, dict):
        chunk = {}
    return {
        "original_video": _text(chunk.get("original_video")),
        "filename": _text(chunk.get("filename")),
        "camera_id": _text(chunk.get("camera_id")),
        "location": _text(chunk.get("location")),
        "capture_type": _text(chunk.get("capture_type")),
        "total_segments": _integer(chunk.get("total_segments")),
        "duration_sec": _number(_first(chunk, "chunk_duration_sec", "duration_sec")),
        "upload_timestamp": _iso(chunk.get("upload_timestamp")),
        "preview_source": _text(chunk.get("preview_source")),
    }


class VSSClient:
    def __init__(self, base_url, username, password, timeout=30):
        self.base_url = (base_url or "").rstrip("/")
        self.username = "" if username is None else str(username)
        self.password = "" if password is None else str(password)
        self.timeout = timeout
        self._token: str | None = None
        self._lock = threading.Lock()

    def list_videos(self) -> list[dict]:
        videos: list[dict] = []
        offset = 0
        while len(videos) < _MAX_VIDEOS:
            data = self._get_json(
                "/api/v1/videos/explore",
                {"scope": "all", "limit": _PAGE_SIZE, "offset": offset},
            )
            chunks = data.get("chunks") or []
            if not isinstance(chunks, list) or not chunks:
                break
            added = 0
            for chunk in chunks:
                if len(videos) >= _MAX_VIDEOS:
                    break
                if isinstance(chunk, dict):
                    videos.append(normalize_video(chunk))
                    added += 1
            if added == 0:
                break
            offset += len(chunks)
            total = _coerce_total(data.get("total"))
            if total is not None and offset >= total:
                break
        return videos

    def segments(self, original_video) -> list[dict]:
        data = self._get_json(
            "/api/v1/tools/segments",
            {"original_video": original_video},
        )
        rows = data.get("segments") or []
        if not isinstance(rows, list):
            rows = []
        normalized: list[dict] = []
        parent = "" if original_video is None else str(original_video)
        for row in rows:
            if not isinstance(row, dict):
                continue
            segment = normalize_segment(row)
            if not segment["original_video"]:
                segment["original_video"] = parent
            normalized.append(segment)
        normalized.sort(key=lambda segment: segment["segment_number"])
        return normalized

    def search(self, query, top_k=20, min_similarity=0.25, metadata_filters=None) -> list[dict]:
        filters = metadata_filters if isinstance(metadata_filters, dict) else {}
        data = self._post_json(
            "/api/v1/search",
            {
                "query": query,
                "top_k": top_k,
                "min_similarity": min_similarity,
                "metadata_filters": filters,
                "include_public": True,
                "llm_top_n": 1,
            },
        )
        rows = data.get("results") or []
        if not isinstance(rows, list):
            return []
        return [normalize_segment(row) for row in rows if isinstance(row, dict)]

    def detections(self, source) -> dict | None:
        try:
            payload = self._get_json("/api/v1/videos/detections", {"source": source})
        except VSSError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(payload, dict):
            raise VSSError("detections payload was not an object", status=None)
        return payload

    def open_stream(self, source, range_header=None) -> tuple[int, dict, object]:
        """GET the range-capable mp4 proxy. Does not read the body."""
        token = self._ensure_token()
        try:
            response = self._open_stream(source, token, range_header)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                return (exc.code, _header_subset(exc.headers), exc)
            _discard(exc)
            self._drop_token(token)
            token = self._ensure_token()
            try:
                response = self._open_stream(source, token, range_header)
            except urllib.error.HTTPError as retry_exc:
                return (retry_exc.code, _header_subset(retry_exc.headers), retry_exc)
            except urllib.error.URLError:
                raise VSSError("stream request failed", status=None) from None
        except urllib.error.URLError:
            raise VSSError("stream request failed", status=None) from None
        return (_response_status(response), _header_subset(response.headers), response)

    def ping(self) -> bool:
        try:
            self._ensure_token()
        except VSSError:
            return False
        return True

    def _open_stream(self, source, token: str, range_header):
        query = urllib.parse.urlencode({"source": source, "token": token})
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/videos/stream?{query}",
            method="GET",
        )
        if range_header:
            request.add_header("Range", range_header)
        return urllib.request.urlopen(request, timeout=self.timeout)

    def _get_json(self, path: str, query: dict | None = None) -> dict:
        def make(token: str) -> urllib.request.Request:
            url = f"{self.base_url}{path}"
            if query:
                url = f"{url}?{urllib.parse.urlencode(query)}"
            return urllib.request.Request(url, method="GET", headers=_auth_headers(token))

        return self._read_json(self._open(make), path)

    def _post_json(self, path: str, body: dict) -> dict:
        payload = json.dumps(body).encode("utf-8")

        def make(token: str) -> urllib.request.Request:
            headers = _auth_headers(token)
            headers["Content-Type"] = "application/json"
            return urllib.request.Request(
                f"{self.base_url}{path}",
                data=payload,
                method="POST",
                headers=headers,
            )

        return self._read_json(self._open(make), path)

    def _open(self, make_request, retry: bool = True):
        token = self._ensure_token()
        request = make_request(token)
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and retry:
                _discard(exc)
                self._drop_token(token)
                return self._open(make_request, retry=False)
            status = exc.code
            _discard(exc)
            raise VSSError(f"request failed ({status})", status=status) from None
        except urllib.error.URLError:
            raise VSSError("request failed", status=None) from None

    def _read_json(self, response, path: str) -> dict:
        try:
            raw = response.read()
        finally:
            response.close()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise VSSError(f"invalid JSON from {path}", status=_response_status(response)) from None
        if not isinstance(data, dict):
            raise VSSError(f"expected object from {path}", status=_response_status(response))
        return data

    def _ensure_token(self) -> str:
        with self._lock:
            if self._token:
                return self._token
            self._token = self._login()
            return self._token

    def _drop_token(self, token: str) -> None:
        with self._lock:
            if self._token == token:
                self._token = None

    def _login(self) -> str:
        payload = json.dumps(
            {"username": self.username, "password": self.password}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/auth/login",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            status = exc.code
            _discard(exc)
            raise VSSError(f"login failed ({status})", status=status) from None
        except urllib.error.URLError:
            raise VSSError("login failed", status=None) from None
        data = self._read_json(response, "/api/v1/auth/login")
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise VSSError("login failed", status=None)
        return token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _discard(exc: urllib.error.HTTPError) -> None:
    try:
        exc.read()
    except Exception:
        pass
    try:
        exc.close()
    except Exception:
        pass


def _header_subset(headers) -> dict:
    if headers is None:
        return {}
    selected = {}
    getter = getattr(headers, "get", None)
    if getter is None:
        return {}
    for name in _STREAM_HEADERS:
        value = getter(name)
        if value is None or value == "":
            continue
        selected[name] = str(value)
    return selected


def _response_status(response) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    if status is None:
        status = getattr(response, "code", 200)
    try:
        return int(status)
    except (TypeError, ValueError):
        return 0


def _first(row: dict, *keys: str):
    for key in keys:
        if key in row:
            return row.get(key)
    return None


def _missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip().lower() in _MISSING_TEXT:
        return True
    return False


def _text(value) -> str:
    if _missing(value):
        return ""
    return str(value).strip()


def _integer(value) -> int:
    if isinstance(value, bool) or _missing(value):
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if math.isnan(number) or math.isinf(number):
        return 0
    return int(number)


def _number(value) -> float:
    if isinstance(value, bool) or _missing(value):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def _optional_number(value) -> float | None:
    if isinstance(value, bool) or _missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _similarity(row: dict) -> float | None:
    if "similarity_score" in row:
        return _optional_number(row.get("similarity_score"))
    if "similarity" in row:
        return _optional_number(row.get("similarity"))
    return None


def _iso(value) -> str:
    if _missing(value):
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            rendered = isoformat()
        except Exception:
            rendered = str(value)
        if _missing(rendered):
            return ""
        return str(rendered).strip()
    return _text(value)


def _object_classes(value) -> list[str]:
    if _missing(value):
        return []
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = [value]
    classes: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if _missing(part):
            continue
        name = str(part).strip().lower()
        if not name or name in _MISSING_TEXT or name in seen:
            continue
        seen.add(name)
        classes.append(name)
    return classes


def _object_counts(value) -> dict[str, int]:
    if _missing(value):
        return {}
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    counts: dict[str, int] = {}
    for key, raw in data.items():
        if _missing(key):
            continue
        name = str(key).strip().lower()
        if not name or name in _MISSING_TEXT:
            continue
        number = _count_value(raw)
        if number is None:
            continue
        counts[name] = number
    return counts


def _count_value(value) -> int | None:
    if isinstance(value, bool) or _missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return int(number)


def _coerce_total(value) -> int | None:
    if _missing(value) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return int(number)
