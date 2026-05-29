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

# ── Prompts ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "你是一位专注于 AI 产业的资深科技分析师，"
    "善于从海量信息中提炼关键信号，输出精准、有深度的每日简报。"
)

USER_PROMPT = f"""你是我的 AI 产品情报分析师。请帮我完成今天（{TODAY_CN} {WEEKDAY_CN}）的 AI 行业每日简报。

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
- 超过 48 小时的旧新闻
- 未经证实的传言

## 输出格式

### 🎯 今日 Top 3（最重要，必看）
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
明确告诉我：
- 哪些源你成功检索了
- 哪些源你没能访问（网络/付费墙等原因）
- 所有链接都是你真实访问过的，还是可能存在幻觉

## 硬性要求
- 所有链接必须是**真实可点击的原文 URL**，绝对不要编造
- 如果某条新闻无法确认原文链接，宁可不收录
- **语言规则**：标题、摘要、分析、观察等所有内容一律用**中文**写，让读者看懂；公司名（Google/Meta/OpenAI）、产品名（Gemini/Claude/GPT）、通用技术术语（agent/LLM/RAG/fine-tuning 等）可保留英文
- 总长度控制在 1000 字以内（精炼）
- 不要在末尾输出字数统计或任何自我评估（如"总字数：XXX 字"）"""


# ── Claude API ────────────────────────────────────────────────────────────────
def fetch_briefing() -> str:
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

    messages: list = [{"role": "user", "content": USER_PROMPT}]

    # System prompt (plain string; caching handled at request level below)
    system = SYSTEM_PROMPT

    print(f"📡 Calling Claude API for {TODAY_ISO}...")

    for turn in range(8):  # safety cap (was 15)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4000,  # briefing ~800–1200 tokens; 4000 gives safe headroom
            system=system,
            tools=[{"type": "web_search_20260209", "name": "web_search", "allowed_callers": ["direct"]}],
            messages=messages,
        )

        print(
            f"  turn {turn + 1} | stop_reason={response.stop_reason} | "
            f"blocks={[b.type for b in response.content]}"
        )

        # Collect any text already present in this response
        text = "\n".join(
            b.text for b in response.content if getattr(b, "type", "") == "text" and b.text
        )

        if response.stop_reason == "end_turn":
            return clean_briefing(text) or "（本次未生成内容，请检查 API 配置）"

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
    return html


