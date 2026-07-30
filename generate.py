#!/usr/bin/env python3
"""
Daily AI News Briefing Generator
每日 AI 简报生成器

Usage:
    DEEPSEEK_API_KEY=... python generate.py
"""

import difflib
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI, RateLimitError

try:
    import markdown as md_lib
except ImportError:
    print("❌ Missing 'markdown' package. Run: pip install markdown", file=sys.stderr)
    sys.exit(1)

# ── Date (China Standard Time UTC+8) ──────────────────────────────────────────
CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST)
FRESHNESS_HOURS = 48
MAX_CANDIDATES = 36
MAX_CANDIDATE_SUMMARY_CHARS = 420
# Conservative character cap: even for CJK-heavy input it keeps the complete
# request below roughly 15k input tokens once the fixed prompt is included.
MAX_MODEL_INPUT_CHARS = 12000
MAX_MODEL_OUTPUT_TOKENS = 1800
MAX_MODEL_CALLS = 2
DEEPSEEK_MODEL = "deepseek-v4-pro"
WINDOW_START = NOW - timedelta(hours=FRESHNESS_HOURS)
# Search engines often interpret `after:` as exclusive. Include a one-day
# discovery buffer, then let the publish validator enforce the real 48 hours.
SEARCH_AFTER_ISO = (WINDOW_START - timedelta(days=1)).strftime("%Y-%m-%d")
SEARCH_BEFORE_ISO = (NOW + timedelta(days=1)).strftime("%Y-%m-%d")
TODAY_ISO = NOW.strftime("%Y-%m-%d")
TODAY_CN = NOW.strftime("%Y年%m月%d日")
WEEKDAYS = "一二三四五六日"
WEEKDAY_CN = f"周{WEEKDAYS[NOW.weekday()]}"

def format_display_date(date_iso: str) -> tuple[str, str]:
    dt = datetime.strptime(date_iso, "%Y-%m-%d")
    return dt.strftime("%Y年%m月%d日"), f"周{WEEKDAYS[dt.weekday()]}"


# ── Deduplication helpers ─────────────────────────────────────────────────────
# Domains whose links should NOT be treated as "already-covered" article URLs.
_SKIP_DOMAINS = {"hiwd.com", "daily.hiwd.com"}


def canonicalize_url(url: str) -> str:
    """Return a stable article URL for exact duplicate checks."""
    url = html.unescape(url.strip())
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, "", ""))


