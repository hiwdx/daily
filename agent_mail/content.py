"""Pull today's daily-briefing content.

Strategy (most-stable-first):
  1. RSS  : docs/rss.xml — structured, kept current by generate.py
  2. HTML : docs/archive/YYYY-MM/YYYY-MM-DD.html — the rendered page
  3. Skip : neither found → return None; digest.py will abort and (in CI) the
            workflow alerts via Issue rather than send a stale/empty email.

Output is always {"date": "YYYY-MM-DD", "title": str, "body_html": str, "url": str}.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date as date_cls
from pathlib import Path

from . import config


def _read_rss(target_date: str) -> dict | None:
    p: Path = config.RSS_PATH
    if not p.exists():
        return None
    try:
        root = ET.fromstring(p.read_text(encoding="utf-8"))
    except ET.ParseError:
        return None
    for item in root.iterfind("./channel/item"):
        title = (item.findtext("title") or "").strip()
        if target_date not in title:
            continue
        return {
            "date": target_date,
            "title": title,
            "body_html": (item.findtext("description") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
        }
    return None


def _read_archive_html(target_date: str) -> dict | None:
    yyyy_mm = target_date[:7]
    p = config.ARCHIVE_DIR / yyyy_mm / f"{target_date}.html"
    if not p.exists():
        return None
    html = p.read_text(encoding="utf-8")
    m = re.search(r"<main[^>]*>(.*?)</main>", html, re.DOTALL | re.IGNORECASE)
    body = m.group(1) if m else html
    title_m = re.search(r"<title>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    title = (title_m.group(1).strip() if title_m else f"AI 行业每日简报 · {target_date}")
    return {
        "date": target_date,
        "title": title,
        "body_html": body,
        "url": f"{config.SITE_URL}/archive/{yyyy_mm}/{target_date}.html",
    }


def fetch_for(target_date: str | date_cls) -> dict | None:
    if isinstance(target_date, date_cls):
        target_date = target_date.isoformat()
    return _read_rss(target_date) or _read_archive_html(target_date)
