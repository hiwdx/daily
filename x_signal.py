"""X trend-radar support for the daily briefing generator.

This module treats X as a lead source only. It searches recent posts around AI
agents, MCP, coding agents, developer tools, and official AI product accounts,
then returns compact leads that the main Claude prompt must independently verify
against official blogs, docs, GitHub, papers, or company announcements before any
item can enter the Daily.

Configuration is environment-only:
  - X_BEARER_TOKEN / X_API_BEARER_TOKEN / TWITTER_BEARER_TOKEN
  - X_SIGNAL_MAX_RESULTS (optional, default: 10 per query)
  - X_SIGNAL_LOOKBACK_HOURS (optional, default: 72)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

API_URL = "https://api.x.com/2/tweets/search/recent"
MAX_PROMPT_LEADS = 12
DEFAULT_MAX_RESULTS = 10
DEFAULT_LOOKBACK_HOURS = 72

OFFICIAL_ACCOUNTS = (
    "OpenAI",
    "AnthropicAI",
    "GoogleDeepMind",
    "xai",
    "MistralAI",
    "huggingface",
    "github",
    "GitHubCopilot",
    "vercel",
    "CloudflareDev",
    "Cloudflare",
    "cursor_ai",
    "Replit",
)

SEARCH_TOPICS = (
    '"MCP" (AI OR agent OR agents OR coding OR developer OR tool OR tools)',
    '"AI agent" OR "AI agents" OR "agent workflow"',
    'Codex OR "Claude Code" OR Cursor OR "GitHub Copilot" OR "Replit Agent"',
)

FILTERS = "-is:retweet lang:en"


@dataclass(frozen=True)
class XLead:
    created_at: str
    author: str
    url: str
    text: str

    @property
    def score(self) -> int:
        lower = self.text.lower()
        score = 0
        if self.author in OFFICIAL_ACCOUNTS:
            score += 5
        for term in (
            "launch", "release", "introducing", "announce", "available",
            "api", "model", "pricing", "docs", "github", "mcp", "agent",
            "codex", "claude code", "copilot", "cursor", "replit agent",
        ):
            if term in lower:
                score += 1
        for noisy in ("rumor", "leak", "hot take", "thread", "opinion"):
            if noisy in lower:
                score -= 2
        return score


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _bearer_token() -> str:
    for name in ("X_BEARER_TOKEN", "X_API_BEARER_TOKEN", "TWITTER_BEARER_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token
    return ""


def _start_time(now: datetime) -> str:
    hours = _env_int("X_SIGNAL_LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS, 1, 72)
    return (now - timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _queries() -> list[str]:
    account_query = " OR ".join(f"from:{account}" for account in OFFICIAL_ACCOUNTS)
    return [f"({topic}) {FILTERS}" for topic in SEARCH_TOPICS] + [f"({account_query}) {FILTERS}"]


def _request_recent_search(token: str, query: str, start_time: str, max_results: int) -> dict:
    params = {
        "query": query,
        "max_results": str(max_results),
        "start_time": start_time,
        "tweet.fields": "created_at,author_id,entities,public_metrics",
        "expansions": "author_id",
        "user.fields": "username,verified,verified_type",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _leads_from_payload(payload: dict) -> Iterable[XLead]:
    users = {
        user.get("id"): user.get("username", "unknown")
        for user in payload.get("includes", {}).get("users", [])
    }
    for tweet in payload.get("data", []) or []:
        author = users.get(tweet.get("author_id"), "unknown")
        tweet_id = tweet.get("id")
        if not tweet_id:
            continue
        text = " ".join((tweet.get("text") or "").split())
        yield XLead(
            created_at=tweet.get("created_at", ""),
            author=author,
            url=f"https://x.com/{author}/status/{tweet_id}",
            text=text[:280],
        )


def fetch_x_leads(now: datetime | None = None) -> list[XLead]:
    """Fetch and pre-rank X leads. Returns [] on any missing-token/API failure."""
    token = _bearer_token()
    if not token:
        print("ℹ️ X signal disabled: no X_BEARER_TOKEN configured")
        return []

    now = now or datetime.now(timezone.utc)
    max_results = _env_int("X_SIGNAL_MAX_RESULTS", DEFAULT_MAX_RESULTS, 10, 100)
    start = _start_time(now)
    seen: set[str] = set()
    leads: list[XLead] = []

    for query in _queries():
        try:
            payload = _request_recent_search(token, query, start, max_results)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"⚠️ X signal query failed and will be ignored: {exc}", file=sys.stderr)
            continue
        for lead in _leads_from_payload(payload):
            if lead.url in seen:
                continue
            seen.add(lead.url)
            if lead.score > 0:
                leads.append(lead)

    leads.sort(key=lambda lead: (lead.score, lead.created_at), reverse=True)
    return leads[:MAX_PROMPT_LEADS]


def build_x_signal_prompt_block(leads: list[XLead]) -> str:
    """Render X leads as strict instructions for source verification."""
    if not leads:
        return ""
    lines = [
        "## X 趋势雷达（只作为线索，禁止直接搬运）",
        "",
        "以下线索来自最近 72 小时内的 X 搜索。X 只用于发现趋势，不是可直接引用的信息源。",
        "进入 Daily 前必须二次核验：优先寻找官方博客、GitHub、产品文档、论文或公司公告；找不到可靠来源则不要发布。",
        "每天最多从这些线索中选择 1-3 条；不要引用推文原文，不要把 X 链接作为正文主来源。",
        "",
    ]
    for i, lead in enumerate(leads, 1):
        lines.append(f"{i}. @{lead.author} · {lead.created_at} · {lead.url}")
        lines.append(f"   线索：{lead.text}")
    return "\n".join(lines)