def get_previous_stories(archive_dir: Path) -> list[dict[str, str]]:
    """Return every previously published story title and canonical URL.

    Using the complete archive prevents an old story from returning after the
    former two-day deduplication window. Titles are included because syndicated
    coverage of the same event often has a different URL.
    """
    stories: dict[str, dict[str, str]] = {}
    html_files = sorted(archive_dir.glob("????-??/????-??-??.html"), reverse=True)
    for html_file in html_files:
        text = html_file.read_text("utf-8")
        body_match = _BRIEFING_BODY_RE.search(text)
        body = body_match.group(1) if body_match else text
        for match in re.finditer(
            r"<strong>标题</strong>\s*[：:]\s*<a\s+href=\"(https?://[^\"]+)\"[^>]*>(.*?)</a>",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            url = canonicalize_url(match.group(1))
            if not url or any(d in urlsplit(url).netloc for d in _SKIP_DOMAINS):
                continue
            title = html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
            stories.setdefault(url, {
                "date": html_file.stem,
                "title": title,
                "url": url,
            })
        # Capture compact-list items and older numbered headings too. setdefault
        # keeps the descriptive title anchor when its source repeats the URL.
        for match in re.finditer(
            r"<a\s+href=\"(https?://[^\"]+)\"[^>]*>(.*?)</a>",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            url = canonicalize_url(match.group(1))
            if not url or any(d in urlsplit(url).netloc for d in _SKIP_DOMAINS):
                continue
            title = html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
            if title:
                stories.setdefault(url, {
                    "date": html_file.stem,
                    "title": title,
                    "url": url,
                })
    return list(stories.values())


# ── Prompts ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "你是一位专注于 AI 产业的资深科技分析师，"
    "善于从海量信息中提炼关键信号，输出精准、有深度的每日简报。"
)

SENSITIVE_POLITICS_PATTERNS = [
    r"中美",
    r"地缘政治|制裁",
    r"涉台|台湾|香港|新疆",
    r"中国.{0,10}(?:敏感|监管|政治|审查|治理)",
    r"(?:敏感|禁止|排除).{0,10}(?:内容|主题|条目|规则)",
    r"人工智能拟人化互动服务管理暂行办法",
    r"(?:中国|国内).{0,24}(?:融资|估值|IPO|监管|政策|市场|冠军)",
    r"(?:融资|估值|IPO).{0,24}(?:中国|国内)",
    r"(?:美国.{0,30}中国|中国.{0,30}美国)",
    r"DeepSeek.{0,24}(?:IPO|估值|监管)",
]


def contains_sensitive_politics(text: str) -> bool:
    """Return True when the briefing touches disallowed CN/US political topics."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in SENSITIVE_POLITICS_PATTERNS)


OFFICIAL_UPDATE_FEEDS = (
    ("GitHub Changelog", "https://github.blog/changelog/feed/"),
    ("Cloudflare Changelog", "https://developers.cloudflare.com/changelog/rss/index.xml"),
    # /changelog/rss.xml returns HTTP 308; use the canonical Atom endpoint so
    # older Python urllib versions do not drop the feed at the redirect.
    ("Vercel Changelog", "https://vercel.com/atom"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
)
_AI_UPDATE_RE = re.compile(
    r"\b(?:AI|Copilot|agent|agents|MCP|model|models|LLM|inference|embedding|RAG)\b|Chat SDK",
    flags=re.IGNORECASE,
)
_LOW_VALUE_UPDATE_RE = re.compile(
    r"\b(?:in talks|reportedly|rumou?rs?|accused|lawsuit|watchdog)\b|"
    r"\b(?:celebrity|singer|actor|actress)\b|"
    r"^[^|]{0,40}\bsays\b",
    flags=re.IGNORECASE,
)
_DATED_ARTICLE_PATH_RE = re.compile(r"/(20\d{2})[-/](\d{2})[-/](\d{2})(?:-|/)")


def _parse_feed_date(raw_date: str) -> Optional[datetime]:
    """Parse RFC 2822 (RSS) and ISO 8601 (Atom) publication dates."""
    try:
        published_at = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError):
        try:
            published_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except ValueError:
            return None
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at


def _plain_text(value: str) -> str:
    """Turn feed HTML into compact prompt-safe plain text."""
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _feed_entries(root: ElementTree.Element) -> list[tuple[str, str, str, str]]:
    """Return title, article URL, date, and compact summary from RSS or Atom."""
    entries: list[tuple[str, str, str, str]] = []
    for item in root.findall(".//item"):
        entries.append((
            html.unescape((item.findtext("title") or "").strip()),
            (item.findtext("link") or "").strip(),
            (item.findtext("pubDate") or item.findtext("date") or "").strip(),
            _plain_text(item.findtext("description") or item.findtext("encoded") or ""),
        ))

    atom_namespace = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(f".//{atom_namespace}entry"):
        title_element = entry.find(f"{atom_namespace}title")
        title = "".join(title_element.itertext()).strip() if title_element is not None else ""
        links = entry.findall(f"{atom_namespace}link")
        article_link = next(
            (
                link.get("href", "")
                for link in links
                if link.get("rel", "alternate") == "alternate" and link.get("href")
            ),
            next((link.get("href", "") for link in links if link.get("href")), ""),
        )
        raw_date = (
            entry.findtext(f"{atom_namespace}published")
            or entry.findtext(f"{atom_namespace}updated")
            or ""
        ).strip()
        summary = entry.findtext(f"{atom_namespace}summary") or entry.findtext(f"{atom_namespace}content") or ""
        entries.append((html.unescape(title), article_link.strip(), raw_date, _plain_text(summary)))
    return entries


def parse_official_feed(
    xml_data: bytes,
    source: str,
    now: Optional[datetime] = None,
) -> list[dict[str, str]]:
    """Extract fresh AI-related entries from an RSS or Atom feed."""
    now = now or NOW
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError:
        return []

    candidates: list[dict[str, str]] = []
    for title, raw_link, raw_pub_date, summary in _feed_entries(root):
        link = canonicalize_url(raw_link)
        if not title or not link or not raw_pub_date or not _AI_UPDATE_RE.search(title):
            continue
        if _LOW_VALUE_UPDATE_RE.search(title):
            continue
        if contains_sensitive_politics(title):
            continue
        published_at = _parse_feed_date(raw_pub_date)
        if published_at is None:
            continue
        if not cutoff <= published_at.astimezone(CST) <= now:
            continue

        # Some feeds bump pubDate when an old page is edited. If the article URL
        # carries its original date, require that date to overlap the real window.
        path_date_match = _DATED_ARTICLE_PATH_RE.search(urlsplit(link).path)
        if path_date_match:
            try:
                path_date = datetime.strptime("-".join(path_date_match.groups()), "%Y-%m-%d").date()
            except ValueError:
                continue
            if not cutoff.date() <= path_date <= now.date():
                continue

        candidates.append({
            "source": source,
            "title": title,
            "url": link,
            "published_at": published_at.isoformat(),
            "summary": summary[:MAX_CANDIDATE_SUMMARY_CHARS],
            "eligible_for_top": "true",
        })
    return candidates


def get_official_candidates(
    previous_stories: Optional[list[dict[str, str]]] = None,
    now: Optional[datetime] = None,
) -> list[dict[str, str]]:
    """Fetch fresh candidates from official feeds, failing open per source."""
    now = now or NOW
    previous_urls = {story["url"] for story in (previous_stories or [])}
    candidates: dict[str, dict[str, str]] = {}
    for source, feed_url in OFFICIAL_UPDATE_FEEDS:
        try:
            request = Request(feed_url, headers={"User-Agent": "hiwd-daily/1.0"})
            with urlopen(request, timeout=15) as response:
                xml_data = response.read()
        except Exception as error:
            print(f"  ⚠️ Could not load {source} feed: {error}")
            continue
        for candidate in parse_official_feed(xml_data, source, now):
            if candidate["url"] not in previous_urls:
                candidates[candidate["url"]] = candidate
    # Keep the prompt broad and interleaved: the model sees one item per source
    # before any high-volume publisher is allowed to repeat.
    ordered = sorted(
        candidates.values(),
        key=lambda candidate: candidate["published_at"],
        reverse=True,
    )
    by_source: dict[str, list[dict[str, str]]] = {}
    for candidate in ordered:
        by_source.setdefault(candidate["source"], []).append(candidate)
    source_order = [
        source for source, _ in OFFICIAL_UPDATE_FEEDS if source in by_source
    ]
    balanced = [
        by_source[source][position]
        for position in range(4)
        for source in source_order
        if position < len(by_source[source])
    ]
    return balanced[:24]


def _fetch_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": "hiwd-daily/2.0"})
    with urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def get_hacker_news_candidates(now: Optional[datetime] = None) -> list[dict[str, str]]:
    """Get fresh AI discussions from HN's public Firebase API.

    HN is discovery-only: its post timestamp cannot prove the publication time
    of an external article, so these entries never qualify for Top 3.
    """
    now = now or NOW
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    try:
        ids = _fetch_json("https://hacker-news.firebaseio.com/v0/topstories.json")
        if not isinstance(ids, list):
            return []
    except Exception as error:
        print(f"  ⚠️ Could not load Hacker News: {error}")
        return []

    def fetch_item(item_id: int) -> object:
        try:
            return _fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        items = list(pool.map(fetch_item, ids[:36]))

    candidates: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "story":
            continue
        title = _plain_text(str(item.get("title", "")))
        timestamp = item.get("time")
        if not title or not isinstance(timestamp, (int, float)) or not _AI_UPDATE_RE.search(title):
            continue
        published_at = datetime.fromtimestamp(timestamp, timezone.utc)
        if not cutoff <= published_at.astimezone(CST) <= now:
            continue
        discussion_url = f"https://news.ycombinator.com/item?id={item.get('id')}"
        candidates.append({
            "source": "Hacker News",
            "title": title,
            "url": discussion_url,
            "published_at": published_at.isoformat(),
            "summary": f"HN discussion score {item.get('score', 0)}. " + _plain_text(str(item.get("text", "")))[:260],
            "eligible_for_top": "false",
        })
    return candidates[:8]


def get_github_release_candidates(now: Optional[datetime] = None) -> list[dict[str, str]]:
    """Use GitHub's public Events API for newly released AI developer tools."""
    now = now or NOW
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    try:
        events = _fetch_json("https://api.github.com/events?per_page=100")
    except Exception as error:
        print(f"  ⚠️ Could not load GitHub public events: {error}")
        return []
    if not isinstance(events, list):
        return []
    candidates: list[dict[str, str]] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "ReleaseEvent":
            continue
        payload = event.get("payload") or {}
        release = payload.get("release") or {}
        repo = event.get("repo") or {}
        title = _plain_text(str(release.get("name") or release.get("tag_name") or ""))
        repo_name = _plain_text(str(repo.get("name", "")))
        text = f"{repo_name} {title} {_plain_text(str(release.get('body', '')))}"
        if not title or not _AI_UPDATE_RE.search(text):
            continue
        try:
            published_at = datetime.fromisoformat(str(event["created_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if not cutoff <= published_at.astimezone(CST) <= now:
            continue
        url = str(release.get("html_url", ""))
        if not url:
            continue
        candidates.append({
            "source": f"GitHub Release · {repo_name}",
            "title": title,
            "url": canonicalize_url(url),
            "published_at": published_at.isoformat(),
            "summary": _plain_text(str(release.get("body", "")))[:MAX_CANDIDATE_SUMMARY_CHARS],
            "eligible_for_top": "true",
        })
    return candidates[:8]



def build_user_prompt(previous_stories=None, official_candidates=None) -> str:
    """Build a bounded editing prompt from pre-validated public candidates.

    Historical de-duplication is deterministic and local.  Never sending the
    entire archive to the model prevents prompt growth as the site ages.
    """
    candidates = (official_candidates or [])[:MAX_CANDIDATES]
    candidate_list = "\n".join(
        " | ".join((
            candidate.get("published_at", ""),
            candidate.get("source", ""),
            candidate.get("title", ""),
            candidate.get("url", ""),
            candidate.get("summary", "")[:MAX_CANDIDATE_SUMMARY_CHARS],
            f"top={candidate.get('eligible_for_top', 'true')}",
        ))
        for candidate in candidates
    )[:MAX_MODEL_INPUT_CHARS]
    return f"""请把以下已验证候选整理为 hiwd daily。只依据候选中提供的事实、日期和 URL，不得搜索、不得补写不存在的链接或细节。

读者是关心 AI 时代变化的普通人。优先选择真正改变产品能力、开发者工作流、成本、基础设施或商业格局的信号；不要堆砌新闻，不要写无意义摘要，不要过度技术化。所有正文使用简洁、有观点的中文。

硬规则：
- Top 3 只能选择 `top=true` 的候选，最多 3 条，且发布方不同；URL、来源和 published_at 必须逐字使用候选值。
- `top=false` 是 Hacker News 讨论线索，只能作为“其他值得看的”，不可进入 Top 3。
- 不足 3 条时宁缺毋滥；没有合格内容时 top_stories 返回空数组。
- 排除传言、营销、超过 48 小时的内容及敏感政治叙事。
- 只输出一个 JSON 对象，不要 markdown、不要解释。JSON 格式：
{{
  "top_stories": [{{"title":"中文标题","url":"候选 URL","source":"候选来源","published_at":"候选时间","what_happened":"一句","why_it_matters":"一句","who_is_affected":"一句","product_angle":"一句"}}],
  "other_stories": [{{"title":"中文标题","url":"候选 URL","source":"候选来源","summary":"一句"}}],
  "theme_observation": "可选的 2-3 句主题观察；没有则空字符串",
  "source_note": "直接提供内容的来源名称，用顿号分隔"
}}

候选（时间 | 来源 | 标题 | URL | 摘要 | 是否可进 Top 3）：
{candidate_list}
"""


# ── Generated-content validation ─────────────────────────────────────────────
_TOP_MARKDOWN_RE = re.compile(
    r"^#{1,3}\s+🎯[^\n]*Top\s*3[^\n]*\n(.*?)(?=^#{1,3}\s|\Z)",
    flags=re.DOTALL | re.MULTILINE | re.IGNORECASE,
)
_TITLE_LINE_RE = re.compile(
    r"^\*\*标题\*\*\s*[：:]\s*\[([^\]\n]+)\]\((https?://[^)\s]+)\)",
    flags=re.MULTILINE,
)
_PUBLISHED_AT_RE = re.compile(
    r"<!--\s*published_at:\s*([^>]+?)\s*-->",
    flags=re.IGNORECASE,
)
_SOURCE_LINE_RE = re.compile(
    r"^\*\*来源\*\*\s*[：:](.*)$",
    flags=re.MULTILINE,
)
_OTHER_MARKDOWN_RE = re.compile(
    r"^#{1,3}\s+📰[^\n]*其他值得看的[^\n]*\n(.*?)(?=^#{1,3}\s|\Z)",
    flags=re.DOTALL | re.MULTILINE | re.IGNORECASE,
)
_OTHER_ITEM_RE = re.compile(
    r"^-\s+\*\*\[([^\]\n]+)\]\((https?://[^)\s]+)\)\*\*\s*·",
    flags=re.MULTILINE,
)
_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_NO_NEWS_RE = re.compile(r"(?:没有|暂无|无)\S{0,12}(?:符合|合格|新闻|内容|动态|发布)|0\s*条")


def parse_top_stories(briefing: str) -> list[dict[str, object]]:
    """Parse Top-3 title, URL, and machine-checkable publication time."""
    section_match = _TOP_MARKDOWN_RE.search(briefing)
    if not section_match:
        return []
    section = section_match.group(1)
    title_matches = list(_TITLE_LINE_RE.finditer(section))
    stories: list[dict[str, object]] = []
    for index, match in enumerate(title_matches):
        block_end = title_matches[index + 1].start() if index + 1 < len(title_matches) else len(section)
        block = section[match.end():block_end]
        timestamp_match = _PUBLISHED_AT_RE.search(block)
        source_match = _SOURCE_LINE_RE.search(block)
        source_metadata = source_match.group(1).rsplit("·", 1)[-1] if source_match else ""
        source_dates = _ISO_DATE_RE.findall(source_metadata)
        published_at = None
        published_date = None
        if timestamp_match:
            raw_timestamp = timestamp_match.group(1).strip().replace("Z", "+00:00")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_timestamp):
                try:
                    published_date = datetime.strptime(raw_timestamp, "%Y-%m-%d").date()
                except ValueError:
                    pass
            else:
                try:
                    published_at = datetime.fromisoformat(raw_timestamp)
                    published_date = published_at.date()
                except ValueError:
                    pass
        stories.append({
            "title": match.group(1).strip(),
            "url": canonicalize_url(match.group(2)),
            "published_at": published_at,
            "published_date": published_date,
            "source_dates": source_dates,
        })
    return stories


def _source_family(url: str) -> str:
    """Return a stable publisher family from an article URL."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    aliases = {
        "github.blog": "github",
        "github.com": "github",
        "developers.cloudflare.com": "cloudflare",
        "cloudflare.com": "cloudflare",
        "vercel.com": "vercel",
        "huggingface.co": "huggingface",
        "blog.google": "google",
        "deepmind.google": "google",
        "techcrunch.com": "techcrunch",
        "theverge.com": "theverge",
    }
    for domain, family in aliases.items():
        if host == domain or host.endswith(f".{domain}"):
            return family
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def parse_other_stories(briefing: str) -> list[dict[str, str]]:
    """Parse compact story links from the ‘other worthwhile reads’ section."""
    section_match = _OTHER_MARKDOWN_RE.search(briefing)
    if not section_match:
        return []
    return [
        {"title": match.group(1).strip(), "url": canonicalize_url(match.group(2))}
        for match in _OTHER_ITEM_RE.finditer(section_match.group(1))
    ]


def _normalise_title(title: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", title.lower())


def validate_briefing(
    briefing: str,
    previous_stories: Optional[list[dict[str, str]]] = None,
    now: Optional[datetime] = None,
    official_candidates: Optional[list[dict[str, str]]] = None,
) -> list[str]:
    """Return publish-blocking errors for freshness and duplicate violations."""
    now = now or NOW
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    section_match = _TOP_MARKDOWN_RE.search(briefing)
    if not section_match:
        return ["缺少‘今日 Top 3’章节"]

    stories = parse_top_stories(briefing)
    if not stories:
        if official_candidates:
            return [
                f"官方订阅源已有 {len(official_candidates)} 条新候选，Top 3 不得为空"
            ]
        no_news = _NO_NEWS_RE.search(section_match.group(1))
        return [] if no_news else ["Top 3 中没有可解析的条目，也没有明确注明过去 48 小时无合格内容"]
    if len(stories) > 3:
        return [f"Top 3 实际包含 {len(stories)} 条，超过 3 条"]

    errors: list[str] = []
    candidate_families = {
        _source_family(candidate.get("url", ""))
        for candidate in (official_candidates or [])
        if candidate.get("url") and candidate.get("eligible_for_top", "true") == "true"
    }
    required_count = min(3, len(candidate_families))
    if len(stories) < required_count:
        errors.append(
            f"可信订阅源覆盖 {len(candidate_families)} 个发布方，"
            f"Top 3 至少需要 {required_count} 条，实际只有 {len(stories)} 条"
        )
    top_families = [_source_family(str(story["url"])) for story in stories]
    repeated_top_families = {
        family for family in top_families if top_families.count(family) > 1
    }
    if repeated_top_families:
        errors.append(
            "Top 3 同一发布方最多 1 条；重复发布方："
            + "、".join(sorted(repeated_top_families))
        )

    other_stories = parse_other_stories(briefing)
    other_families = [_source_family(story["url"]) for story in other_stories]
    repeated_other_families = {
        family for family in other_families if other_families.count(family) > 2
    }
    if repeated_other_families:
        errors.append(
            "‘其他值得看的’同一发布方最多 2 条；超额发布方："
            + "、".join(sorted(repeated_other_families))
        )
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    previous_stories = previous_stories or []
    previous_urls = {story["url"] for story in previous_stories}
    previous_titles = [
        (_normalise_title(story["title"]), story["title"])
        for story in previous_stories
        if story.get("title")
    ]

    for position, story in enumerate(stories, start=1):
        title = str(story["title"])
        url = str(story["url"])
        published_at = story["published_at"]
        published_date = story["published_date"]
        source_dates = story["source_dates"]
        if isinstance(published_at, datetime) and published_at.tzinfo is not None:
            if not cutoff <= published_at <= now:
                errors.append(
                    f"第 {position} 条《{title}》发布时间 {published_at.isoformat()} "
                    f"不在 {cutoff.isoformat()} 至 {now.isoformat()} 内"
                )
        elif published_date is not None:
            if not cutoff.date() <= published_date <= now.date():
                errors.append(
                    f"第 {position} 条《{title}》发布日期 {published_date.isoformat()} "
                    f"超出 48 小时窗口涉及的日期范围"
                )
        else:
            errors.append(f"第 {position} 条《{title}》缺少有效的 published_at 日期或时间")

        if isinstance(published_at, datetime) and published_at.tzinfo is None:
            errors.append(
                f"第 {position} 条《{title}》提供了具体时刻但没有时区"
            )
        if not isinstance(source_dates, list) or len(source_dates) != 1:
            errors.append(f"第 {position} 条《{title}》的来源行必须且只能包含一个 YYYY-MM-DD 发布日期")
        else:
            try:
                source_date = datetime.strptime(source_dates[0], "%Y-%m-%d").date()
            except ValueError:
                errors.append(f"第 {position} 条《{title}》的来源日期无效：{source_dates[0]}")
            else:
                if source_date < cutoff.date() or source_date > now.date():
                    errors.append(
                        f"第 {position} 条《{title}》展示的来源日期 {source_date.isoformat()} "
                        f"超出 48 小时窗口涉及的日期范围"
                    )
                # Feeds commonly store UTC while an article displays the
                # publisher's local date. A one-day difference is a valid
                # timezone boundary; larger gaps remain publish-blocking.
                if published_date is not None and abs((source_date - published_date).days) > 1:
                    errors.append(
                        f"第 {position} 条《{title}》的来源日期 {source_date.isoformat()} "
                        f"与 published_at 日期 {published_date.isoformat()} 不一致"
                    )

        path = urlsplit(url).path.rstrip("/").lower()
        if path in {"", "/blog", "/news", "/research", "/ai"}:
            errors.append(f"第 {position} 条《{title}》使用主页或栏目页，缺少可核验的文章 URL")

        if url in seen_urls:
            errors.append(f"第 {position} 条《{title}》与本期其他条目 URL 重复")
        seen_urls.add(url)
        if url in previous_urls:
            errors.append(f"第 {position} 条《{title}》的 URL 已在历史简报中报道")

        normalised_title = _normalise_title(title)
        if normalised_title in seen_titles:
            errors.append(f"第 {position} 条《{title}》与本期其他条目标题重复")
        seen_titles.append(normalised_title)
        for old_normalised, old_title in previous_titles:
            if normalised_title == old_normalised or difflib.SequenceMatcher(
                None, normalised_title, old_normalised
            ).ratio() >= 0.9:
                errors.append(
                    f"第 {position} 条《{title}》疑似重复历史事件《{old_title}》"
                )
                break
    return errors


def format_empty_top_state(briefing: str) -> str:
    """Replace technical no-news explanations with concise reader-facing copy.

    This only touches a Top-3 section that contains no parsed stories and an
    explicit no-news statement. A normal generated briefing passes through
    unchanged.
    """
    section_match = _TOP_MARKDOWN_RE.search(briefing)
    if not section_match or parse_top_stories(briefing):
        return briefing
    if not _NO_NEWS_RE.search(section_match.group(1)):
        return briefing

    heading = section_match.group(0).splitlines()[0]
    replacement = (
        f"{heading}\n\n"
        "**今天暂时没有新的重点动态**\n\n"
        "过去 48 小时内，暂未发现来源可靠、值得关注且没有重复报道的新消息。"
        "我们会继续关注，有重要进展会及时更新。\n\n"
    )
    return briefing[:section_match.start()] + replacement + briefing[section_match.end():]


def build_official_feed_fallback(official_candidates: Optional[list[dict[str, str]]] = None) -> str:
    """Publish verified metadata when DeepSeek is unavailable or invalid."""
    selected: list[dict[str, str]] = []
    seen_publishers: set[str] = set()
    for candidate in official_candidates or []:
        url = candidate.get("url", "")
        family = _source_family(url)
        if not url or candidate.get("eligible_for_top", "true") != "true" or family in seen_publishers:
            continue
        selected.append(candidate)
        seen_publishers.add(family)
        if len(selected) == 3:
            break
    if not selected:
        return (
            "### 🎯 今日 Top 3\n\n**今天暂时没有新的重点动态**\n\n"
            "过去 48 小时内，暂未发现来源可靠、值得关注且没有重复报道的新消息。"
            "我们会继续关注，有重要进展会及时更新。\n\n"
            "### 📰 其他值得看的\n\n### ⚠️ 信息来源说明\n\n"
            "- 本期检查了公开订阅源，未发现可发布的新条目。\n"
        )
    blocks = []
    for candidate in selected:
        blocks.append(
            f"**标题**：[{candidate['title']}]({candidate['url']})\n\n"
            f"**来源**：[{candidate['source']}]({candidate['url']}) · {candidate['published_at'][:10]}\n\n"
            f"<!-- published_at: {candidate['published_at']} -->\n\n"
            "**摘要**：\n\n- 官方或可信媒体订阅源确认了这项最新动态\n"
            "- 条目发布于最近 48 小时，具备产品或开发者生态参考价值\n"
            "- 相关用户与开发者可通过原文了解完整细节\n\n"
            "**产品技术视角**：本条仅依据已核验的订阅源元数据发布，不对原文未提供的细节作额外推断。"
        )
    sources = "、".join(candidate["source"] for candidate in selected)
    return (
        "### 🎯 今日 Top 3\n\n" + "\n\n---\n\n".join(blocks)
        + "\n\n### 📰 其他值得看的\n\n### ⚠️ 信息来源说明\n\n"
        + f"- 直接提供内容的源：{sources}\n- 所有链接均来自已核验的公开数据源。\n"
    )


def _api_create_with_retry(client: OpenAI, messages: list[dict[str, str]]):
    """Make exactly one bounded request; the caller owns the two-call budget."""
    try:
        return client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            max_tokens=MAX_MODEL_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
    except AuthenticationError:
        print("❌ Authentication error — check DEEPSEEK_API_KEY", file=sys.stderr)
        raise
    except (APIConnectionError, RateLimitError, APIStatusError):
        raise


def briefing_from_payload(payload: dict[str, object], candidates: list[dict[str, str]]) -> str:
    """Render JSON and enforce candidate URL/date/source allow-lists locally."""
    by_url = {candidate["url"]: candidate for candidate in candidates if candidate.get("url")}
    selected: list[dict[str, str]] = []
    families: set[str] = set()
    for item in payload.get("top_stories", []) if isinstance(payload.get("top_stories"), list) else []:
        if not isinstance(item, dict):
            continue
        candidate = by_url.get(canonicalize_url(str(item.get("url", ""))))
        fields = ("title", "what_happened", "why_it_matters", "who_is_affected", "product_angle")
        if (not candidate or candidate.get("eligible_for_top") != "true"
                or _source_family(candidate["url"]) in families
                or not all(_plain_text(str(item.get(field, ""))) for field in fields)):
            continue
        selected.append({
            "title": _plain_text(str(item["title"]))[:120],
            "url": candidate["url"], "source": candidate["source"],
            "published_at": candidate["published_at"],
            **{field: _plain_text(str(item[field]))[:130] for field in fields[1:]},
        })
        families.add(_source_family(candidate["url"]))
        if len(selected) == 3:
            break
    if not selected:
        return build_official_feed_fallback(candidates)

    def render(story: dict[str, str]) -> str:
        return (
            f"**标题**：[{story['title']}]({story['url']})\n\n"
            f"**来源**：[{story['source']}]({story['url']}) · {story['published_at'][:10]}\n\n"
            f"<!-- published_at: {story['published_at']} -->\n\n**摘要**：\n\n"
            f"- {story['what_happened']}\n- {story['why_it_matters']}\n- {story['who_is_affected']}\n\n"
            f"**产品技术视角**：{story['product_angle']}"
        )
    other: list[str] = []
    counts: dict[str, int] = {}
    selected_urls = {story["url"] for story in selected}
    for item in payload.get("other_stories", []) if isinstance(payload.get("other_stories"), list) else []:
        if not isinstance(item, dict):
            continue
        candidate = by_url.get(canonicalize_url(str(item.get("url", ""))))
        family = _source_family(candidate["url"]) if candidate else ""
        title, summary = _plain_text(str(item.get("title", "")))[:120], _plain_text(str(item.get("summary", "")))[:150]
        if not candidate or candidate["url"] in selected_urls or counts.get(family, 0) >= 2 or not title or not summary:
            continue
        other.append(f"- **[{title}]({candidate['url']})** · {candidate['source']}\n- {summary}")
        counts[family] = counts.get(family, 0) + 1
        if len(other) == 8:
            break
    theme = _plain_text(str(payload.get("theme_observation", "")))[:360]
    sources = "、".join(dict.fromkeys(story["source"] for story in selected))
    briefing = "### 🎯 今日 Top 3\n\n" + "\n\n---\n\n".join(render(story) for story in selected)
    briefing += "\n\n### 📰 其他值得看的\n\n" + "\n\n".join(other)
    if theme:
        briefing += "\n\n### 🔍 今日主题观察\n\n" + theme
    return briefing + f"\n\n### ⚠️ 信息来源说明\n\n- 直接提供内容的源：{sources}\n- 所有链接均来自程序已核验的公开数据源。\n"


def fetch_briefing(user_prompt: str, previous_stories=None, official_candidates=None) -> str:
    """Use at most two DeepSeek calls; fall back to verified source metadata."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise EnvironmentError("DEEPSEEK_API_KEY environment variable is not set")
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT + " 只输出有效 JSON。"},
        {"role": "user", "content": user_prompt},
    ]
    print(f"📡 Calling DeepSeek {DEEPSEEK_MODEL} for {TODAY_ISO} (max {MAX_MODEL_CALLS} calls)...")
    for attempt in range(MAX_MODEL_CALLS):
        response = _api_create_with_retry(client, messages)
        content = response.choices[0].message.content or "{}"
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = {}
        briefing = format_empty_top_state(clean_briefing(briefing_from_payload(payload, official_candidates or [])))
        errors = validate_briefing(briefing, previous_stories, official_candidates=official_candidates)
        if not contains_sensitive_politics(briefing) and not errors:
            return briefing
        if attempt + 1 < MAX_MODEL_CALLS:
            reason = "敏感内容" if contains_sensitive_politics(briefing) else "；".join(errors)
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"上次 JSON 未通过发布校验：{reason}。仅用给定候选重做完整 JSON。"}]
    fallback = build_official_feed_fallback(official_candidates)
    errors = validate_briefing(fallback, previous_stories, official_candidates=official_candidates)
    if errors:
        raise RuntimeError("DeepSeek output and feed fallback both failed validation: " + "; ".join(errors))
    print("⚠️ DeepSeek output did not pass validation; publishing verified-source fallback")
    return fallback


