import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import generate


def briefing(*entries: tuple[str, str, datetime]) -> str:
    blocks = []
    for title, url, published_at in entries:
        blocks.append(
            f"""**标题**：[{title}]({url})
**来源**：[测试源]({url}) · {published_at:%Y-%m-%d}
<!-- published_at: {published_at.isoformat()} -->
**摘要**：
- 发生了什么
- 为什么重要
- 对谁有影响
**产品技术视角**：测试
"""
        )
    return "### 🎯 今日 Top 3\n\n" + "\n".join(blocks) + "\n### 📰 其他值得看的"


def briefing_with_source_date(title: str, url: str, published_at: datetime, source_date: str) -> str:
    return f"""### 🎯 今日 Top 3

**标题**：[{title}]({url})
**来源**：[测试源]({url}) · {source_date}
<!-- published_at: {published_at.isoformat()} -->
**摘要**：测试
**产品技术视角**：测试

### 📰 其他值得看的
"""


class FreshnessValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 13, 9, 0, tzinfo=timezone(timedelta(hours=8)))

    def test_accepts_story_inside_48_hour_window(self):
        text = briefing(("新模型发布", "https://example.com/new?utm_source=x", self.now - timedelta(hours=47)))
        self.assertEqual(generate.validate_briefing(text, now=self.now), [])

    def test_accepts_date_only_official_publication(self):
        published = self.now - timedelta(days=1)
        text = briefing(("官方更新日志发布新功能", "https://example.com/changelog/new", published))
        text = text.replace(published.isoformat(), published.strftime("%Y-%m-%d"))
        self.assertEqual(generate.validate_briefing(text, now=self.now), [])

    def test_rejects_story_older_than_48_hours(self):
        text = briefing(("旧模型发布", "https://example.com/old", self.now - timedelta(hours=49)))
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("不在" in error for error in errors))

    def test_rejects_old_date_only_publication(self):
        published = self.now - timedelta(days=3)
        text = briefing(("过期的官方更新", "https://example.com/changelog/old", published))
        text = text.replace(published.isoformat(), published.strftime("%Y-%m-%d"))
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("发布日期" in error and "超出" in error for error in errors))

    def test_rejects_old_visible_date_even_with_fresh_hidden_timestamp(self):
        text = briefing_with_source_date(
            "伪装成新内容的旧文章",
            "https://example.com/article",
            self.now - timedelta(hours=2),
            "2026-07-09",
        )
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("展示的来源日期" in error for error in errors))
        self.assertTrue(any("不一致" in error for error in errors))

    def test_rejects_homepage_as_top_story_url(self):
        text = briefing(("无法核验的主页新闻", "https://example.com/blog", self.now - timedelta(hours=2)))
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("缺少可核验的文章 URL" in error for error in errors))

    def test_rejects_historical_url_after_canonicalization(self):
        text = briefing(("同一事件的新标题", "https://example.com/story?utm_source=x", self.now - timedelta(hours=2)))
        history = [{"date": "2026-07-12", "title": "历史标题", "url": "https://example.com/story"}]
        errors = generate.validate_briefing(text, history, now=self.now)
        self.assertTrue(any("URL 已在历史" in error for error in errors))

    def test_rejects_highly_similar_historical_title_on_different_url(self):
        text = briefing(("OpenAI 正式发布 GPT-6 新模型", "https://new.example/gpt-6", self.now - timedelta(hours=2)))
        history = [{
            "date": "2026-07-12",
            "title": "OpenAI 正式发布 GPT-6 新模型！",
            "url": "https://old.example/gpt-6",
        }]
        errors = generate.validate_briefing(text, history, now=self.now)
        self.assertTrue(any("疑似重复历史事件" in error for error in errors))

    def test_allows_explicit_empty_window_instead_of_old_filler(self):
        text = "### 🎯 今日 Top 3\n\n过去 48 小时暂无符合条件且未报道的内容。\n\n### 📰 其他值得看的"
        self.assertEqual(generate.validate_briefing(text, now=self.now), [])

    def test_empty_state_is_rewritten_for_readers(self):
        text = """### 🎯 今日 Top 3

**无符合条件的新发布**

基于严格的时间窗口和内容筛选，过去 48 小时（2026-07-12 09:00 至 2026-07-14 09:00）内，未发现满足规则的内容。

### 📰 其他值得看的
"""
        formatted = generate.format_empty_top_state(text)
        self.assertIn("今天暂时没有新的重点动态", formatted)
        self.assertIn("有重要进展会及时更新", formatted)
        self.assertNotIn("2026-07-12", formatted)
        self.assertNotIn("严格的时间窗口", formatted)

    def test_non_empty_top_stories_are_not_rewritten(self):
        text = briefing(("真实的新发布", "https://example.com/article", self.now - timedelta(hours=2)))
        self.assertEqual(generate.format_empty_top_state(text), text)


class HistoryTests(unittest.TestCase):
    def test_reads_top_stories_from_all_archive_days(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir)
            for date, title in (("2026-01-01", "第一条"), ("2026-06-30", "第二条")):
                month = archive / date[:7]
                month.mkdir(parents=True, exist_ok=True)
                (month / f"{date}.html").write_text(
                    f'<h2>🎯 今日 Top 3</h2><p><strong>标题</strong>：'
                    f'<a href="https://example.com/{date}">{title}</a></p>'
                    '<h2>📰 其他值得看的</h2>',
                    encoding="utf-8",
                )
            stories = generate.get_previous_stories(archive)
            self.assertEqual({story["title"] for story in stories}, {"第一条", "第二条"})

    def test_prompt_contains_complete_history_and_no_24_hour_fallback(self):
        prompt = generate.build_user_prompt([
            {"date": "2026-01-01", "title": "已经报道", "url": "https://example.com/old"}
        ])
        self.assertIn("全部历史简报中已经报道过的内容", prompt)
        self.assertIn("已经报道", prompt)
        self.assertIn("过去 48 小时", prompt)
        self.assertNotIn("最近 48 小时内最重要", prompt)

    def test_prompt_uses_broader_official_source_searches(self):
        prompt = generate.build_user_prompt()
        self.assertIn("严格限制 6 次", prompt)
        self.assertIn(f"after:{generate.SEARCH_AFTER_ISO}", prompt)
        self.assertIn(f"before:{generate.SEARCH_BEFORE_ISO}", prompt)
        self.assertIn("GitHub Copilot Changelog", prompt)
        self.assertIn("Cloudflare AI Changelog", prompt)
        self.assertIn("Google Vertex AI Release Notes", prompt)
        self.assertIn("聚合站、新闻摘要页和搜索结果页只能用于发现线索", prompt)


class SensitiveContentTests(unittest.TestCase):
    def test_rejects_sensitive_exclusion_explanation(self):
        text = "本周涉及中国敏感监管内容的条目符合排除规则，因此未列示。"
        self.assertTrue(generate.contains_sensitive_politics(text))

    def test_allows_non_political_china_product_news(self):
        text = "一家中国公司发布了新的多模态模型和开发者 API。"
        self.assertFalse(generate.contains_sensitive_politics(text))


if __name__ == "__main__":
    unittest.main()
