"""Smoke-check the live VSS archive and W&B inference. No work at import time."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import config
import vss_client


def main() -> int:
    try:
        settings = config.load_settings()
    except Exception as exc:
        print(f"settings failed: {type(exc).__name__}")
        return 1

    secrets = [settings.vss_password, settings.wandb_api_key]
    client = vss_client.VSSClient(
        settings.vss_url,
        settings.vss_username,
        settings.vss_password,
    )
    failed = False
    videos: list[dict] = []
    model_ids: list[str] = []

    def step(name: str, action) -> None:
        nonlocal failed
        try:
            action()
        except Exception as exc:
            failed = True
            print(f"{name} failed: {_redact(exc, secrets)}")

    def login() -> None:
        if client.ping():
            print("login ok")
        else:
            raise vss_client.VSSError("login failed", status=None)

    def list_archive() -> None:
        nonlocal videos
        videos = client.list_videos()
        counts: dict[str, int] = {}
        for video in videos:
            camera = video.get("camera_id") or ""
            counts[camera] = counts.get(camera, 0) + 1
        print(f"videos: {len(videos)}")
        for camera in sorted(counts):
            print(f"  {camera}: {counts[camera]}")

    def warehouse() -> None:
        chosen = next(
            (
                video
                for video in videos
                if "warehouse" in str(video.get("camera_id") or "").lower()
            ),
            None,
        )
        if chosen is None:
            print("warehouse video: none")
            return
        segments = client.segments(chosen.get("original_video"))
        camera = chosen.get("camera_id") or ""
        print(f"warehouse camera {camera} segments: {len(segments)}")
        for segment in segments[:3]:
            caption = str(segment.get("caption") or "")[:200]
            counts = segment.get("object_counts") or {}
            print(f"  [{segment.get('segment_number')}] {caption}")
            print(f"      object_counts={json.dumps(counts, sort_keys=True)}")
        classes: set[str] = set()
        for segment in segments:
            for name in segment.get("object_classes") or []:
                classes.add(str(name))
            for name in (segment.get("object_counts") or {}):
                classes.add(str(name))
        print("yolo classes: " + ", ".join(sorted(classes)))

    def models() -> None:
        nonlocal model_ids
        payload = _wandb_json(settings, "/models")
        rows = []
        if isinstance(payload, dict):
            rows = payload.get("data") or payload.get("models") or []
        ids: list[str] = []
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("id"):
                    ids.append(str(row["id"]))
                elif isinstance(row, str):
                    ids.append(row)
        model_ids = ids
        print(f"llm models ({len(ids)}):")
        for model_id in ids[:15]:
            print(f"  {model_id}")

    def chat() -> None:
        model = settings.llm_model or (model_ids[0] if model_ids else "")
        if not model:
            print("llm chat skipped: no model")
            return
        payload = _wandb_json(
            settings,
            "/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with the single word ok."}],
                "temperature": 0,
                "max_tokens": 16,
            },
        )
        content = ""
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                content = message.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        line = content.strip().splitlines()[0][:200] if content.strip() else ""
        print(f"llm chat: {line}")

    step("login", login)
    step("videos", list_archive)
    step("warehouse", warehouse)
    step("llm models", models)
    step("llm chat", chat)
    return 1 if failed else 0


def _wandb_json(settings: config.Settings, path: str, body: dict | None = None) -> dict:
    base = (settings.llm_base_url or "").rstrip("/")
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method="GET" if body is None else "POST",
    )
    request.add_header("Authorization", f"Bearer {settings.wandb_api_key}")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "watchtower/1.0")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    # urllib capitalizes header names; put the project header on as specified.
    request.headers["OpenAI-Project"] = settings.wandb_project_path
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            exc.close()
        except Exception:
            pass
        raise vss_client.VSSError(f"llm request failed ({status})", status=status) from None
    except urllib.error.URLError:
        raise vss_client.VSSError("llm request failed", status=None) from None
    try:
        raw = response.read()
    finally:
        response.close()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise vss_client.VSSError("llm response was not an object", status=None)
    return payload


def _redact(exc: BaseException, secrets: list[str]) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return text[:300]


if __name__ == "__main__":
    raise SystemExit(main())
