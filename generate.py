#!/usr/bin/env python3
"""
Daily AI News Briefing Generator
每日 AI 简报生成器

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python generate.py
"""

import anthropic
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import markdown as md_lib
except ImportError:
    print("❌ Missing 'markdown' package. Run: pip install markdown", file=sys.stderr)
    sys.exit(1)

# ── Date (China Standard Time UTC+8) ──────────────────────────────────────────
CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST)
TODAY_ISO = NOW.strftime("%Y-%m-%d")
TODAY_CN = NOW.strftime("%Y年%m月%d日")
WEEKDAYS = "一二三四五六日"
WEEKDAY_CN = f"周{WEEKDAYS[NOW.weekday()]}"

# ── Deduplication helpers ─────────────────────────────────────────────────────
# Domains whose links should NOT be treated as "already-covered" article URLs.
_SKIP_DOMAINS = {"hiwd.com", "daily.hiwd.com"}


def get_recent_article_urls(archive_dir: Path, days: int = 2) -> list[str]:
    """Return external article URLs found in the last `days` archived HTML files.

    These are passed to the prompt so Claude avoids re-reporting the same stories.
    """
    urls: set[str] = set()
    html_files = sorted(archive_dir.glob("????-??/????-??-??.html"), reverse=True)[:days]
    for html_file in html_files:
        text = html_file.read_text("utf-8")
        for url in re.findall(r'href="(https?://[^"]+)"', text):
            if not any(d in url for d in _SKIP_DOMAINS):
                # Normalise: strip query strings / tracking params and trailing slash
                urls.add(re.sub(r"\?.*$", "", url).rstrip("/"))
    return sorted(urls)


# ── Prompts ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "你是一位专注于 AI 产业的资深科技分析师，"
    "善于从海量信息中提炼关键信号，输出精准、有深度的每日简报。"
)

SENSITIVE_POLITICS_PATTERNS = [
    r"中美",
    r"地缘政治|制裁",
    r"涉台|台湾|香港|新疆",
]


def contains_sensitive_politics(text: str) -> bool:
    """Return True when the briefing touches disallowed CN/US political topics."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in SENSITIVE_POLITICS_PATTERNS)

_USER_PROMPT_TEMPLATE = f"""你是我的 AI 产品情报分析师。请帮我完成今天（{TODAY_CN} {WEEKDAY_CN}）的 AI 行业每日简报。

## 搜索策略（严格限制 3 次）

用宽泛关键词一次覆盖多个源，**总搜索次数不超过 3 次**：
- 搜索 1：`AI news {TODAY_ISO} site:openai.com OR site:anthropic.com OR site:deepmind.google OR site:techcrunch.com OR site:theverge.com`
- 搜索 2：`AI model release OR product launch OR LLM benchmark OR AI funding OR acquisition {TODAY_ISO}`
- 搜索 3（中文）：`AI 大模型 发布 {TODAY_CN}`

## 信息源优先级

### S 级（必须覆盖，一手源）
- OpenAI Blog (openai.com/blog)
- Anthropic News (anthropic.com/news)
- Google DeepMind Blog (deepmind.google/discover/blog)
- Meta AI Blog (ai.meta.com/blog)
- Mistral、xAI、Perplexity、Cohere 官方博客
- Stratechery (Ben Thompson)
- Platformer (Casey Newton)
- Import AI (Jack Clark)
- Latent Space (Swyx)
- 海外独角兽 / 拾象

### A 级（产品技术深度报道）
- TechCrunch AI 频道
- The Verge AI 频道
- Bloomberg Technology
- The Information（付费墙外可见部分）
- Ars Technica
- Wired
- NYT Technology
- Semafor Tech
- 机器之心、量子位、硅星人、36Kr AI、InfoQ 中国 AI

### B 级（辅助，信号筛选）
- Hacker News 今日 Top 20（只挑 AI 相关）
- Ben's Bites、The Batch（Andrew Ng）

## 筛选标准（重要）

