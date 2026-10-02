"""Persistent rules and incidents, plus optional webhook notification."""

import json
import os
import tempfile
import threading
import urllib.request
import uuid
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _footage_clock(start_sec) -> str:
    try:
        total = int(float(start_sec))
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def make_incident(rule, segment, verdict, report, mode) -> dict:
    """Build an Incident dict. ``notified`` stays null until a webhook attempt finishes."""
    return {
        "id": "i_" + uuid.uuid4().hex,
        "rule_id": rule.get("id"),
        "rule_name": rule.get("name"),
        "mode": mode,
        "source": segment.get("source"),
        "original_video": segment.get("original_video"),
        "segment_number": segment.get("segment_number"),
        "camera_id": segment.get("camera_id"),
        "location": segment.get("location"),
        "start_sec": segment.get("start_sec"),
        "end_sec": segment.get("end_sec"),
        "upload_timestamp": segment.get("upload_timestamp") or "",
        "caption": segment.get("caption") or "",
        "object_counts": dict(segment.get("object_counts") or {}),
        "severity": (verdict or {}).get("severity"),
        "confidence": (verdict or {}).get("confidence"),
        "reason": (verdict or {}).get("reason") or "",
        "report": report or "",
        "created_at": _utc_now(),
        "notified": None,
    }


def notify(webhook_url, incident, on_done=None):
    """POST a Discord- and Slack-compatible payload on a daemon thread.

    Empty URL returns immediately. Every error is swallowed. The URL is never logged.
    ``on_done(bool)`` runs after the attempt when a URL was provided.
    """
    if not webhook_url or not str(webhook_url).strip():
        return
    severity = incident.get("severity")
    text = (
        f"🚨 [sev {severity}] {incident.get('rule_name')} — "
        f"{incident.get('camera_id')} @ {_footage_clock(incident.get('start_sec'))} — "
        f"{incident.get('reason')}"
    )
    payload = json.dumps({"content": text, "text": text}).encode("utf-8")
    url = str(webhook_url)

    def _send():
        ok = False
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                # Discord sits behind Cloudflare, which rejects urllib's default User-Agent.
                headers={"Content-Type": "application/json", "User-Agent": "watchtower/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                ok = 200 <= int(getattr(resp, "status", 0)) < 300
                resp.read()
        except Exception:
            ok = False
        if on_done is not None:
            try:
                on_done(bool(ok))
            except Exception:
                pass

    threading.Thread(target=_send, name="alert-webhook", daemon=True).start()


class Store:
    """Thread-safe JSON store for rules and incidents.

    Writes are atomic (temp file in the same directory + os.replace).
    Deleting a rule does not delete its incidents.
    """

    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._rules_path = os.path.join(data_dir, "rules.json")
        self._incidents_path = os.path.join(data_dir, "incidents.json")
        self._rules = self._load(self._rules_path)
        self._incidents = self._load(self._incidents_path)
        if not os.path.exists(self._rules_path):
            self._atomic_write(self._rules_path, self._rules)
        if not os.path.exists(self._incidents_path):
            self._atomic_write(self._incidents_path, self._incidents)

    def _load(self, path):
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _atomic_write(self, path, data):
        directory = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def list_rules(self):
        with self._lock:
            return [dict(rule) for rule in self._rules]

    def get_rule(self, rule_id):
        with self._lock:
            for rule in self._rules:
                if rule.get("id") == rule_id:
                    return dict(rule)
        return None

    def save_rule(self, rule):
        stored = dict(rule)
        with self._lock:
            replaced = False
            for index, existing in enumerate(self._rules):
                if existing.get("id") == stored.get("id"):
                    self._rules[index] = stored
                    replaced = True
                    break
            if not replaced:
                self._rules.append(stored)
            self._atomic_write(self._rules_path, self._rules)
            return dict(stored)

    def update_rule(self, rule_id, **fields):
        with self._lock:
            for index, existing in enumerate(self._rules):
                if existing.get("id") == rule_id:
                    updated = dict(existing)
                    updated.update(fields)
                    updated["id"] = rule_id
                    self._rules[index] = updated
                    self._atomic_write(self._rules_path, self._rules)
                    return dict(updated)
        return None

    def delete_rule(self, rule_id):
        with self._lock:
            kept = [rule for rule in self._rules if rule.get("id") != rule_id]
            if len(kept) == len(self._rules):
                return False
            self._rules = kept
            self._atomic_write(self._rules_path, self._rules)
            return True

    def list_incidents(self):
        with self._lock:
            items = [dict(incident) for incident in self._incidents]
        items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return items

    def add_incident(self, incident):
        """Persist ``incident`` unless ``(rule_id, source, mode)`` already exists."""
        stored = dict(incident)
        key = (stored.get("rule_id"), stored.get("source"), stored.get("mode"))
        with self._lock:
            for existing in self._incidents:
                if (existing.get("rule_id"), existing.get("source"), existing.get("mode")) == key:
                    return None
            self._incidents.append(stored)
            self._atomic_write(self._incidents_path, self._incidents)
            return dict(stored)

    def clear_incidents(self):
        with self._lock:
            self._incidents = []
            self._atomic_write(self._incidents_path, self._incidents)

    def set_incident_notified(self, incident_id, ok):
        with self._lock:
            for index, incident in enumerate(self._incidents):
                if incident.get("id") == incident_id:
                    updated = dict(incident)
                    updated["notified"] = bool(ok)
                    self._incidents[index] = updated
                    self._atomic_write(self._incidents_path, self._incidents)
                    return dict(updated)
        return None
