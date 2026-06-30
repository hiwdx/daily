"""state.json — single source of truth for what's been done.

Schema (kept stable; add fields, never rename):
{
  "last_digest_date": "2026-06-30",          # CST date string of last sent digest
  "last_digest_recipients": ["a@b.com", ...],
  "processed_message_ids": ["<...>", ...],   # de-dup [AGENT]/[SEND_EMAIL]/[DAILY] tasks
  "unread_first_seen": {                     # IMAP unseen UID -> first-observed ISO ts
    "12345": "2026-06-27T01:00:00+00:00"
  },
  "last_idle_forward_at": "2026-06-30T01:00:00+00:00",
  "forwarded_unread_ids": ["12345", ...],    # already forwarded, won't re-forward
  "version": 1
}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

_DEFAULT: dict[str, Any] = {
    "last_digest_date": None,
    "last_digest_recipients": [],
    "processed_message_ids": [],
    "unread_first_seen": {},
    "last_idle_forward_at": None,
    "forwarded_unread_ids": [],
    "version": 1,
}


def load() -> dict[str, Any]:
    p: Path = config.STATE_PATH
    if not p.exists():
        return dict(_DEFAULT)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # corrupted state should fail loudly, not silently start over —
        # restoring from git history is preferable to losing dedup info
        raise
    for k, v in _DEFAULT.items():
        data.setdefault(k, v)
    return data


def save(state: dict[str, Any]) -> None:
    p: Path = config.STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
