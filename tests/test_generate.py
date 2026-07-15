import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import generate


def briefing(*entries: tuple[str, str, datetime], other: str = "") -> str:
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
    return "### 🎯 今日 Top 3\n\n" + "\n".join(blocks) + "\n### 📰 其他值得看的\n" + other


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

    def test_rejects_empty_top_when_official_candidates_exist(self):
        text = "### 🎯 今日 Top 3\n\n过去 48 小时暂无符合条件且未报道的内容。\n"
        candidates = [{"title": "官方更新", "url": "https://example.com/new"}]
        errors = generate.validate_briefing(
            text,
            now=self.now,
            official_candidates=candidates,
        )
        self.assertTrue(any("不得为空" in error for error in errors))

    def test_requires_three_stories_when_three_official_candidates_exist(self):
        text = briefing(("只有一条", "https://example.com/one", self.now - timedelta(hours=2)))
        candidates = [
            {"title": f"候选 {index}", "url": f"https://source{index}.example/{index}"}
            for index in range(3)
        ]
        errors = generate.validate_briefing(
            text,
            now=self.now,
            official_candidates=candidates,
        )
        self.assertTrue(any("至少需要 3 条" in error for error in errors))

    def test_rejects_same_publisher_filling_top_three(self):
        text = briefing(*[
            (f"GitHub 更新 {index}", f"https://github.blog/changelog/2026-07-1{index}-item", self.now - timedelta(hours=index))
            for index in range(1, 4)
        ])
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("同一发布方最多 1 条" in error for error in errors))

    def test_accepts_three_distinct_top_publishers(self):
        published = self.now - timedelta(hours=2)
        text = briefing(
            ("GitHub 更新", "https://github.blog/changelog/2026-07-12-github", published),
            ("Vercel 更新", "https://vercel.com/changelog/vercel-ai-update", published),
            ("媒体报道", "https://techcrunch.com/2026/07/12/ai-update/", published),
        )
        self.assertEqual(generate.validate_briefing(text, now=self.now), [])

    def test_rejects_one_publisher_dominating_other_reads(self):
        published = self.now - timedelta(hours=2)
        other = "\n".join(
            f"- **[扩展阅读 {index}](https://github.blog/changelog/2026-07-12-other-{index})** · GitHub\n- 简介"
            for index in range(3)
        )
        text = briefing(("Vercel 更新", "https://vercel.com/changelog/update", published), other=other)
        errors = generate.validate_briefing(text, now=self.now)
        self.assertTrue(any("其他值得看的" in error and "最多 2 条" in error for error in errors))

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

    def test_prompt_prioritizes_prefetched_official_candidates(self):
        candidates = [{
            "source": "GitHub Changelog",
            "title": "Security reviews now available in the GitHub Copilot app",
            "url": "https://github.blog/changelog/2026-07-14-security-reviews",
            "published_at": "2026-07-14T12:54:12+00:00",
        }]
        prompt = generate.build_user_prompt(official_candidates=candidates)
        self.assertIn("可信订阅源已确认候选", prompt)
        self.assertIn(candidates[0]["title"], prompt)
        self.assertIn(candidates[0]["url"], prompt)


class OfficialFeedTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 15, 9, 0, tzinfo=timezone(timedelta(hours=8)))

    def test_extracts_fresh_ai_entries_with_exact_links(self):
        feed = b"""<rss><channel>
          <item><title>Security reviews now available in GitHub Copilot</title>
            <link>https://github.blog/changelog/2026-07-14-security-reviews</link>
            <pubDate>Tue, 14 Jul 2026 12:54:12 +0000</pubDate></item>
          <item><title>Unrelated storage update</title>
            <link>https://example.com/2026-07-14-storage</link>
            <pubDate>Tue, 14 Jul 2026 12:00:00 +0000</pubDate></item>
        </channel></rss>"""
        candidates = generate.parse_official_feed(feed, "GitHub Changelog", self.now)
        self.assertEqual(len(candidates), 1)
        self.assertIn("security-reviews", candidates[0]["url"])

    def test_rejects_old_article_with_recently_updated_feed_date(self):
        feed = b"""<rss><channel><item><title>Old AI model page updated</title>
          <link>https://example.com/changelog/2026-06-10-old-ai-page</link>
          <pubDate>Tue, 14 Jul 2026 12:00:00 +0000</pubDate>
        </item></channel></rss>"""
        self.assertEqual(generate.parse_official_feed(feed, "Test", self.now), [])

    def test_extracts_atom_entries_and_iso_dates(self):
        feed = b"""<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>AI Gateway adds model leaderboard</title>
            <link rel="alternate" href="https://vercel.com/changelog/ai-gateway-leaderboard" />
            <published>2026-07-14T12:00:00Z</published></entry>
        </feed>"""
        candidates = generate.parse_official_feed(feed, "Vercel Changelog", self.now)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://vercel.com/changelog/ai-gateway-leaderboard")


class SensitiveContentTests(unittest.TestCase):
    def test_rejects_sensitive_exclusion_explanation(self):
        text = "本周涉及中国敏感监管内容的条目符合排除规则，因此未列示。"
        self.assertTrue(generate.contains_sensitive_politics(text))

    def test_allows_non_political_china_product_news(self):
        text = "一家中国公司发布了新的多模态模型和开发者 API。"
        self.assertFalse(generate.contains_sensitive_politics(text))

    def test_rejects_china_financing_and_valuation_narrative(self):
        text = "中国一级市场的 AI 融资与估值快速上升，形成全球竞争格局。"
        self.assertTrue(generate.contains_sensitive_politics(text))


if __name__ == "__main__":
    unittest.main()
