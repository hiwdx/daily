#!/usr/bin/env python3
"""
Daily AI News Briefing Generator
每日 AI 简报生成器

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python generate.py
"""

import anthropic
import difflib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

try:
    import markdown as md_lib
except ImportError:
    print("❌ Missing 'markdown' package. Run: pip install markdown", file=sys.stderr)
    sys.exit(1)

# ── Date (China Standard Time UTC+8) ──────────────────────────────────────────
CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST)
FRESHNESS_HOURS = 48
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


def _feed_entries(root: ElementTree.Element) -> list[tuple[str, str, str]]:
    """Return title, article URL, and date from RSS or Atom XML."""
    entries: list[tuple[str, str, str]] = []
    for item in root.findall(".//item"):
        entries.append((
            html.unescape((item.findtext("title") or "").strip()),
            (item.findtext("link") or "").strip(),
            (item.findtext("pubDate") or item.findtext("date") or "").strip(),
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
        entries.append((html.unescape(title), article_link.strip(), raw_date))
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
    for title, raw_link, raw_pub_date in _feed_entries(root):
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

_USER_PROMPT_TEMPLATE = f"""你是我的 AI 产品情报分析师。请帮我完成今天（{TODAY_CN} {WEEKDAY_CN}）的 AI 行业每日简报。

## 搜索策略（严格限制 6 次）

为避免搜索引擎漏掉窗口边界日期，候选检索统一使用 `after:{SEARCH_AFTER_ISO} before:{SEARCH_BEFORE_ISO}`；这只是发现阶段的日期缓冲，最终收录仍必须通过严格的 48 小时校验。不能只搜索今天的日期。**总搜索次数不超过 6 次**：
- 搜索 1（高频产品更新）：`AI agent model API changelog after:{SEARCH_AFTER_ISO} before:{SEARCH_BEFORE_ISO} site:github.blog/changelog OR site:vercel.com/changelog OR site:developers.cloudflare.com/changelog`
- 搜索 2（核心实验室）：`AI model release after:{SEARCH_AFTER_ISO} before:{SEARCH_BEFORE_ISO} site:openai.com OR site:anthropic.com/news OR site:deepmind.google OR site:blog.google/technology/ai OR site:ai.meta.com/blog OR site:mistral.ai/news`
- 搜索 3（开发工具与开源）：`AI agent framework model release after:{SEARCH_AFTER_ISO} before:{SEARCH_BEFORE_ISO} site:huggingface.co/blog OR site:blog.langchain.com OR site:llamaindex.ai/blog OR site:replicate.com/blog OR site:together.ai/blog OR site:fireworks.ai/blog`
- 搜索 4（云平台）：`generative AI launch after:{SEARCH_AFTER_ISO} before:{SEARCH_BEFORE_ISO} site:aws.amazon.com/blogs OR site:developers.googleblog.com OR site:docs.cloud.google.com/vertex-ai OR site:learn.microsoft.com/azure/ai-foundry OR site:developer.nvidia.com/blog`
- 搜索 5（可信媒体）：`AI model product funding acquisition after:{SEARCH_AFTER_ISO} before:{SEARCH_BEFORE_ISO} site:reuters.com OR site:techcrunch.com OR site:theverge.com OR site:arstechnica.com`
- 搜索 6（中文）：`AI 大模型 产品 API 发布 {SEARCH_AFTER_ISO}..{TODAY_ISO} 机器之心 OR 量子位 OR InfoQ`

## 信息源优先级

### S 级（必须覆盖，一手源）
- OpenAI News (openai.com/news)
- Anthropic News (anthropic.com/news)
- Google DeepMind Blog (deepmind.google/discover/blog)
- Google AI / Developers Blog (blog.google/technology/ai、developers.googleblog.com)
- Meta AI Blog (ai.meta.com/blog)
- Mistral、xAI、Perplexity、Cohere 官方博客
- GitHub Copilot Changelog (github.blog/changelog/label/copilot)
- Vercel Changelog（AI SDK / AI Gateway）
- Cloudflare AI Changelog（Workers AI / AI Gateway）
- AWS AI News、Google Vertex AI Release Notes、Microsoft Foundry What's New
- Hugging Face Blog、NVIDIA Technical Blog

### A 级（深度分析与可信媒体）
- Stratechery (Ben Thompson)
- Platformer (Casey Newton)
- Import AI (Jack Clark)
- Latent Space (Swyx)
- 海外独角兽 / 拾象
- Reuters Technology
- TechCrunch AI 频道
- The Verge AI 频道
- Bloomberg Technology
- The Information
- Ars Technica
- Wired
- NYT Technology
- Semafor Tech
- 机器之心、量子位、硅星人、36Kr AI、InfoQ 中国 AI

### B 级（辅助，信号筛选）
- Hacker News 今日 Top 20（只挑 AI 相关）
- Ben's Bites、The Batch（Andrew Ng）

## 筛选标准（重要）

### 时间窗口（最高优先级，不得放宽）

- 当前生成时间：`{NOW.isoformat(timespec="minutes")}`
- 最早允许发布时间：`{WINDOW_START.isoformat(timespec="minutes")}`
- Top 3 的每一条都必须能从原文或搜索结果中确认，首次发布时间处于上述两个时间点之间
- 转载时间、页面更新日期、榜单收录日期不能代替事件首次发布时间
- 优先使用精确发布时间；若官方原文只提供 YYYY-MM-DD 日期，可以保留日期精度，严禁自行编造具体时刻
- 过去 48 小时若不足 3 条合格且未报道的新闻，Top 3 可以少于 3 条；严禁用更早的旧闻或重复事件凑数
- 若没有合格内容，Top 3 只输出两句面向普通读者的说明：`**今天暂时没有新的重点动态**`，以及 `过去 48 小时内，暂未发现来源可靠、值得关注且没有重复报道的新消息。我们会继续关注，有重要进展会及时更新。`；不要展示时间戳、筛选规则或技术性解释

只收录符合以下之一的内容：
1. **产品技术突破**：新模型发布、新 API、新功能上线、benchmark 刷新
2. **架构/工程深度**：推理优化、agent 框架、基础设施变化
3. **商业战略信号**：重要融资、收购、人才流动、合作签约
4. **行业观点**：有分析深度的评论文章（不是转述新闻）
5. **开发者生态更新**：官方 changelog 中影响功能、API、模型可用性、成本、性能或工作流的重要更新

**排除**：
- 纯营销稿、一句话新闻、股价波动、名人 Twitter 口水战
- 超过 48 小时的旧新闻
- 未经证实的传言
- 涉及中国敏感内容或明显地缘政治争议的内容，包括但不限于中美对抗叙事、涉台涉港涉疆、人权与制裁等
- 如果一条新闻的主叙事是中国敏感议题或地缘政治对抗，即使与 AI 相关也不要收录；一般性的政府部门、公共部门项目、政策讨论或海外政治人物表述可保留

## 输出格式

### 🎯 今日 Top 3
每条格式严格如下（必须用标准 markdown，不要用分号做分隔）：

**标题**：[中文标题](原文链接)
**来源**：[媒体名称](原文链接) · 发布日期（用 YYYY-MM-DD 格式，不要用"昨日""今日"等相对表达）
<!-- published_at: 原文首次发布时间；有精确时间时使用含时区的 ISO 8601，原文只有日期时只写 YYYY-MM-DD，严禁补造时刻；此行必须保留 -->
**摘要**：
- 发生了什么（一句话，**中文**）
- 为什么重要（一句话，**中文**）
- 对谁有影响（一句话，**中文**）
**产品技术视角**：一句话点出技术或产品层面的关键点（**中文**）

### 📰 其他值得看的（5-8 条）
简洁版，每条 2 行：
- **[标题](链接)** · 来源
- 一句话说清楚这是什么（**中文**）

### 🔍 今日主题观察（可选）
如果今天的新闻呈现某个趋势（例如"多家公司都在做 agent 框架"），给出 2-3 句话的观察。

### ⚠️ 信息来源说明
只需告诉我：
- 本次简报中，哪些源**直接提供了内容**（列出媒体名即可）
- 上述内容中的链接，是搜索结果中的真实 URL，还是根据主域名推测的（注明 `⚠️ 链接待确认`）

## 硬性要求
- 所有链接必须是**真实可点击的原文 URL**，绝对不要编造
- Top 3 必须使用带文章路径的原文 URL，主页、栏目页或仅有主域名的链接不得进入 Top 3
- Top 3 必须来自 3 个不同的发布方，同一发布方最多 1 条；不能用同一家公司的多个更新占满榜单。如果只有不足 3 个发布方有合格内容，宁可少于 3 条
- “其他值得看的”同一发布方最多 2 条，并尽量不要重复 Top 3 已出现的发布方；当天所有栏目都不能被单一公司或媒体主导
- 聚合站、新闻摘要页和搜索结果页只能用于发现线索，不能作为 Top 3 的唯一依据；Top 3 必须能回溯到公司官方公告或可信媒体的具体文章
- “今日 Top 3”“其他值得看的”“主题观察”“信息来源说明”都不得提及被排除的敏感内容、敏感事件名称、筛选命中原因或删除说明；排除后直接不写，不能用“未列示”方式变相展示
- “其他值得看的”如果搜索片段中只有主域名（如 `techcrunch.com`）而没有完整文章路径，可以用主域名作为链接占位，并注明 `⚠️ 链接待确认`
- 如果某条新闻既无法确认完整 URL、也找不到主域名，才宁可不收录
- 严禁输出任何涉及中国敏感内容或明显地缘政治对抗的条目、摘要、观察或来源说明；一般性的政府机构、公共部门、企业合规合作、海外政治人物或立法机构表述可保留
- **语言规则**：标题、摘要、分析、观察等所有内容一律用**中文**写，让读者看懂；公司名（Google/Meta/OpenAI）、产品名（Gemini/Claude/GPT）、通用技术术语（agent/LLM/RAG/fine-tuning 等）可保留英文
- 总长度控制在 1000 字以内（精炼）
- 不要在末尾输出字数统计或任何自我评估（如"总字数：XXX 字"）
- **绝对不要向用户提问或请求确认**：不得询问"是否要更精准的搜索"、"您希望我如何处理"等。直接执行，用搜索到的最佳信息生成完整简报
- **绝不允许扩大时间范围**：若合格新闻不足 3 条，就如实输出较少条目，并在“⚠️ 信息来源说明”中注明；不得采用 48 小时以前的内容"""


def build_user_prompt(previous_stories=None, official_candidates=None) -> str:
    """Build the prompt with a complete history-based deduplication block."""
    prompt = _USER_PROMPT_TEMPLATE
    if official_candidates:
        candidate_list = "\n".join(
            f"- {candidate['published_at']} | {candidate['source']} | "
            f"{candidate['title']} | {candidate['url']}"
            for candidate in official_candidates
        )
        candidate_block = (
            "\n\n## 可信订阅源已确认候选（必须优先选入 Top 3）\n\n"
            "以下候选由程序直接读取官方与可信媒体的 RSS/Atom，已通过 48 小时窗口、AI 相关性、"
            "历史 URL 去重和敏感内容初筛。请优先打开具体链接核查并从中选出最重要的条目；"
            "只要确有用户或开发者价值，不要因为它来自 changelog 就降级。"
            "当候选覆盖至少 3 个发布方时，Top 3 必须从不同发布方选满 3 条，禁止输出空榜；"
            "当候选为 1–2 条时必须全部收录，再用其他可信来源补充。"
            "使用候选时，published_at 注释必须逐字复制候选给出的完整时间（包括时区），"
            "不要自行删改为无时区时间。\n\n"
            f"{candidate_list}"
        )
        prompt = prompt.replace("## 信息源优先级", candidate_block + "\n\n## 信息源优先级")
    if previous_stories:
        story_list = "\n".join(
            f"- {story['date']} | {story['title']} | {story['url']}"
            for story in previous_stories
        )
        dedup_block = (
            "\n\n## 已报道内容（严格排除）\n\n"
            "以下是**全部历史简报中已经报道过的内容**。请严格排除这些 URL，"
            "也要排除同一事件的转载、跟进报道和换链接版本；只有确有独立新进展的事件才能收录：\n\n"
            f"{story_list}"
        )
        prompt = prompt.replace("## 输出格式", dedup_block + "\n\n## 输出格式")
    return prompt


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
        if candidate.get("url")
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


# ── Claude API ────────────────────────────────────────────────────────────────
def _api_create_with_retry(client, system: str, messages: list, max_retries: int = 3):
    """Call client.messages.create with exponential back-off for transient errors.

    Retries on connection errors, rate-limit errors, and 5xx server errors.
    Fails immediately on authentication errors (retrying won't help).
    """
    _retryable = (
        anthropic.APIConnectionError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
    )
    delays = [10, 30, 60]  # seconds between successive attempts

    for attempt in range(max_retries + 1):
        try:
            return client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=4000,
                system=system,
                tools=[{
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "allowed_callers": ["direct"],
                    "max_uses": 6,
                }],
                messages=messages,
            )
        except anthropic.AuthenticationError as e:
            print(f"❌ Authentication error — check ANTHROPIC_API_KEY: {e}", file=sys.stderr)
            raise
        except anthropic.BadRequestError as e:
            msg = str(e)
            if "usage limits" in msg or "regain access" in msg:
                print(f"❌ API usage limit reached — go to console.anthropic.com/settings/limits to increase your monthly spend limit. {e}", file=sys.stderr)
            else:
                print(f"❌ Bad request error: {e}", file=sys.stderr)
            raise
        except _retryable as e:
            if attempt >= max_retries:
                print(
                    f"❌ API error after {max_retries} retries: {type(e).__name__}: {e}",
                    file=sys.stderr,
                )
                raise
            wait = delays[attempt]
            print(f"  ⚠️ Transient API error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}: {e}")
            print(f"  ⏳ Retrying in {wait}s…")
            time.sleep(wait)


def fetch_briefing(user_prompt: str, previous_stories=None, official_candidates=None) -> str:
    """
    Call Claude with the web_search tool and return the briefing markdown.

    The web_search tool (type: "web_search_20260209") is server-side:
    Anthropic executes searches automatically and injects results back into
    the conversation. Claude may perform multiple searches before finishing,
    so we run an agentic loop until stop_reason == "end_turn".
    If the server-side loop hits its iteration limit it returns "pause_turn";
    we re-send the conversation to resume.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set")

    client = anthropic.Anthropic(api_key=api_key)

    # Wrap user prompt in a content block so we can attach cache_control.
    # On the first turn this primes the cache; subsequent turns (pause_turn loop)
    # read from cache at ~10% of normal input token cost.
    messages: list = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": user_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]

    # Cache the system prompt too (it's stable across all turns).
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

    print(f"📡 Calling Claude API for {TODAY_ISO}...")

    for turn in range(8):  # safety cap (was 15)
        response = _api_create_with_retry(client, system, messages)

        print(
            f"  turn {turn + 1} | stop_reason={response.stop_reason} | "
            f"blocks={[b.type for b in response.content]}"
        )

        # Collect any text already present in this response
        text = "\n".join(
            b.text for b in response.content if getattr(b, "type", "") == "text" and b.text
        )

        if response.stop_reason == "end_turn":
            cleaned = clean_briefing(text) or "（本次未生成内容，请检查 API 配置）"
            cleaned = format_empty_top_state(cleaned)
            if contains_sensitive_politics(cleaned):
                print("  ⚠️ Sensitive political content detected; requesting rewrite")
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上次输出包含不允许公开展示的话题，或提及了相关删除与筛选说明。"
                            "请完全重写整份简报，只保留产品、工程、商业落地、开发者生态相关内容，"
                            "不要解释哪些内容被排除，不要复述被删除的话题，也不要提及筛选命中原因。"
                            "下一条回复必须直接以 `### 🎯 今日 Top 3` 开始，并完整包含"
                            "`### 📰 其他值得看的` 和 `### ⚠️ 信息来源说明`；"
                            "不要回复确认、道歉或修改说明。"
                        ),
                    }
                )
                continue
            validation_errors = validate_briefing(
                cleaned,
                previous_stories,
                official_candidates=official_candidates,
            )
            if validation_errors:
                print("  ⚠️ Freshness/deduplication validation failed; requesting rewrite")
                for error in validation_errors:
                    print(f"    - {error}")
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上次输出未通过发布前硬校验，不能发布：\n- "
                            + "\n- ".join(validation_errors)
                            + "\n请重新核查并完整重写。只能保留发布时间处于指定 48 小时窗口内、"
                            "且未在历史清单出现过的独立事件。若不足 3 条就少输出，"
                            "不要用旧闻或重复事件补足。原文有精确时间就保留含时区时间，"
                              "原文只有日期就只写日期；严禁编造时刻。每条必须保留有效的 published_at 注释。"
                              "下一条回复必须直接以 `### 🎯 今日 Top 3` 开始，并完整包含"
                              "`### 📰 其他值得看的` 和 `### ⚠️ 信息来源说明`；"
                              "不要回复确认、道歉或修改说明。Top 3 同一发布方只能出现 1 条。"
                        ),
                    }
                )
                continue
            return cleaned

        if response.stop_reason == "pause_turn":
            # web_search_20260209 runs searches in a server-side loop (max 10
            # iterations). When it hits the limit it returns "pause_turn" with
            # partial content. Re-send the conversation to let it continue.
            messages.append({"role": "assistant", "content": response.content})
            continue

        # Never publish a truncated response because it bypasses validation.
        raise RuntimeError(f"Claude stopped before a valid briefing: {response.stop_reason}")

    raise RuntimeError("Claude did not produce a fresh, non-duplicate briefing within 8 turns")


# ── Markdown → HTML ───────────────────────────────────────────────────────────
def clean_briefing(text: str) -> str:
    """Strip LLM preamble and fix common markdown formatting issues."""
    # 0. Strip trailing whitespace on every line FIRST.
    #    Markdown treats "line  \n" (two trailing spaces) as a hard <br>.
    #    Claude often emits these unintentionally, causing cramped output.
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
    #     Claude outputs **来源**：（colon outside bold），so pattern must include \*\*.
    text = re.sub(
        r'(?m)(?<!\n)\n(\*\*(?:来源|摘要|产品技术视角)\*\*\s*[：:])',
        r'\n\n\1',
        text,
    )

    # 0c. Ensure a blank line between **摘要**： and the first bullet item.
    #     sane_lists requires a blank line before any list that follows text;
    #     without it, "- item" is treated as plain text inside the label's <p>.
    #     Claude outputs **摘要**：（colon outside bold），pattern corrected accordingly.
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
    # Claude outputs **来源**：→ HTML: <strong>来源</strong>：，pattern matches closing </strong>.
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
    # Claude sometimes splits a sentence mid-line; in HTML a bare \n followed
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
    <div class="footer-meta">由 Claude + Web Search 自动生成</div>
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
RSS_DESCRIPTION = "由 Claude + Web Search 自动生成的 AI 行业每日精选"
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

    # Seed the model with fresh, exact article links from official and trusted
    # media feeds. Web search still broadens coverage, but is no longer the only
    # way important stories reach the candidate pool.
    official_candidates = get_official_candidates(previous_stories)
    if official_candidates:
        print(f"📥 Loaded {len(official_candidates)} fresh trusted feed candidates")

    # Build prompt and generate briefing via Claude
    user_prompt = build_user_prompt(previous_stories, official_candidates)
    briefing_md = fetch_briefing(user_prompt, previous_stories, official_candidates)
    print(f"✅ Received {len(briefing_md)} chars from Claude")

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