def build_archive_nav(entries: list[dict]) -> str:
    """
    Build archive list HTML. Uses root-relative paths (/archive/DATE.html)
    so it works correctly from both docs/index.html and docs/archive/*.html.
    "今日" label and active state are applied dynamically by JS so they
    remain correct no matter when the static page is viewed.
    """
    if not entries:
        return '<p class="no-archive">暂无历史记录</p>'
    items = []
    for entry in sorted(entries, key=lambda e: e["date"], reverse=True)[:90]:
        date = entry["date"]
        items.append(f'<li data-date="{date}"><a href="/archive/{date}.html">{date}</a></li>')
    return '<ul class="archive-list" id="archive-list">' + "\n".join(items) + "</ul>"


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
  <title>AI 行业每日简报 · [[DATE_CN]]</title>
  <meta name="description" content="AI 行业每日精选 [[DATE_CN]] [[WEEKDAY]]" />
  <link rel="icon" type="image/x-icon" href="/favicon.ico?v=2" />
  <style>
    /* ── Reset ── */
    *, *::before, *::after { box-sizing: border-box; }

    /* ── Base — identical to hiwd style.css ── */
    body {
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
      font-size: 16px;
      background-color: #f5f5f5;
      color: #1a1a1a;
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
      display: block;
      width: 100px;
      height: 100px;
      background-image: url('https://hiwd.com/img/logo.png');
      background-size: cover;
      background-repeat: no-repeat;
      outline: none;
      border: none;
      -webkit-tap-highlight-color: transparent;
    }

    /* ── Content card — same as hiwd #content ── */
    #content {
      margin-top: 80px;
      margin-left: auto;
      margin-right: auto;
      width: 90%;
      max-width: 800px;
      padding: 30px;
      background-color: #fff;
      box-shadow: 0px 2px 4px rgba(0, 0, 0, 0.1);
      border-radius: 12px;
      margin-bottom: 30px;
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

    /* ── Breadcrumb ── */
    .breadcrumb {
      font-size: 13px;
      color: #999;
      padding-left: 15px;
      margin-bottom: 20px;
      display: block;
    }
    .breadcrumb a { color: #008F84; }

    /* ── Date subtitle ── */
    .date-sub {
      font-size: 14px;
      color: #999;
      padding-left: 15px;
      margin-top: -6px;
      margin-bottom: 22px;
    }

    /* ── Generation badge ── */
    .gen-badge {
      display: inline-block;
      font-size: 12px;
      color: #aaa;
      background: #f5f5f5;
      border: 1px solid #eee;
      padding: 3px 12px;
      border-radius: 99px;
      margin-bottom: 28px;
    }

    /* ── Archive section ── */
    .archive-section { margin-top: 40px; }

    .archive-list { list-style: none; padding: 0; margin: 0; }
    .archive-list li { border-bottom: 1px solid #eee; }
    .archive-list li:last-child { border-bottom: none; }
    .archive-list li a {
      display: block;
      padding: 11px 5px;
      font-size: 15px;
      color: #555;
      text-decoration: none;
      transition: padding-left 0.2s ease, background-color 0.2s ease, opacity 0.2s;
      font-variant-numeric: tabular-nums;
    }
    .archive-list li a:hover {
      background-color: #fafafa;
      padding-left: 10px;
      opacity: 1;
    }
    .archive-list li.active a { color: #008F84; font-weight: 600; }
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
      text-align: center;
      margin-top: 10px;
      padding: 20px;
      font-size: 12px;
      color: #999;
    }
    #footer a { color: #008F84; }

    /* ── Responsive — mirrors hiwd breakpoints ── */
    @media (max-width: 1150px) {
      #logo { position: absolute !important; top: 15px; left: 15px; width: 80px; height: 80px; }
      #content { margin-top: 110px; padding: 30px 25px; }
    }
    @media (max-width: 767px) {
      #content { margin-top: 110px; padding: 25px 18px; }
      h1 { font-size: 26px; }
      h2 { font-size: 22px; }
      h3 { font-size: 18px; }
    }
  </style>
</head>
<body>

  <!-- Logo links back to hiwd main site -->
  <a href="https://hiwd.com/" id="logo" aria-label="返回 hiwd 主站"></a>

  <div id="content">

    <!-- Breadcrumb -->
    <span class="breadcrumb">
      <a href="https://hiwd.com/">hiwd</a> / AI 行业每日简报
    </span>

    <h1>AI 行业每日简报</h1>
    <p class="date-sub">[[DATE_CN]] [[WEEKDAY]]</p>

    <span class="gen-badge">由 Claude + Web Search 自动生成于 [[GENERATED_AT]]</span>

    <!-- Briefing body -->
    [[CONTENT]]

    <!-- Archive -->
    <div class="archive-section">
      <h2>历史存档</h2>
      [[ARCHIVE]]
    </div>

  </div>

  <div id="footer">
    © 2026 hiwd · All rights reserved.
  </div>
  <script>
    // Dynamically mark today's entry in the archive list.
    // Using browser time shifted to CST (UTC+8) so it matches the generation timezone.
    (function () {
      const cstNow = new Date(Date.now() + 8 * 3600 * 1000);
      const today = cstNow.toISOString().slice(0, 10);
      const list = document.getElementById('archive-list');
      if (!list) return;
      const li = list.querySelector('[data-date="' + today + '"]');
      if (!li) return;
      li.classList.add('active');
      const a = li.querySelector('a');
      if (a) {
        a.href = '/';
        a.innerHTML = today + ' <span class="today-tag">今日</span>';
      }
    })();
  </script>

</body>
</html>
"""


def render_page(briefing_md: str, archive_entries: list[dict]) -> str:
    content_html = md_to_html(briefing_md)
    archive_html = build_archive_nav(archive_entries)
    generated_at = NOW.strftime("%Y-%m-%d %H:%M CST")

    return (
        HTML_TEMPLATE
        .replace("[[DATE_CN]]", TODAY_CN)
        .replace("[[DATE_ISO]]", TODAY_ISO)
        .replace("[[WEEKDAY]]", WEEKDAY_CN)
        .replace("[[CONTENT]]", content_html)
        .replace("[[ARCHIVE]]", archive_html)
        .replace("[[GENERATED_AT]]", generated_at)
    )


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    docs = Path("docs")
    archive_dir = docs / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

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

    # Generate briefing via Claude
    briefing_md = fetch_briefing()
    print(f"✅ Received {len(briefing_md)} chars from Claude")

    # Add today to archive entries BEFORE rendering so it appears in the nav
    # and the JS "今日" highlight can find the entry.
    archive_updated = not any(e["date"] == TODAY_ISO for e in archive_entries)
    if archive_updated:
        archive_entries.append({"date": TODAY_ISO})
        archive_entries.sort(key=lambda e: e["date"])

    # Render HTML
    page_html = render_page(briefing_md, archive_entries)

    # Save archive copy
    archive_file = archive_dir / f"{TODAY_ISO}.html"
    archive_file.write_text(page_html, encoding="utf-8")
    print(f"✅ Saved  → docs/archive/{TODAY_ISO}.html")

    # Update index (latest briefing)
    (docs / "index.html").write_text(page_html, encoding="utf-8")
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
