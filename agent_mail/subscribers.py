"""Subscriber list IO. Format chosen: JSON.

Each entry:
  {"email": "a@b.com", "name": "Alice", "paused": false, "subscribed_at": "2026-06-30"}

paused=true → skipped without removal. Unknown fields preserved on rewrite.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load() -> list[dict]:
    p: Path = config.SUBSCRIBERS_PATH
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("subscribers.json must be a JSON array")
    out = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict) or "email" not in e:
            raise ValueError(f"subscribers.json[{i}] missing 'email'")
        if not _EMAIL_RE.match(e["email"]):
            raise ValueError(f"subscribers.json[{i}] invalid email: {e['email']!r}")
        out.append(e)
    return out


def active(subs: list[dict]) -> list[dict]:
    return [s for s in subs if not s.get("paused")]


def remove(email: str) -> bool:
    """Return True if removed (used by [UNSUBSCRIBE] handler)."""
    p: Path = config.SUBSCRIBERS_PATH
    if not p.exists():
        return False
    subs = load()
    target = email.strip().lower()
    new = [s for s in subs if s["email"].lower() != target]
    if len(new) == len(subs):
        return False
    p.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True