只收录符合以下之一的内容：
1. **产品技术突破**：新模型发布、新 API、新功能上线、benchmark 刷新
2. **架构/工程深度**：推理优化、agent 框架、基础设施变化
3. **商业战略信号**：重要融资、收购、人才流动、合作签约
4. **行业观点**：有分析深度的评论文章（不是转述新闻）

**排除**：
- 纯营销稿、一句话新闻、股价波动、名人 Twitter 口水战
- 超过 24 小时的旧新闻
- 未经证实的传言
- 涉及中国敏感内容或明显地缘政治争议的内容，包括但不限于中美对抗叙事、涉台涉港涉疆、人权与制裁等
- 如果一条新闻的主叙事是中国敏感议题或地缘政治对抗，即使与 AI 相关也不要收录；一般性的政府部门、公共部门项目、政策讨论或海外政治人物表述可保留

## 输出格式

### 🎯 今日 Top 3
每条格式严格如下（必须用标准 markdown，不要用分号做分隔）：

**标题**：[中文标题](原文链接)
**来源**：[媒体名称](原文链接) · 发布日期（用 YYYY-MM-DD 格式，不要用"昨日""今日"等相对表达）
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
- 如果搜索片段中只有主域名（如 `techcrunch.com`）而没有完整文章路径，可以用主域名作为链接占位，并在"来源"字段后注明 `⚠️ 链接待确认`，不要因为缺少完整 URL 就丢弃重大新闻
- 如果某条新闻既无法确认完整 URL、也找不到主域名，才宁可不收录
- 严禁输出任何涉及中国敏感内容或明显地缘政治对抗的条目、摘要、观察或来源说明；一般性的政府机构、公共部门、企业合规合作、海外政治人物或立法机构表述可保留
- **语言规则**：标题、摘要、分析、观察等所有内容一律用**中文**写，让读者看懂；公司名（Google/Meta/OpenAI）、产品名（Gemini/Claude/GPT）、通用技术术语（agent/LLM/RAG/fine-tuning 等）可保留英文
- 总长度控制在 1000 字以内（精炼）
- 不要在末尾输出字数统计或任何自我评估（如"总字数：XXX 字"）"""


def build_user_prompt(recent_urls=None) -> str:
    """Build the user prompt, optionally injecting a deduplication block.

    `recent_urls` should be the list returned by `get_recent_article_urls()`.
    When provided, a "已报道内容" section is inserted before the output-format
    section so Claude skips stories already covered in recent briefings.
    """
    prompt = _USER_PROMPT_TEMPLATE
    if recent_urls:
        url_list = "\n".join(f"- {u}" for u in recent_urls)
        dedup_block = (
            "\n\n## 已报道内容（严格排除）\n\n"
            "以下链接来自**前 2 天**已发布的简报。"
            "请**严格排除**这些 URL 及同一事件的任何其他报道，不得重复收录：\n\n"
            f"{url_list}"
        )
        prompt = prompt.replace("## 输出格式", dedup_block + "\n\n## 输出格式")
    return prompt


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
                tools=[{"type": "web_search_20260209", "name": "web_search", "allowed_callers": ["direct"]}],
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


def fetch_briefing(user_prompt: str) -> str:
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
            if contains_sensitive_politics(cleaned):
                print("  ⚠️ Sensitive political content detected; requesting rewrite")
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上次输出触及禁止主题：中国敏感内容或地缘政治对抗叙事。"
                            "请完全重写整份简报，只保留产品、工程、商业落地、开发者生态相关内容，"
                            "一般性的政府机构、公共部门合作、海外政治人物或立法机构表述可以保留，但不要保留或改写任何涉及中美对抗、地缘政治、涉台涉港涉疆、制裁的条目。"
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

        # stop_reason == "max_tokens" or other — return whatever text we have
        return text or "（输出被截断，请增大 max_tokens）"

    return "（超出最大轮次，请检查配置）"


# ── Markdown → HTML ───────────────────────────────────────────────────────────
def clean_briefing(text: str) -> str:
    """Strip LLM preamble and fix common markdown formatting issues."""
    # 0. Strip trailing whitespace on every line FIRST.
    #    Markdown treats "line  \n" (two trailing spaces) as a hard <br>.
    #    Claude often emits these unintentionally, causing cramped output.
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)

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
  <link rel="icon" type="image/x-icon" href="/favicon.ico?v=2" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400&display=swap" rel="stylesheet">
  <style>
    /* ── Reset ── */
    *, *::before, *::after { box-sizing: border-box; }

    /* ── Base — identical to hiwd style.css ── */
    body {
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
      font-size: 17px;
      line-height: 1.82;
      background-color: #f5f5f7;
      color: #1d1d1f;
      /* Prevent iOS Safari from auto-scaling font sizes */
      -webkit-text-size-adjust: 100%;
      text-size-adjust: 100%;
      /* Break long URLs / mixed CJK+EN strings */
      overflow-wrap: break-word;
      word-break: break-word;
    }

    /* ── Logo — fixed top-left, links to hiwd.com ── */
    #logo {
      position: fixed;
      top: 15px;
      left: 15px;
      z-index: 9999;
      display: flex;
      align-items: center;
      gap: .38rem;
      text-decoration: none;
      outline: none;
      border: none;
      -webkit-tap-highlight-color: transparent;
      white-space: nowrap;
      user-select: none;
    }
    #logo-hiwd {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      font-size: 1.22rem;
      font-weight: 400;
      letter-spacing: -0.02em;
      color: #1d1d1f;
      line-height: 1;
    }
    #logo-i {
      position: relative;
      display: inline-block;
    }
    #logo-i::after {
      content: '';
      position: absolute;
      top: .06em;
      left: 50%;
      transform: translateX(-50%);
      width: .155em;
      height: .155em;
      border-radius: 50%;
      background: #00C2B3;
    }
    #logo-rule {
      width: 1px;
      height: .9em;
      background: rgba(29,29,31,.18);
      flex-shrink: 0;
      align-self: center;
    }
    #logo-sub {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      font-size: 1.22rem;
      font-weight: 400;
      letter-spacing: -0.02em;
      color: #1d1d1f;
    }

    /* ── Content card — same as hiwd #content ── */
    #content {
      margin-top: 84px;
      margin-left: auto;
      margin-right: auto;
      width: min(90%, 840px);
      padding: 40px 42px 34px;
      background-color: rgba(255, 255, 255, 0.94);
      border: 1px solid rgba(15, 23, 42, 0.05);
      box-shadow: 0 14px 40px rgba(15, 23, 42, 0.06);
      border-radius: 24px;
      margin-bottom: 26px;
      backdrop-filter: saturate(180%) blur(18px);
    }

    /* ── Headings — exact hiwd style ── */
    h1, h2, h3 {
      position: relative;
      padding-left: 15px;
      font-weight: bold;
      line-height: 1.2;
      color: #333;
    }
    /* h1: 4px solid teal — same as hiwd */
    h1::before {
      content: "";
      position: absolute;
      left: 0; top: 50%;
      transform: translateY(-50%);
      width: 4px; height: 26px;
      background-color: #00C2B3;
      border-radius: 2px;
    }
    /* h2: 2px semi-transparent teal — same as hiwd */
    h2::before {
      content: "";
      position: absolute;
      left: 0; top: 50%;
      transform: translateY(-50%);
      width: 2px; height: 18px;
      background-color: rgba(0, 194, 179, 0.35);
      border-radius: 1px;
    }
    /* h3: same weight as h2 bar — briefing section headers */
    h3::before {
      content: "";
      position: absolute;
      left: 0; top: 50%;
      transform: translateY(-50%);
      width: 2px; height: 14px;
      background-color: rgba(0, 194, 179, 0.35);
      border-radius: 1px;
    }

    h1 { font-size: 32px; margin-top: 0; }
    h2 { font-size: 24px; margin-top: 35px; }
    h3 { font-size: 19px; margin-top: 28px; margin-bottom: 6px; }

    /* ── h4/h5/h6 — for AI-generated content ── */
    h4, h5, h6 {
      position: relative;
      padding-left: 15px;
      font-weight: 600;
      line-height: 1.4;
      color: #444;
      margin-top: 20px;
      margin-bottom: 6px;
    }
    h4 { font-size: 16px; }
    h5, h6 { font-size: 15px; }

    /* ── Body text & links — identical to hiwd ── */
    p {
      line-height: 1.7;
      margin-top: 10px;
      margin-bottom: 16px;
      color: #333;
    }
    a { color: #008F84; text-decoration: none; transition: opacity 0.2s; }
    a:hover { opacity: 0.7; }
    strong { font-weight: 700; color: #1a1a1a; }
    em { font-style: italic; color: #555; }

    /* ── Lists ── */
    ul, ol { padding-left: 1.5em; margin: 8px 0 16px; }
    li { line-height: 1.7; color: #333; margin: 4px 0; }

    /* ── Inline code ── */
    code {
      font-family: "SF Mono", "Fira Code", monospace;
      font-size: .85em;
      background: #f3f4f6;
      padding: .1em .35em;
      border-radius: 4px;
      color: #1a1a1a;
    }

    /* ── Blockquote ── */
    blockquote {
      border-left: 3px solid rgba(0, 194, 179, 0.5);
      margin: 16px 0;
      padding: 8px 16px;
      background: #f9fefe;
      border-radius: 0 8px 8px 0;
      color: #555;
    }

    /* ── HR ── */
    hr { border: none; border-top: 1px solid #eee; margin: 28px 0; }

    /* ── Header compact block ── */
    .hero {
      margin-bottom: 12px;
    }

    .hero-meta {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0 4px;
      padding-left: 15px;
      font-size: 17px;
      line-height: 1.72;
      color: #6e6e73;
    }

    .hero-meta a {
      color: #008F84;
      text-decoration: none;
    }

    .hero-divider {
      margin: 0 2px;
      color: #8e8e93;
    }

    .hero-date {
      margin-left: 10px;
      color: #6e6e73;
      white-space: nowrap;
    }

    .hero h1 {
      margin-top: 20px;
      margin-bottom: 0;
    }

    .theme-observation {
      margin: 8px 0 2px;
      padding: 14px 16px 14px 18px;
      border: 1px solid rgba(0, 194, 179, 0.12);
      border-left: 3px solid rgba(0, 194, 179, 0.28);
      border-radius: 10px;
      background: linear-gradient(180deg, #fafdfd 0%, #f7fbfb 100%);
    }

    .theme-intro {
      margin-top: 0;
      margin-bottom: 10px;
      color: #555;
    }

    .theme-list {
      margin: 0;
      padding-left: 1.15em;
    }

    .theme-list li {
      margin: 8px 0;
    }

    /* ── Archive section ── */
    .archive-section { margin-top: 40px; }

    /* ── Month-grouped collapsible archive nav ── */
    .archive-nav { margin: 0; }

    details.month-item,
    details.older-archive {
      border-bottom: 1px solid #eee;
    }
    details.month-item:last-child,
    details.older-archive:last-child { border-bottom: none; }

    details.month-item > summary,
    details.older-archive > summary {
      list-style: none;
      display: flex;
      align-items: center;
      padding: 11px 5px;
      font-size: 15px;
      color: #555;
      cursor: pointer;
      user-select: none;
      transition: background-color 0.15s;
    }
    details.month-item > summary::-webkit-details-marker,
    details.older-archive > summary::-webkit-details-marker { display: none; }

    details.month-item > summary::before {
      content: "▶";
      font-size: 10px;
      margin-right: 8px;
      color: #bbb;
      transition: transform 0.2s;
      flex-shrink: 0;
    }
    details.month-item[open] > summary::before { transform: rotate(90deg); }

    details.older-archive > summary {
      color: #008F84;
      font-size: 14px;
    }
    details.older-archive > summary::before {
      content: "▶";
      font-size: 10px;
      margin-right: 8px;
      color: #00c2b3;
      transition: transform 0.2s;
      flex-shrink: 0;
    }
    details.older-archive[open] > summary::before { transform: rotate(90deg); }

    details.month-item > summary:hover,
    details.older-archive > summary:hover { background-color: #fafafa; }

    .month-count {
      font-size: 12px;
      color: #bbb;
      margin-left: 6px;
    }

    .day-list { list-style: none; padding: 0; margin: 0; }
    .day-list li { border-top: 1px solid #f5f5f5; }
    .day-list li a {
      display: block;
      padding: 9px 5px 9px 22px;
      font-size: 14px;
      color: #555;
      text-decoration: none;
      transition: padding-left 0.2s ease, background-color 0.2s ease;
      font-variant-numeric: tabular-nums;
    }
    .day-list li a:hover {
      background-color: #fafafa;
      padding-left: 28px;
    }
    .day-list li.active a { color: #008F84; font-weight: 600; }
    .older-months { padding-bottom: 4px; }

    .today-tag {
      font-size: 11px;
      background: rgba(0,194,179,.12);
      color: #00a396;
      padding: 1px 6px;
      border-radius: 4px;
      margin-left: 6px;
      vertical-align: middle;
    }
    .no-archive { font-size: 14px; color: #999; }

    /* ── Footer — same as hiwd footer.html ── */
    #footer {
      width: min(90%, 840px);
      margin: 0 auto 28px;
      padding: 0 8px 8px;
      text-align: center;
      font-size: 13px;
      line-height: 1.65;
      color: #8e8e93;
    }
    #footer a { color: #008F84; }

    .footer-meta {
      margin-bottom: 4px;
    }

    /* ── Responsive — mirrors hiwd breakpoints ── */
    @media (max-width: 1150px) {
      #logo { position: absolute !important; top: 15px; left: 15px; }
      #content { margin-top: 110px; padding: 34px 28px 28px; }
    }
    @media (max-width: 767px) {
      #content { margin-top: 108px; width: calc(100% - 32px); padding: 28px 18px 24px; border-radius: 20px; }
      .hero { margin-bottom: 14px; }
      .hero-meta {
        padding-left: 12px;
        font-size: 17px;
      }
      h1 { font-size: 28px; }
      h2 { font-size: 24px; }
      h3 { font-size: 18px; margin-top: 20px; }
      p, li { line-height: 1.82; }
    }
  </style>
</head>
<body>

  <!-- Logo links back to hiwd main site -->
  <a href="https://hiwd.com/" id="logo" aria-label="返回 hiwd 主站">
    <span id="logo-hiwd">h<span id="logo-i">ı</span>wd</span>
    <span id="logo-rule"></span>
    <span id="logo-sub">daily</span>
  </a>

  <div id="content">

    <div class="hero">
      <div class="hero-meta"><a href="https://hiwd.com/">hiwd</a><span class="hero-divider">｜</span><a href="/">daily</a><span class="hero-date">[[DATE_CN]] [[WEEKDAY]]</span></div>
      <h1>AI 行业每日简报</h1>
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
    <div>© 2026 hiwd · All rights reserved.</div>
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


def render_page(briefing_md: str, archive_entries: list[dict], page_title: str | None = None) -> str:
    content_html = md_to_html(briefing_md)
    archive_html = build_archive_nav(archive_entries)
    generated_at = NOW.strftime("%Y-%m-%d %H:%M CST")
    if page_title is None:
        page_title = f"AI 行业每日简报 · {TODAY_CN}"

    return (
        HTML_TEMPLATE
        .replace("[[PAGE_TITLE]]", page_title)
        .replace("[[DATE_CN]]", TODAY_CN)
        .replace("[[DATE_ISO]]", TODAY_ISO)
        .replace("[[WEEKDAY]]", WEEKDAY_CN)
        .replace("[[CONTENT]]", content_html)
        .replace("[[ARCHIVE]]", archive_html)
        .replace("[[GENERATED_AT]]", generated_at)
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
    docs = Path("docs")
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
        except json.JSONDecodeError:
            archive_entries = []

    # Collect recently-covered article URLs for deduplication (before generation)
    recent_urls = get_recent_article_urls(archive_dir)
    if recent_urls:
        print(f"🔍 Loaded {len(recent_urls)} recent URLs for deduplication")

    # Build prompt and generate briefing via Claude
    user_prompt = build_user_prompt(recent_urls)
    briefing_md = fetch_briefing(user_prompt)
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


if __name__ == "__main__":
    main()
