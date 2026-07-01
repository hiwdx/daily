"""Compose the email body.

Kept deliberately simple — one shared template, per-recipient personalisation
only via the unsubscribe link / greeting.
"""
from __future__ import annotations

from email.utils import formataddr
from html import escape

from . import config


def render_subject(content: dict) -> str:
    return content["title"] or f"hiwd daily · {content['date']}"


def render_html(content: dict, subscriber: dict) -> str:
    name = escape(subscriber.get("name") or subscriber["email"].split("@")[0])
    body = content.get("body_html") or ""
    url = escape(content.get("url") or config.SITE_URL)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', Helvetica, Arial, sans-serif; max-width: 720px; margin: 0 auto; padding: 24px; color: #1d1d1f; line-height: 1.7;">
  <p>嗨 {name}，今日 AI 简报送达：</p>
  <hr style="border:none;border-top:1px solid #e5e5e7;margin:16px 0;" />
  {body}
  <hr style="border:none;border-top:1px solid #e5e5e7;margin:24px 0;" />
  <p style="font-size:13px;color:#86868b;">
    在线阅读：<a href="{url}">{url}</a><br />
    {escape(config.UNSUBSCRIBE_HINT)}
  </p>
</body></html>
"""


def render_text(content: dict, subscriber: dict) -> str:
    # crude html→text fallback for clients that prefer plaintext
    import re
    name = subscriber.get("name") or subscriber["email"].split("@")[0]
    body = re.sub(r"<[^>]+>", "", content.get("body_html") or "")
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return (
        f"嗨 {name}，今日 AI 简报送达：\n\n"
        f"{body}\n\n"
        f"在线阅读：{content.get('url') or config.SITE_URL}\n"
        f"{config.UNSUBSCRIBE_HINT}\n"
    )


def from_header(c: config.RuntimeConf) -> str:
    return formataddr((c.agently.from_name, c.agently.from_addr))