# ── Markdown → HTML ───────────────────────────────────────────────────────────
def clean_briefing(text: str) -> str:
    """Strip LLM preamble and fix common markdown formatting issues."""
    # 0. Strip trailing whitespace on every line FIRST.
    #    Markdown treats "line  \n" (two trailing spaces) as a hard <br>.
    #    Models may emit these unintentionally, causing cramped output.
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)

    # 0a. Fix malformed reference-style links where the URL was emitted as
    #     the reference label: [title][https://example.com] -> [title](https://example.com)
    text = re.sub(
        r'(?<!!)\[([^\]\n]+)\]\[(https?://[^\]\s]+)\]',
        r'[\1](\2)',
        text,
    )

    # 0b. Ensure Top-3 briefing field labels start new paragraphs.
    #     Without a blank line before them, markdown renders everything in one <p>.
    #     Model output may place the colon outside bold, so pattern must include \*\*.
    text = re.sub(
        r'(?m)(?<!\n)\n(\*\*(?:来源|摘要|产品技术视角)\*\*\s*[：:])',
        r'\n\n\1',
        text,
    )

    # 0c. Ensure a blank line between **摘要**： and the first bullet item.
    #     sane_lists requires a blank line before any list that follows text;
    #     without it, "- item" is treated as plain text inside the label's <p>.
    #     Model output may place the colon outside bold, so pattern handles it.
    text = re.sub(
        r'(\*\*摘要\*\*\s*[：:])\n(-\s)',
        r'\1\n\n\2',
        text,
    )

    # 1. Drop everything before the first heading
    match = re.search(r'^#{1,3}\s', text, re.MULTILINE)
    if match:
        text = text[match.start():]

    # 2. Fix broken bold: **\n内容\n** → **内容**
    text = re.sub(
        r'\*\*\s*\n([^\n*]{1,200})\n\s*\*\*',
        lambda m: f'**{m.group(1).strip()}**',
        text,
    )

    # 3. Remove standalone semicolons used as sentence separators
    text = re.sub(r'^\s*[；;]\s*$', '', text, flags=re.MULTILINE)

    # 4. Remove standalone single-dash lines (empty pseudo-list items)
    text = re.sub(r'^\s*-\s*$', '', text, flags=re.MULTILINE)

    # 5. Collapse 3+ blank lines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 6. Strip word-count / self-evaluation lines (e.g. "总字数：~850 字 | 符合要求精炼程度")
    text = re.sub(r'^总字数[：:].+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)  # re-collapse after removal

    return text.strip()


def _format_theme_observation_block(match: re.Match) -> str:
    heading, body = match.groups()
    if "：<strong>" not in body:
        return match.group(0)

    intro, rest = body.split("：<strong>", 1)
    intro = intro.strip() + "："
    items = [part.strip() for part in re.split(r'；(?=<strong>)', f'<strong>{rest}') if part.strip()]
    if not items:
        return match.group(0)

    list_items = "".join(f"<li>{item}</li>" for item in items)
    return (
        f'{heading}<div class="theme-observation">'
        f'<p class="theme-intro">{intro}</p>'
        f'<ul class="theme-list">{list_items}</ul>'
        f'</div>'
    )


def md_to_html(text: str) -> str:
    html = md_lib.markdown(
        text,
        extensions=["extra", "sane_lists"],
    )
    # Fallback: if any <br> + bold field label combos remain, split into proper <p>.
    # Handles cases where clean_briefing didn't add blank lines (e.g., older content).
    # The rendered label becomes <strong>来源</strong>：, so match the closing tag.
    html = re.sub(
        r'<br\s*/?>\s*\n(<strong>(?:来源|摘要)</strong>\s*[：：])',
        r'</p>\n<p>\1',
        html,
    )
    html = re.sub(
        r'\n(<strong>产品技术视角</strong>\s*[：：])',
        r'</p>\n<p>\1',
        html,
    )
    # Remove stray newlines before Chinese punctuation.
    # Model output can split a sentence mid-line; in HTML a bare \n followed
    # by ，。；etc. renders as " ，" (space + punctuation) which looks wrong.
    html = re.sub(r'[ \t]*\n[ \t]*([，。；：！？、—])', r'\1', html)
    html = re.sub(
        r'(<h2>🔍 今日主题观察</h2>)\s*<p>(.*?)</p>',
        _format_theme_observation_block,
        html,
        flags=re.S,
    )
    return html


def build_archive_nav(entries: list) -> str:
    """
    Build archive nav HTML grouped by month, collapsible via <details>.
    Last 3 months shown as collapsed <details>; older months inside "查看更早".
    Default visible rows: ≤4 (3 month headers + "查看更早"), regardless of total entries.
    URL format: /archive/YYYY-MM/YYYY-MM-DD.html
    """
    if not entries:
        return '<p class="no-archive">暂无历史记录</p>'

    # Group by YYYY-MM, newest first
    months: dict = {}
    for entry in sorted(entries, key=lambda e: e["date"], reverse=True):
        month = entry["date"][:7]
        months.setdefault(month, []).append(entry["date"])

    def render_month(month: str, dates: list) -> str:
        count = len(dates)
        items = "\n".join(
            f'    <li data-date="{d}"><a href="/archive/{month}/{d}.html">{d}</a></li>'
            for d in sorted(dates, reverse=True)
        )
        return (
            f'<details class="month-item">\n'
            f'  <summary>{month} <span class="month-count">{count}篇</span></summary>\n'
            f'  <ul class="day-list">\n{items}\n  </ul>\n'
            f'</details>'
        )

    month_keys = sorted(months.keys(), reverse=True)
    recent = month_keys[:3]
    older = month_keys[3:]

    parts = [render_month(m, months[m]) for m in recent]

    if older:
        older_html = "\n".join(render_month(m, months[m]) for m in older)
        parts.append(
            f'<details class="older-archive">\n'
            f'  <summary>查看更早</summary>\n'
            f'  <div class="older-months">\n{older_html}\n  </div>\n'
            f'</details>'
        )

    return '<div class="archive-nav">' + "\n".join(parts) + "</div>"


# ── HTML template (uses [[PLACEHOLDER]] to avoid .format() escaping CSS) ──────
# Design mirrors hiwd.com exactly:
#   bg #f5f5f5 · card white + shadow + border-radius 12px
#   accent #00C2B3 · links #008F84
#   headings: left colored bar (h1→4px solid, h2/h3→2px semi-transparent)
#   logo: fixed top-left, links back to hiwd.com
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>[[PAGE_TITLE]]</title>
  <meta name="description" content="AI 行业每日精选 [[DATE_CN]] [[WEEKDAY]]" />
  <link rel="icon" type="image/x-icon" href="/favicon.ico?v=3" />
  <link rel="alternate" type="application/rss+xml" title="hiwd daily · AI 行业每日简报" href="/rss.xml" />
  <script src="/theme.js?v=20260704-1"></script>
  <link rel="stylesheet" type="text/css" href="/style.css?v=20260707-1" />
</head>
<body>

  <a href="https://hiwd.com/" id="logo" aria-label="返回 hiwd 主站"></a>

  <div id="content">

    <div class="hero">
      <div class="hero-title-row">
        <h1>AI 行业每日简报</h1>
        <span class="hero-date">[[DATE_CN]] [[WEEKDAY]]</span>
      </div>
    </div>

    <!-- Briefing body -->
    [[CONTENT]]

    <!-- Archive -->
    <div class="archive-section">
      <h2>历史存档</h2>
      [[ARCHIVE]]
    </div>

  </div>

  <div id="footer">
    <div class="footer-meta">由 hiwd daily 自动整理</div>
    <div>© 2026 <a href="https://hiwd.com/">hiwd</a> · All rights reserved. <button class="theme-toggle" type="button" data-theme-toggle>夜间</button></div>
  </div>
  <script>
    // Dynamically mark today's entry in the archive nav.
    // Using browser time shifted to CST (UTC+8) so it matches the generation timezone.
    (function () {
      const cstNow = new Date(Date.now() + 8 * 3600 * 1000);
      const today = cstNow.toISOString().slice(0, 10);
      const li = document.querySelector('[data-date="' + today + '"]');
      if (!li) return;
      li.classList.add('active');
      const a = li.querySelector('a');
      if (a) {
        a.innerHTML = today + ' <span class="today-tag">今日</span>';
      }
    })();
  </script>

</body>
</html>
"""


def render_page_from_html(content_html: str, archive_entries: list[dict],
                          page_title: Optional[str] = None,
                          date_iso: Optional[str] = None) -> str:
    date_iso = date_iso or TODAY_ISO
    date_cn, weekday_cn = format_display_date(date_iso)
    archive_html = build_archive_nav(archive_entries)
    if page_title is None:
        page_title = f"AI 行业每日简报 · {date_cn}"

    return (
        HTML_TEMPLATE
        .replace("[[PAGE_TITLE]]", page_title)
        .replace("[[DATE_CN]]", date_cn)
        .replace("[[DATE_ISO]]", date_iso)
        .replace("[[WEEKDAY]]", weekday_cn)
        .replace("[[CONTENT]]", content_html)
        .replace("[[ARCHIVE]]", archive_html)
    )


def render_page(briefing_md: str, archive_entries: list[dict],
                page_title: Optional[str] = None,
                date_iso: Optional[str] = None) -> str:
    content_html = md_to_html(briefing_md)
    return render_page_from_html(
        content_html,
        archive_entries,
        page_title=page_title,
        date_iso=date_iso,
    )


# ── RSS feed ──────────────────────────────────────────────────────────────────
RSS_SITE_URL = "https://daily.hiwd.com/"
RSS_FEED_URL = "https://daily.hiwd.com/rss.xml"
RSS_TITLE = "hiwd daily · AI 行业每日简报"
RSS_DESCRIPTION = "由 hiwd daily 自动整理的 AI 行业每日精选"
RSS_COPYRIGHT = "© 2026 hiwd · All rights reserved. https://hiwd.com/"
RSS_ITEM_LIMIT = 14

# Match the briefing body emitted by HTML_TEMPLATE between these two markers.
_BRIEFING_BODY_RE = re.compile(
    r"<!-- Briefing body -->\s*(.*?)\s*<!-- Archive -->",
    re.DOTALL,
)


def extract_briefing_body(archive_file: Path) -> Optional[str]:
    """Pull just the briefing HTML out of an archived day page.

    The archive pages embed the full template (logo, footer, archive nav).
    For RSS we only want the inner briefing — between the
    `<!-- Briefing body -->` and `<!-- Archive -->` markers.
    """
    try:
        html = archive_file.read_text("utf-8")
    except OSError:
        return None
    match = _BRIEFING_BODY_RE.search(html)
    if not match:
        return None
    return match.group(1).strip()


def build_rss(archive_dir: Path, archive_entries: list[dict],
              today_html: Optional[str] = None) -> str:
    """Build an RSS 2.0 feed from the most recent archived briefings.

    today_html, when provided, is the just-rendered briefing body for the
    current day — it lets us include today's entry without re-reading the
    archive file (which is identical in content but a tick stale on disk).
    """
    # Newest dates first; cap at RSS_ITEM_LIMIT.
    sorted_entries = sorted(archive_entries, key=lambda e: e["date"], reverse=True)
    selected = sorted_entries[:RSS_ITEM_LIMIT]

    items_xml: list[str] = []
    for entry in selected:
        date_iso = entry["date"]
        # 23:59 CST so the published time always lies within the calendar day
        # in the user's likely timezones.
        try:
            pub_dt = datetime.strptime(date_iso, "%Y-%m-%d").replace(
                hour=23, minute=59, tzinfo=CST,
            )
        except ValueError:
            continue
        pub_date = format_datetime(pub_dt)

        if date_iso == TODAY_ISO and today_html:
            body_html = today_html
        else:
            body_html = extract_briefing_body(
                archive_dir / date_iso[:7] / f"{date_iso}.html"
            )
        if not body_html:
            continue

        link = f"{RSS_SITE_URL}archive/{date_iso[:7]}/{date_iso}.html"
        title = f"AI 行业每日简报 · {date_iso}"
        guid = link

        items_xml.append(
            "    <item>\n"
            f"      <title>{xml_escape(title)}</title>\n"
            f"      <link>{xml_escape(link)}</link>\n"
            f"      <guid isPermaLink=\"true\">{xml_escape(guid)}</guid>\n"
            f"      <pubDate>{pub_date}</pubDate>\n"
            f"      <description><![CDATA[{body_html}]]></description>\n"
            "    </item>"
        )

    last_build = format_datetime(NOW)
    items_block = "\n".join(items_xml)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<?xml-stylesheet type="text/xsl" href="/rss.xsl"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        '  <channel>\n'
        f'    <title>{xml_escape(RSS_TITLE)}</title>\n'
        f'    <link>{xml_escape(RSS_SITE_URL)}</link>\n'
        f'    <description>{xml_escape(RSS_DESCRIPTION)}</description>\n'
        '    <language>zh-CN</language>\n'
        f'    <copyright>{xml_escape(RSS_COPYRIGHT)}</copyright>\n'
        f'    <lastBuildDate>{last_build}</lastBuildDate>\n'
        f'    <atom:link href="{xml_escape(RSS_FEED_URL)}" rel="self" type="application/rss+xml" />\n'
        f'{items_block}\n'
        '  </channel>\n'
        '</rss>\n'
    )


# ── One-time migration ────────────────────────────────────────────────────────
def migrate_archive(docs_dir: Path) -> None:
    """Move flat archive/YYYY-MM-DD.html files into archive/YYYY-MM/ subdirs.

    Updates all internal /archive/DATE.html links in each file.
    Skips automatically if no flat HTML files are found (already migrated).
    """
    archive_dir = docs_dir / "archive"
    old_files = list(archive_dir.glob("????-??-??.html"))
    if not old_files:
        return  # Already migrated or nothing to do

    print(f"🔄 Migrating {len(old_files)} archive files to monthly subdirs...")
    link_re = re.compile(r'/archive/(\d{4}-\d{2}-\d{2})\.html')

    def rewrite_links(html: str) -> str:
        return link_re.sub(lambda m: f'/archive/{m.group(1)[:7]}/{m.group(1)}.html', html)

    for old_file in sorted(old_files):
        date = old_file.stem          # e.g. "2026-05-25"
        month = date[:7]              # e.g. "2026-05"
        month_dir = archive_dir / month
        month_dir.mkdir(parents=True, exist_ok=True)
        new_file = month_dir / f"{date}.html"
        html = rewrite_links(old_file.read_text("utf-8"))
        new_file.write_text(html, encoding="utf-8")
        old_file.unlink()
        print(f"  ✅ archive/{date}.html → archive/{month}/{date}.html")

    # Update docs/index.html links
    index_file = docs_dir / "index.html"
    if index_file.exists():
        html = index_file.read_text("utf-8")
        updated = rewrite_links(html)
        if updated != html:
            index_file.write_text(updated, encoding="utf-8")
            print("  ✅ Updated links in docs/index.html")

    print("✅ Migration complete")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    docs = Path(__file__).parent / "docs"
    archive_dir = docs / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # One-time migration: move flat archive files into monthly subdirs
    migrate_archive(docs)

    # Touch .nojekyll so GitHub Pages serves raw files
    (docs / ".nojekyll").touch()

    # Load existing archive index
    archive_json = docs / "archive.json"
    archive_entries: list[dict] = []
    if archive_json.exists():
        try:
            archive_entries = json.loads(archive_json.read_text("utf-8"))
        except json.JSONDecodeError as e:
            # Refuse to silently wipe history — fail the workflow loudly so
            # we don't lose months of archive entries to a transient corruption.
            raise RuntimeError(
                f"docs/archive.json is corrupted ({e}). Refusing to overwrite. "
                f"Restore from git history (git log -- docs/archive.json) and re-run."
            ) from e

    # Load the complete reporting history so old stories cannot reappear after
    # a short rolling deduplication window.
    previous_stories = get_previous_stories(archive_dir)
    if previous_stories:
        print(f"🔍 Loaded {len(previous_stories)} previously reported stories for deduplication")

    # Collect public-source candidates before one bounded DeepSeek editing call.
    # Historical de-duplication remains local and is never sent in full to the model.
    official_candidates = get_official_candidates(previous_stories)
    official_candidates += get_github_release_candidates()
    official_candidates += get_hacker_news_candidates()
    unique_candidates: dict[str, dict[str, str]] = {}
    previous_urls = {story["url"] for story in previous_stories}
    for candidate in official_candidates:
        if candidate.get("url") and candidate["url"] not in previous_urls:
            unique_candidates[candidate["url"]] = candidate
    official_candidates = list(unique_candidates.values())[:MAX_CANDIDATES]
    if official_candidates:
        print(f"📥 Loaded {len(official_candidates)} fresh public-source candidates")

    user_prompt = build_user_prompt(official_candidates=official_candidates)
    briefing_md = fetch_briefing(user_prompt, previous_stories, official_candidates)
    print(f"✅ Received {len(briefing_md)} chars from DeepSeek or verified-source fallback")

    # Add today to archive entries BEFORE rendering so it appears in the nav
    # and the JS "今日" highlight can find the entry.
    archive_updated = not any(e["date"] == TODAY_ISO for e in archive_entries)
    if archive_updated:
        archive_entries.append({"date": TODAY_ISO})
        archive_entries.sort(key=lambda e: e["date"])

    # Render HTML — two variants with different <title> for SEO:
    # archive page keeps the date (unique URL = unique title),
    # index page uses a stable keyword title (no date = better ranking for main page).
    archive_html = render_page(
        briefing_md, archive_entries,
        page_title=f"AI 行业每日简报 · {TODAY_CN} | hiwd",
    )
    index_html = render_page(
        briefing_md, archive_entries,
        page_title="AI 行业每日简报 | hiwd",
    )

    # Save archive copy (monthly subdir: docs/archive/YYYY-MM/YYYY-MM-DD.html)
    month_dir = archive_dir / TODAY_ISO[:7]
    month_dir.mkdir(parents=True, exist_ok=True)
    archive_file = month_dir / f"{TODAY_ISO}.html"
    archive_file.write_text(archive_html, encoding="utf-8")
    print(f"✅ Saved  → docs/archive/{TODAY_ISO[:7]}/{TODAY_ISO}.html")

    # Update index (latest briefing)
    (docs / "index.html").write_text(index_html, encoding="utf-8")
    print(f"✅ Updated → docs/index.html")

    # Persist archive index only when a new entry was added
    if archive_updated:
        archive_json.write_text(
            json.dumps(archive_entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"✅ Updated → docs/archive.json")

    # Build RSS feed (most recent N briefings, full HTML in CDATA)
    today_body = md_to_html(briefing_md)
    rss_xml = build_rss(archive_dir, archive_entries, today_html=today_body)
    (docs / "rss.xml").write_text(rss_xml, encoding="utf-8")
    print(f"✅ Updated → docs/rss.xml")


if __name__ == "__main__":
    main()
